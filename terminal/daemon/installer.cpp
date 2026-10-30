#include "installer.h"

#include <errno.h>
#include <fcntl.h>
#include <poll.h>
#include <signal.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/statvfs.h>
#include <sys/utsname.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

#include <cmath>
#include <dirent.h>
#include <grp.h>

#include "container.h"

namespace gbos {

namespace {

constexpr const char* kAppProcess = "/system/bin/app_process64";
constexpr const char* kSha256sum = "/system/bin/sha256sum";
constexpr const char* kTar = "/system/bin/tar";
// Download + unpacked base + the packages setup.sh installs, with headroom.
constexpr uint64_t kMinFreeBytes = 1536ull << 20;

// Overall progress windows of the phases (percent).
constexpr double kResolveEnd = 2, kDownloadEnd = 40, kVerifyEnd = 42, kExtractEnd = 55,
                 kConfigureEnd = 99;

struct Reporter {
    Channel& ch;
    std::string phase;
    double last = -1;
    void progress(const std::string& ph, double percent, json extra = json::object()) {
        percent = std::clamp(percent, 0.0, 100.0);
        if (ph == phase && std::fabs(percent - last) < 0.2 && extra.empty()) return;
        phase = ph;
        last = percent;
        extra["event"] = "progress";
        extra["phase"] = ph;
        extra["percent"] = std::round(percent * 10) / 10;
        ch.send(extra);
    }
    void log(const std::string& line) {
        if (!line.empty()) ch.send({{"event", "log"}, {"line", line}});
    }
};

std::string arch_of_machine() {
    struct utsname u;
    uname(&u);
    std::string m = u.machine;
    if (m == "aarch64" || m == "arm64") return "arm64";
    if (m == "x86_64") return "amd64";
    if (m.rfind("armv7", 0) == 0) return "arm";
    return m;
}

std::string sha256_of(const std::string& path) {
    std::string out;
    int st = run({kSha256sum, path}, &out);
    if (st != 0) return {};
    return out.substr(0, out.find(' '));
}

struct Fetched {
    bool ok = false;
    std::string body;
    std::string error;
};

// HTTPS goes through Android's own stack: com.gbos.terminal.Fetch from the Terminal APK, run
// with app_process (see Fetch.java). Headers in auth_file are only sent to the URL's host.
std::vector<std::string> fetch_argv(const std::vector<std::string>& args) {
    std::vector<std::string> argv = {kAppProcess, "/system/bin", "com.gbos.terminal.Fetch"};
    argv.insert(argv.end(), args.begin(), args.end());
    return argv;
}

std::vector<std::string> fetch_env(const Paths& paths) {
    std::vector<std::string> env;
    for (char** e = environ; *e; e++) {
        if (strncmp(*e, "CLASSPATH=", 10) != 0) env.push_back(*e);
    }
    env.push_back("CLASSPATH=" + paths.apk);
    return env;
}

Fetched fetch_get(const Paths& paths, const std::string& url, const std::vector<std::string>& headers,
                  const std::string& auth_file = {}) {
    std::vector<std::string> args;
    for (auto& h : headers) {
        args.push_back("-H");
        args.push_back(h);
    }
    if (!auth_file.empty()) {
        args.push_back("--auth");
        args.push_back(auth_file);
    }
    args.push_back(url);
    Fetched f;
    auto env = fetch_env(paths);
    int st = run(fetch_argv(args), &f.body, &env);
    f.ok = st == 0;
    if (!f.ok) f.error = format("%s: %s", url.c_str(), describe_status(st).c_str());
    return f;
}

struct Layer {
    std::string digest;  // sha256:<hex>
    uint64_t size = 0;
    std::string manifest_digest;
};

bool resolve(const LinuxConf& conf, const Paths& paths, const std::string& header_file,
             Layer* layer, std::string* err) {
    auto tok = fetch_get(paths, conf.auth + "&scope=repository:" + conf.repository + ":pull", {});
    if (!tok.ok) {
        *err = tok.error;
        return false;
    }
    json t = json::parse(tok.body, nullptr, false);
    std::string token = t.is_object() ? t.value("token", t.value("access_token", "")) : "";
    if (token.empty()) {
        *err = "registry token response without a token";
        return false;
    }
    if (!write_file_atomic(header_file, "Authorization: Bearer " + token + "\n", 0600)) {
        *err = format("write %s: %s", header_file.c_str(), strerror(errno));
        return false;
    }
    std::string base = "https://" + conf.registry + "/v2/" + conf.repository;
    const std::string accept_index =
        "Accept: application/vnd.oci.image.index.v1+json, "
        "application/vnd.docker.distribution.manifest.list.v2+json, "
        "application/vnd.oci.image.manifest.v1+json, "
        "application/vnd.docker.distribution.manifest.v2+json";
    auto idx = fetch_get(paths, base + "/manifests/" + conf.tag, {accept_index}, header_file);
    if (!idx.ok) {
        *err = idx.error;
        return false;
    }
    json index = json::parse(idx.body, nullptr, false);
    if (!index.is_object()) {
        *err = "invalid image index";
        return false;
    }
    std::string arch = arch_of_machine();
    std::string manifest_body;
    if (index.contains("manifests")) {
        std::string digest;
        for (auto& m : index["manifests"]) {
            auto p = m.value("platform", json::object());
            if (p.value("os", "") != "linux" || p.value("architecture", "") != arch) continue;
            std::string variant = p.value("variant", "");
            if (arch == "arm64" && !variant.empty() && variant != "v8") continue;
            digest = m.value("digest", "");
            break;
        }
        if (digest.empty()) {
            *err = format("%s:%s has no linux/%s image", conf.repository.c_str(), conf.tag.c_str(),
                          arch.c_str());
            return false;
        }
        auto man = fetch_get(paths, base + "/manifests/" + digest, {accept_index}, header_file);
        if (!man.ok) {
            *err = man.error;
            return false;
        }
        // Content addressing: the manifest must hash to the digest the index named.
        std::string tmp = paths.cache + "/manifest.json";
        if (!write_file_atomic(tmp, man.body, 0600)) {
            *err = format("write %s: %s", tmp.c_str(), strerror(errno));
            return false;
        }
        std::string got = "sha256:" + sha256_of(tmp);
        unlink(tmp.c_str());
        if (got != digest) {
            *err = format("manifest digest mismatch: expected %s, got %s", digest.c_str(), got.c_str());
            return false;
        }
        layer->manifest_digest = digest;
        manifest_body = std::move(man.body);
    } else {
        manifest_body = std::move(idx.body);
    }
    json manifest = json::parse(manifest_body, nullptr, false);
    if (!manifest.is_object() || !manifest.contains("layers") || !manifest["layers"].is_array()) {
        *err = "invalid image manifest";
        return false;
    }
    auto& layers = manifest["layers"];
    if (layers.size() != 1) {
        *err = format("expected a single-layer image, got %zu layers", layers.size());
        return false;
    }
    std::string media = layers[0].value("mediaType", "");
    if (media.find("tar+gzip") == std::string::npos && media.find("tar.gzip") == std::string::npos) {
        *err = "unsupported layer type " + media;
        return false;
    }
    layer->digest = layers[0].value("digest", "");
    layer->size = layers[0].value("size", 0ull);
    if (!starts_with(layer->digest, "sha256:") || layer->digest.size() != 71 || layer->size == 0) {
        *err = "invalid layer descriptor";
        return false;
    }
    for (char c : layer->digest.substr(7)) {
        if (!isxdigit(static_cast<unsigned char>(c))) {
            *err = "invalid layer digest";
            return false;
        }
    }
    return true;
}

off_t file_size(const std::string& path) {
    struct stat st;
    return stat(path.c_str(), &st) == 0 ? st.st_size : 0;
}

bool download(const Paths& paths, const LinuxConf& conf, const std::string& header_file,
              const Layer& layer, const std::string& part, Reporter& rep, std::string* err) {
    std::string url = "https://" + conf.registry + "/v2/" + conf.repository + "/blobs/" + layer.digest;
    int errpipe[2];
    if (pipe2(errpipe, O_CLOEXEC) != 0) {
        *err = strerror(errno);
        return false;
    }
    auto argv = fetch_argv({"--auth", header_file, "--resume", "-o", part, url});
    auto env = fetch_env(paths);
    pid_t pid = fork();
    if (pid == 0) {
        reset_signals();
        int devnull = open("/dev/null", O_RDWR);
        dup2(devnull, 0);
        dup2(devnull, 1);
        dup2(errpipe[1], 2);
        std::vector<char*> a, e;
        for (auto& x : argv) a.push_back(const_cast<char*>(x.c_str()));
        a.push_back(nullptr);
        for (auto& x : env) e.push_back(const_cast<char*>(x.c_str()));
        e.push_back(nullptr);
        execve(a[0], a.data(), e.data());
        _exit(127);
    }
    close(errpipe[1]);
    fcntl(errpipe[0], F_SETFL, O_NONBLOCK);
    std::string fetch_err;
    int status = 0;
    while (true) {
        pid_t r = waitpid(pid, &status, WNOHANG);
        char buf[1024];
        ssize_t n;
        while ((n = read(errpipe[0], buf, sizeof buf)) > 0) fetch_err.append(buf, n);
        uint64_t have = file_size(part);
        rep.progress("download",
                     kResolveEnd + (kDownloadEnd - kResolveEnd) *
                                       std::min(1.0, static_cast<double>(have) / layer.size),
                     {{"bytes", have}, {"total", layer.size}});
        if (r == pid) break;
        usleep(300 * 1000);
    }
    close(errpipe[0]);
    if (status != 0) {
        *err = "download failed: " + trim(fetch_err.empty() ? describe_status(status) : fetch_err);
        return false;
    }
    return true;
}

bool extract(const std::string& archive, const std::string& dest, Reporter& rep,
             std::string* err) {
    if (!remove_tree(dest) || mkdir(dest.c_str(), 0755) != 0) {
        *err = format("prepare %s: %s", dest.c_str(), strerror(errno));
        return false;
    }
    int in = open(archive.c_str(), O_RDONLY | O_CLOEXEC);
    if (in < 0) {
        *err = format("open %s: %s", archive.c_str(), strerror(errno));
        return false;
    }
    int p[2], e[2];
    if (pipe2(p, O_CLOEXEC) != 0 || pipe2(e, O_CLOEXEC) != 0) {
        *err = strerror(errno);
        close(in);
        return false;
    }
    pid_t pid = fork();
    if (pid == 0) {
        reset_signals();
        dup2(p[0], 0);
        int devnull = open("/dev/null", O_RDWR);
        dup2(devnull, 1);
        dup2(e[1], 2);
        // Numeric owners (never Android's account names), modes incl. setuid bits, and no
        // entry may land outside dest.
        execl(kTar, "tar", "-x", "-z", "-p", "--numeric-owner", "--restrict", "-f", "-", "-C",
              dest.c_str(), static_cast<char*>(nullptr));
        _exit(127);
    }
    close(p[0]);
    close(e[1]);
    fcntl(e[0], F_SETFL, O_NONBLOCK);
    off_t total = file_size(archive), fed = 0;
    std::string tar_err;
    char buf[256 * 1024];
    bool write_failed = false;
    while (true) {
        ssize_t n = TEMP_FAILURE_RETRY(read(in, buf, sizeof buf));
        if (n <= 0) break;
        ssize_t off = 0;
        while (off < n) {
            ssize_t w = TEMP_FAILURE_RETRY(write(p[1], buf + off, n - off));
            if (w <= 0) {
                write_failed = true;
                break;
            }
            off += w;
        }
        if (write_failed) break;
        fed += n;
        char eb[512];
        ssize_t en;
        while ((en = read(e[0], eb, sizeof eb)) > 0) tar_err.append(eb, en);
        rep.progress("extract", kVerifyEnd + (kExtractEnd - kVerifyEnd) * fed / std::max<off_t>(total, 1));
    }
    close(in);
    close(p[1]);
    int status = 0;
    waitpid(pid, &status, 0);
    fcntl(e[0], F_SETFL, 0);
    char eb[512];
    ssize_t en;
    while ((en = read(e[0], eb, sizeof eb)) > 0) tar_err.append(eb, en);
    close(e[0]);
    if (status != 0 || write_failed) {
        *err = "unpacking failed: " + trim(tar_err.empty() ? describe_status(status) : tar_err);
        return false;
    }
    if (!path_exists(dest + "/etc/debian_version") || !path_exists(dest + "/usr/bin/apt-get")) {
        *err = "the unpacked image is not a Debian system";
        return false;
    }
    // The image carries placeholders for what a container runtime injects (Docker writes its own
    // resolv.conf, hosts and hostname). Here gbos-linuxd provides them, so they go.
    for (const char* f : {"/etc/resolv.conf", "/etc/hosts", "/etc/hostname"}) {
        std::string p = dest + f;
        struct stat st;
        if (lstat(p.c_str(), &st) == 0 && (S_ISREG(st.st_mode) || S_ISLNK(st.st_mode))) unlink(p.c_str());
    }
    if (chmod(dest.c_str(), 0755) != 0 || chown(dest.c_str(), 0, 0) != 0) {
        *err = format("chmod %s: %s", dest.c_str(), strerror(errno));
        return false;
    }
    return true;
}

// Whether the kernel needs the inet group for sockets (Android's paranoid networking):
// tries a socket as an unprivileged uid without supplementary groups.
bool kernel_needs_inet_group() {
    pid_t pid = fork();
    if (pid == 0) {
        if (setgroups(0, nullptr) != 0 || setresgid(65534, 65534, 65534) != 0 ||
            setresuid(65534, 65534, 65534) != 0)
            _exit(2);
        int fd = socket(AF_INET, SOCK_STREAM, 0);
        _exit(fd < 0 && errno == EACCES ? 1 : 0);
    }
    int status = 0;
    waitpid(pid, &status, 0);
    return WIFEXITED(status) && WEXITSTATUS(status) == 1;
}

std::string script(const Paths& paths, const char* name, std::string* err) {
    auto s = read_file(paths.sysdir + "/" + name);
    if (!s) *err = format("missing %s/%s", paths.sysdir.c_str(), name);
    return s ? *s : std::string();
}

std::string joined(const std::vector<std::string>& v) {
    std::string r;
    for (auto& s : v) r += (r.empty() ? "" : " ") + s;
    return r;
}

}  // namespace

LinuxConf LinuxConf::load(const Paths& paths) {
    LinuxConf c;
    if (auto text = read_file(paths.sysdir + "/linux.conf")) {
        for (auto& line : split(*text, '\n')) {
            line = trim(line);
            if (line.empty() || line[0] == '#') continue;
            size_t eq = line.find('=');
            if (eq == std::string::npos) continue;
            std::string k = trim(line.substr(0, eq)), v = trim(line.substr(eq + 1));
            if (k == "registry") c.registry = v;
            else if (k == "auth") c.auth = v;
            else if (k == "repository") c.repository = v;
            else if (k == "tag") c.tag = v;
        }
    }
    return c;
}

json read_state(const Paths& paths) {
    auto text = read_file(paths.state);
    if (!text) return json{{"state", "absent"}};
    json s = json::parse(*text, nullptr, false);
    return s.is_object() ? s : json{{"state", "absent"}};
}

bool write_state(const Paths& paths, const json& state) {
    return write_file_atomic(paths.state, state.dump(2) + "\n", 0644);
}

int run_guest_script(const Paths& paths, const std::string& text,
                     const std::vector<std::string>& args,
                     const std::function<void(const std::string&)>& on_line,
                     const std::function<void(const std::string&)>& on_status, std::string* err) {
    int fd = connect_unix(paths.socket);
    if (fd < 0) {
        *err = format("connect %s: %s", paths.socket.c_str(), strerror(errno));
        return -1;
    }
    Channel ch(fd);
    int in[2], out[2], st[2];
    if (pipe2(in, O_CLOEXEC) != 0 || pipe2(out, O_CLOEXEC) != 0 || pipe2(st, O_CLOEXEC) != 0) {
        *err = strerror(errno);
        return -1;
    }
    json argv = json::array({"/bin/sh", "-s", "--"});
    for (auto& a : args) argv.push_back(a);
    bool sent = ch.send({{"op", "spawn"}, {"tty", false}, {"user", "root"}, {"argv", argv},
                         {"cwd", "/"},
                         {"env", json::array({"DEBIAN_FRONTEND=noninteractive", "LANG=C.UTF-8",
                                              "TERM=dumb"})}},
                        {in[0], out[1], out[1], st[1]});
    close(in[0]);
    close(out[1]);
    close(st[1]);
    if (!sent) {
        *err = format("send: %s", strerror(errno));
        close(in[1]);
        close(out[0]);
        close(st[0]);
        return -1;
    }
    json reply;
    if (!ch.recv(&reply) || !reply.value("ok", false)) {
        *err = reply.is_object() ? reply.value("error", "spawn failed") : "daemon closed the connection";
        close(in[1]);
        close(out[0]);
        close(st[0]);
        return -1;
    }
    signal(SIGPIPE, SIG_IGN);
    size_t off = 0;
    while (off < text.size()) {
        ssize_t n = TEMP_FAILURE_RETRY(write(in[1], text.data() + off, text.size() - off));
        if (n <= 0) break;
        off += n;
    }
    close(in[1]);
    std::string obuf, sbuf;
    int open_fds = 2;
    pollfd pfds[2] = {{out[0], POLLIN, 0}, {st[0], POLLIN, 0}};
    auto drain = [](std::string& buf, const std::function<void(const std::string&)>& cb) {
        size_t nl;
        while ((nl = buf.find_first_of("\r\n")) != std::string::npos) {
            std::string line = buf.substr(0, nl);
            buf.erase(0, nl + 1);
            if (!line.empty()) cb(line);
        }
    };
    while (open_fds > 0) {
        if (poll(pfds, 2, -1) < 0 && errno != EINTR) break;
        for (int i = 0; i < 2; i++) {
            if (pfds[i].fd < 0 || !pfds[i].revents) continue;
            char buf[8192];
            ssize_t n = read(pfds[i].fd, buf, sizeof buf);
            if (n <= 0) {
                close(pfds[i].fd);
                pfds[i].fd = -1;
                open_fds--;
                continue;
            }
            (i == 0 ? obuf : sbuf).append(buf, n);
            drain(i == 0 ? obuf : sbuf, i == 0 ? on_line : on_status);
        }
    }
    if (!obuf.empty()) on_line(obuf);
    json ev;
    while (ch.recv(&ev)) {
        if (ev.value("event", "") == "exit") return ev.value("status", 1);
    }
    *err = "lost the session before it reported its exit status";
    return -1;
}

bool create_user(const Paths& paths, const InstallOptions& opts, Channel* events, std::string* err) {
    if (!valid_user_name(opts.user)) {
        *err = "invalid user name";
        return false;
    }
    std::string text = script(paths, "adduser.sh", err);
    if (text.empty()) return false;
    std::string output;
    int status = run_guest_script(
        paths, text, {opts.user, std::to_string(opts.user_uid), std::to_string(opts.android_user)},
        [&](const std::string& line) {
            output += line + "\n";
            if (events) events->send({{"event", "log"}, {"line", line}});
        },
        [](const std::string&) {}, err);
    if (status != 0) {
        if (err->empty()) *err = trim(output).empty() ? format("adduser.sh exited %d", status) : trim(output);
        return false;
    }
    return true;
}

bool create_droid(const Paths& paths, const std::string& owner, uid_t uid, int android_user,
                  std::string* err) {
    std::string text = script(paths, "droidadd.sh", err);
    if (text.empty()) return false;
    std::string output;
    int status = run_guest_script(paths, text, {owner, std::to_string(uid), std::to_string(android_user)},
                                  [&](const std::string& line) { output += line + "\n"; },
                                  [](const std::string&) {}, err);
    if (status != 0) {
        if (err->empty()) *err = trim(output).empty() ? format("droidadd.sh exited %d", status) : trim(output);
        return false;
    }
    return true;
}

bool generate_locale(const Paths& paths, const std::string& locale, std::string* err) {
    std::string text = script(paths, "locale.sh", err);
    if (text.empty()) return false;
    std::string output;
    int status = run_guest_script(paths, text, {locale},
                                  [&](const std::string& l) { output += l + "\n"; },
                                  [](const std::string&) {}, err);
    if (status != 0 && err->empty()) *err = trim(output);
    return status == 0;
}

int install_worker(const Paths& paths, const InstallOptions& opts, Channel& events) {
    setpgid(0, 0);  // cancel kills the whole group: the downloader and tar included
    signal(SIGPIPE, SIG_IGN);
    Reporter rep{events, "", -1};
    json state = read_state(paths);
    std::string prev = state.value("state", "absent");
    auto fail = [&](const std::string& phase, const std::string& code, const std::string& msg) {
        loge("install: %s: %s", phase.c_str(), msg.c_str());
        state["state"] = "failed";
        state["failed_phase"] = phase;
        state["error"] = msg;
        state["error_code"] = code;
        write_state(paths, state);
        events.send({{"event", "done"}, {"ok", false}, {"phase", phase}, {"code", code}, {"error", msg}});
        return 1;
    };
    if (!mkdirs(paths.cache, 0700)) return fail("prepare", "io", format("mkdir %s: %s", paths.cache.c_str(), strerror(errno)));
    LinuxConf conf = LinuxConf::load(paths);
    bool have_rootfs = is_dir(paths.rootfs + "/usr");

    if (!have_rootfs) {
        struct statvfs vfs;
        if (statvfs(paths.base.c_str(), &vfs) == 0) {
            uint64_t free_bytes = static_cast<uint64_t>(vfs.f_bavail) * vfs.f_frsize;
            if (free_bytes < kMinFreeBytes)
                return fail("prepare", "disk", format("%llu MB free, %llu MB needed",
                                                      static_cast<unsigned long long>(free_bytes >> 20),
                                                      static_cast<unsigned long long>(kMinFreeBytes >> 20)));
        }
        rep.progress("resolve", 0);
        state = {{"state", "downloading"}, {"image", conf.repository + ":" + conf.tag}};
        write_state(paths, state);
        std::string header_file = paths.cache + "/registry-auth";
        Layer layer;
        std::string err;
        if (!resolve(conf, paths, header_file, &layer, &err)) {
            unlink(header_file.c_str());
            return fail("resolve", "network", err);
        }
        state["digest"] = layer.digest;
        state["manifest"] = layer.manifest_digest;
        write_state(paths, state);
        rep.log(format("%s:%s %s (%llu bytes)", conf.repository.c_str(), conf.tag.c_str(),
                       layer.digest.c_str(), static_cast<unsigned long long>(layer.size)));
        std::string hex = layer.digest.substr(7);
        std::string part = paths.cache + "/" + hex + ".part";
        std::string archive = paths.cache + "/" + hex + ".tar.gz";
        // Leftovers of other (older) images are useless now.
        if (DIR* d = opendir(paths.cache.c_str())) {
            while (struct dirent* e = readdir(d)) {
                std::string n = e->d_name;
                if ((n.size() > 5 && n.compare(n.size() - 5, 5, ".part") == 0) ||
                    (n.size() > 7 && n.compare(n.size() - 7, 7, ".tar.gz") == 0)) {
                    if (!starts_with(n, hex)) unlinkat(dirfd(d), n.c_str(), 0);
                }
            }
            closedir(d);
        }
        if (!path_exists(archive)) {
            if (file_size(part) > static_cast<off_t>(layer.size)) unlink(part.c_str());
            if (!download(paths, conf, header_file, layer, part, rep, &err)) {
                unlink(header_file.c_str());
                return fail("download", "network", err);
            }
            rep.progress("verify", kDownloadEnd);
            std::string got = sha256_of(part);
            if ("sha256:" + got != layer.digest) {
                unlink(part.c_str());
                unlink(header_file.c_str());
                return fail("verify", "integrity",
                            format("digest mismatch: expected %s, got sha256:%s", layer.digest.c_str(), got.c_str()));
            }
            if (rename(part.c_str(), archive.c_str()) != 0)
                return fail("verify", "io", format("rename: %s", strerror(errno)));
        }
        unlink(header_file.c_str());
        rep.progress("verify", kVerifyEnd);
        state["state"] = "extracting";
        write_state(paths, state);
        std::string staging = paths.rootfs + ".new";
        if (!extract(archive, staging, rep, &err)) {
            remove_tree(staging);
            return fail("extract", "io", err);
        }
        if (rename(staging.c_str(), paths.rootfs.c_str()) != 0)
            return fail("extract", "io", format("rename %s: %s", staging.c_str(), strerror(errno)));
        unlink(archive.c_str());
        if (auto v = read_file(paths.rootfs + "/etc/debian_version")) state["debian_version"] = trim(*v);
        rep.progress("extract", kExtractEnd);
    }

    if (!have_rootfs || prev != "installed" || opts.repair) {
        state["state"] = "configuring";
        write_state(paths, state);
        rep.progress("configure", kExtractEnd);
        std::string err;
        std::string text = script(paths, "setup.sh", &err);
        if (text.empty()) return fail("configure", "io", err);
        std::string packages;
        if (auto p = read_file(paths.sysdir + "/packages")) {
            for (auto& line : split(*p, '\n')) {
                line = trim(line);
                if (!line.empty() && line[0] != '#') packages += (packages.empty() ? "" : " ") + line;
            }
        }
        std::vector<std::string> locales = opts.locales;
        if (locales.empty()) locales.push_back("C.UTF-8");
        double win_start = kExtractEnd, win_end = kExtractEnd;
        std::string label;
        int status = run_guest_script(
            paths, text,
            {packages, joined(locales), kernel_needs_inet_group() ? "1" : "0"},
            [&](const std::string& line) { rep.log(line); },
            [&](const std::string& line) {
                // gbos:step:<start>:<end>:<label> from setup.sh, then apt's Status-Fd lines.
                auto f = split(line, ':');
                if (f.size() >= 5 && f[0] == "gbos" && f[1] == "step") {
                    double a = atof(f[2].c_str()), b = atof(f[3].c_str());
                    win_start = kExtractEnd + (kConfigureEnd - kExtractEnd) * a / 100;
                    win_end = kExtractEnd + (kConfigureEnd - kExtractEnd) * b / 100;
                    label = f[4];
                    rep.progress("configure", win_start, {{"step", label}});
                } else if (f.size() >= 4 && (f[0] == "pmstatus" || f[0] == "dlstatus")) {
                    double pct = atof(f[2].c_str());
                    std::string msg = line.substr(f[0].size() + f[1].size() + f[2].size() + 3);
                    rep.progress("configure", win_start + (win_end - win_start) * pct / 100,
                                 {{"step", label}, {"detail", msg}});
                }
            },
            &err);
        if (status != 0) {
            return fail("configure", "configure",
                        err.empty() ? format("setup.sh exited with %d (see the log)", status) : err);
        }
        state["state"] = "installed";
        state["installed_at"] = static_cast<long long>(time(nullptr));
        state.erase("error");
        state.erase("error_code");
        state.erase("failed_phase");
        if (auto v = read_file(paths.rootfs + "/etc/debian_version")) state["debian_version"] = trim(*v);
        write_state(paths, state);
    }

    if (!opts.user.empty()) {
        rep.progress("user", kConfigureEnd);
        std::string err;
        if (!create_user(paths, opts, &events, &err)) return fail("user", "user", err);
    }
    rep.progress("done", 100);
    events.send({{"event", "done"}, {"ok", true}});
    return 0;
}

}  // namespace gbos
