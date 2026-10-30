// gbos-linuxd: root service of the Googlebook Linux environment.
//
// Started by init (gbos-linux.rc). Listens on /dev/socket/gbos_linux; callers are identified
// by SO_PEERCRED: root and adb shell may do everything, the Terminal app (com.gbos.terminal,
// any Android user) may install the environment and open sessions as its own Linux user.
// Protocol: one JSON object per line, file descriptors as SCM_RIGHTS (see BENIOKU.md).
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <signal.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mount.h>
#include <sys/prctl.h>
#include <sys/signalfd.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/un.h>
#include <sys/wait.h>
#include <unistd.h>

#include <deque>
#include <memory>
#include <regex>
#include <set>

#include "config.h"
#include "container.h"
#include "dns.h"
#include "droid.h"
#include "installer.h"
#include "loop.h"
#include "util.h"

namespace gbos {
namespace {

constexpr int kProtocolVersion = 1;
constexpr size_t kLogTail = 300;

struct Caller {
    enum Kind { Root, Shell, App, Guest, Other } kind = Other;
    uid_t uid = 0;
    pid_t pid = 0;
    int android_user = -1;
    bool privileged() const { return kind == Root || kind == Shell; }
};

struct Conn {
    Channel ch;
    Caller caller;
    bool host = false;  // connection on the environment's host.sock
};

bool valid_env_name(const std::string& k) {
    if (k.empty() || !(isalpha(static_cast<unsigned char>(k[0])) || k[0] == '_')) return false;
    for (char c : k)
        if (!(isalnum(static_cast<unsigned char>(c)) || c == '_')) return false;
    return true;
}

bool valid_locale(const std::string& l) {
    static const std::regex re("^[A-Za-z]{1,8}(_[A-Za-z0-9]{1,8})?(\\.[A-Za-z0-9-]{1,16})?(@[a-z]{1,16})?$");
    return std::regex_match(l, re);
}

// "tr-TR" / "tr_TR" -> "tr_TR.UTF-8"
std::string android_to_posix_locale(const std::string& tag) {
    std::string lang, region;
    auto parts = split(tag, tag.find('-') != std::string::npos ? '-' : '_');
    if (parts.empty() || parts[0].empty()) return {};
    lang = parts[0];
    for (size_t i = 1; i < parts.size(); i++) {
        if (parts[i].size() == 2 || (parts[i].size() == 3 && isdigit(static_cast<unsigned char>(parts[i][0])))) {
            region = parts[i];
            break;
        }
    }
    for (auto& c : lang) c = static_cast<char>(tolower(static_cast<unsigned char>(c)));
    for (auto& c : region) c = static_cast<char>(toupper(static_cast<unsigned char>(c)));
    std::string r = lang + (region.empty() ? "" : "_" + region) + ".UTF-8";
    return valid_locale(r) ? r : std::string();
}

class Daemon {
public:
    Daemon()
        : paths_(Paths::get()),
          container_(paths_),
          dns_(loop_, [this](uid_t uid) { return dns_allowed(uid); }) {}

    int serve();

private:
    // connections
    void on_accept(int listen_fd, bool host);
    void on_conn(int fd, short revents);
    void drop(int fd);
    void dispatch(int fd, json& msg, std::vector<int>& fds);
    void reply_error(Conn& c, const std::string& code, const std::string& msg);
    Caller classify(int fd);

    // operations
    void op_status(Conn& c);
    void op_install(int fd, Conn& c, const json& msg);
    void op_cancel(Conn& c);
    void op_spawn(int fd, Conn& c, const json& msg, std::vector<int>& fds);
    void op_useradd(int fd, Conn& c, const json& msg);
    void op_droid_account(int fd, Conn& c);
    void op_droid_file(Conn& c, const json& msg);
    void op_droid_prepare(Conn& c);
    std::optional<LinuxUser> droid_of(const Caller& c, std::string* code, std::string* err);
    void op_stop(Conn& c);
    void op_end_user(Conn& c);
    void op_remove(int fd, Conn& c);
    void op_open(int fd, Conn& c, const json& msg);

    // helpers
    bool ensure_running(std::string* err);
    void start_services();
    void stop_services();
    uid_t terminal_app_id();
    bool dns_allowed(uid_t uid);
    int android_user_of(const Caller& c);
    std::vector<std::string> default_locales();
    std::set<std::string> available_locales();
    void ensure_locale(const std::string& locale);
    // Hands the connection to a forked job running fn(conn); the daemon forgets it.
    void fork_job(int fd, const std::function<void(Conn&)>& fn,
                  std::function<void(int status)> done = nullptr);
    void on_signal();
    void on_install_events(short revents);
    void install_finished(const json& done);
    bool installing() const { return install_pid_ > 0; }

    Paths paths_;
    Loop loop_;
    Container container_;
    DnsForwarder dns_;
    int control_fd_ = -1;
    int host_fd_ = -1;
    int signal_fd_ = -1;
    bool quit_ = false;
    std::map<int, std::unique_ptr<Conn>> conns_;
    std::map<pid_t, int> sessions_;  // monitor pid -> Android user (-1: root/shell)
    std::map<pid_t, std::function<void(int)>> jobs_;

    pid_t install_pid_ = -1;
    Channel install_events_;
    std::set<int> install_watchers_;
    json install_progress_;
    std::deque<std::string> install_log_;
    bool install_cancelled_ = false;
    bool install_done_sent_ = false;

    uid_t terminal_app_id_ = 0;
    time_t packages_mtime_ = 0;
    std::set<std::string> locale_jobs_;
};

int Daemon::serve() {
    umask(022);
    signal(SIGPIPE, SIG_IGN);
    prctl(PR_SET_CHILD_SUBREAPER, 1, 0, 0, 0);
    sigset_t mask;
    sigemptyset(&mask);
    sigaddset(&mask, SIGCHLD);
    sigaddset(&mask, SIGTERM);
    sigaddset(&mask, SIGINT);
    sigprocmask(SIG_BLOCK, &mask, nullptr);
    signal_fd_ = signalfd(-1, &mask, SFD_CLOEXEC | SFD_NONBLOCK);
    loop_.add(signal_fd_, POLLIN, [this](short) { on_signal(); });

    if (!mkdirs(paths_.base, 0711) || !mkdirs(paths_.cache, 0700) || !mkdirs(paths_.run, 0755)) {
        loge("cannot create %s: %s", paths_.base.c_str(), strerror(errno));
        return 1;
    }
    // Control socket: from init, or (development runs) created here.
    if (const char* env = getenv("ANDROID_SOCKET_gbos_linux")) {
        control_fd_ = atoi(env);
        fcntl(control_fd_, F_SETFD, FD_CLOEXEC);
    } else {
        control_fd_ = socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0);
        sockaddr_un addr = {};
        addr.sun_family = AF_UNIX;
        strncpy(addr.sun_path, paths_.socket.c_str(), sizeof addr.sun_path - 1);
        unlink(paths_.socket.c_str());
        if (bind(control_fd_, reinterpret_cast<sockaddr*>(&addr), sizeof addr) != 0) {
            loge("bind %s: %s", paths_.socket.c_str(), strerror(errno));
            return 1;
        }
        chmod(paths_.socket.c_str(), 0666);
    }
    fcntl(control_fd_, F_SETFL, O_NONBLOCK);
    if (listen(control_fd_, 32) != 0) {
        loge("listen: %s", strerror(errno));
        return 1;
    }
    loop_.add(control_fd_, POLLIN, [this](short) { on_accept(control_fd_, false); });

    // A previous instance may have been interrupted mid-install; the next install resumes.
    json state = read_state(paths_);
    std::string s = state.value("state", "absent");
    if (s == "downloading" || s == "extracting" || s == "configuring") {
        state["state"] = "failed";
        state["failed_phase"] = s;
        state["error"] = "interrupted";
        state["error_code"] = "interrupted";
        write_state(paths_, state);
    }
    std::string mount_err;
    if (!container_.prepare_host_mounts(&mount_err)) {
        loge("%s", mount_err.c_str());
        return 1;
    }
    if (container_.adopt()) start_services();
    logi("gbos-linuxd ready (protocol %d, state %s)", kProtocolVersion,
         read_state(paths_).value("state", "absent").c_str());
    while (!quit_) loop_.run_once();
    stop_services();
    return 0;
}

void Daemon::on_signal() {
    signalfd_siginfo si;
    while (read(signal_fd_, &si, sizeof si) == sizeof si) {
        if (si.ssi_signo == SIGTERM || si.ssi_signo == SIGINT) quit_ = true;
    }
    int status;
    pid_t pid;
    while ((pid = waitpid(-1, &status, WNOHANG)) > 0) {
        sessions_.erase(pid);
        if (pid == install_pid_) {
            install_pid_ = -1;
            on_install_events(POLLIN);  // anything still buffered
            if (!install_done_sent_) {
                install_finished({{"event", "done"}, {"ok", false},
                                  {"code", install_cancelled_ ? "cancelled" : "crashed"},
                                  {"error", install_cancelled_ ? "cancelled" : describe_status(status)}});
            }
            if (install_events_.fd() >= 0) loop_.remove(install_events_.fd());
            install_events_.close();
            if (install_cancelled_) {
                json st = read_state(paths_);
                if (st.value("state", "") != "installed" && st.value("state", "") != "absent") {
                    st["state"] = "failed";
                    st["error"] = "cancelled";
                    st["error_code"] = "cancelled";
                    write_state(paths_, st);
                }
            }
        }
        auto job = jobs_.find(pid);
        if (job != jobs_.end()) {
            auto cb = std::move(job->second);
            jobs_.erase(job);
            if (cb) cb(status);
        }
    }
}

void Daemon::on_accept(int listen_fd, bool host) {
    while (true) {
        int fd = accept4(listen_fd, nullptr, nullptr, SOCK_CLOEXEC | SOCK_NONBLOCK);
        if (fd < 0) return;
        auto c = std::make_unique<Conn>();
        c->ch = Channel(fd);
        c->host = host;
        c->caller = classify(fd);
        if (host) {
            c->caller.kind = Caller::Guest;
        } else if (c->caller.kind == Caller::Other) {
            Channel ch(fd);
            ch.send({{"ok", false}, {"code", "denied"}, {"error", "not allowed"}});
            c->ch.release();
            continue;
        }
        conns_[fd] = std::move(c);
        loop_.add(fd, POLLIN, [this, fd](short ev) { on_conn(fd, ev); });
    }
}

Caller Daemon::classify(int fd) {
    Caller c;
    if (!peer_cred(fd, &c.pid, &c.uid, nullptr)) return c;
    if (c.uid == 0) c.kind = Caller::Root;
    else if (c.uid == kAidShell && get_property("ro.debuggable") == "1") c.kind = Caller::Shell;
    else if (terminal_app_id() != 0 && c.uid % kPerUserRange == terminal_app_id()) {
        c.kind = Caller::App;
        c.android_user = static_cast<int>(c.uid / kPerUserRange);
    }
    return c;
}

uid_t Daemon::terminal_app_id() {
    struct stat st;
    if (stat("/data/system/packages.list", &st) != 0) return terminal_app_id_;
    if (st.st_mtime == packages_mtime_ && terminal_app_id_ != 0) return terminal_app_id_;
    packages_mtime_ = st.st_mtime;
    if (auto text = read_file("/data/system/packages.list")) {
        for (auto& line : split(*text, '\n')) {
            auto f = split(line, ' ');
            if (f.size() > 1 && f[0] == kTerminalPackage) {
                terminal_app_id_ = static_cast<uid_t>(strtoul(f[1].c_str(), nullptr, 10));
                break;
            }
        }
    }
    return terminal_app_id_;
}

bool Daemon::dns_allowed(uid_t uid) {
    uid_t app = uid % kPerUserRange;
    if (terminal_app_id() != 0 && app == terminal_app_id()) return true;
    bool android_app = (app >= 10000 && app <= 19999) || (app >= 20000 && app <= 29999) ||
                       (app >= 90000 && app <= 99999);
    return !android_app;
}

void Daemon::drop(int fd) {
    loop_.remove(fd);
    install_watchers_.erase(fd);
    conns_.erase(fd);
}

void Daemon::on_conn(int fd, short) {
    auto it = conns_.find(fd);
    if (it == conns_.end()) return;
    std::vector<std::pair<json, std::vector<int>>> msgs;
    bool alive = it->second->ch.pump(&msgs);
    for (auto& [msg, fds] : msgs) {
        if (!conns_.count(fd)) {  // handed over to a job or monitor
            for (int f : fds) close(f);
            continue;
        }
        dispatch(fd, msg, fds);
        for (int f : fds)
            if (f >= 0) close(f);
    }
    if (!alive && conns_.count(fd)) drop(fd);
}

void Daemon::reply_error(Conn& c, const std::string& code, const std::string& msg) {
    c.ch.send({{"ok", false}, {"code", code}, {"error", msg}});
}

void Daemon::dispatch(int fd, json& msg, std::vector<int>& fds) {
    Conn& c = *conns_[fd];
    std::string op = msg.value("op", "");
    try {
        if (c.host) {
            if (op == "open") return op_open(fd, c, msg);
            return reply_error(c, "denied", "unsupported request");
        }
        if (op == "status") return op_status(c);
        if (op == "install") return op_install(fd, c, msg);
        if (op == "cancel") return op_cancel(c);
        if (op == "spawn") return op_spawn(fd, c, msg, fds);
        if (op == "useradd") return op_useradd(fd, c, msg);
        if (op == "droid_account") return op_droid_account(fd, c);
        if (op == "droid_file") return op_droid_file(c, msg);
        if (op == "droid_prepare") return op_droid_prepare(c);
        if (op == "stop") return op_stop(c);
        if (op == "end_user") return op_end_user(c);
        if (op == "remove") return op_remove(fd, c);
        reply_error(c, "bad_request", "unknown op " + op);
    } catch (const json::exception& e) {
        reply_error(c, "bad_request", e.what());
    }
}

std::vector<std::string> Daemon::default_locales() {
    std::vector<std::string> out;
    for (const char* prop : {"persist.sys.locale", "ro.product.locale"}) {
        std::string l = android_to_posix_locale(get_property(prop));
        if (!l.empty() && std::find(out.begin(), out.end(), l) == out.end()) out.push_back(l);
    }
    if (std::find(out.begin(), out.end(), "en_US.UTF-8") == out.end()) out.push_back("en_US.UTF-8");
    return out;
}

std::set<std::string> Daemon::available_locales() {
    std::set<std::string> out = {"C", "C.UTF-8", "C.utf8", "POSIX"};
    if (auto text = read_file(paths_.rootfs + "/var/lib/googlebook/locales")) {
        for (auto& l : split(*text, '\n'))
            if (!trim(l).empty()) out.insert(trim(l));
    }
    return out;
}

void Daemon::ensure_locale(const std::string& locale) {
    if (locale_jobs_.count(locale) || install_pid_ > 0) return;
    locale_jobs_.insert(locale);
    pid_t pid = fork();
    if (pid == 0) {
        reset_signals();
        std::string err;
        bool ok = generate_locale(paths_, locale, &err);
        if (!ok) loge("locale %s: %s", locale.c_str(), err.c_str());
        _exit(ok ? 0 : 1);
    }
    if (pid > 0) {
        jobs_[pid] = [this, locale](int) { locale_jobs_.erase(locale); };
    } else {
        locale_jobs_.erase(locale);
    }
}

void Daemon::op_status(Conn& c) {
    json st = read_state(paths_);
    std::string state = st.value("state", "absent");
    bool installed = state == "installed" && is_dir(paths_.rootfs + "/usr");
    json r = {{"ok", true},
              {"protocol", kProtocolVersion},
              {"state", installing() ? "installing" : state},
              {"installed", installed},
              {"running", container_.running()},
              {"sessions", sessions_.size()},
              {"image", st.value("image", "")},
              {"debian_version", st.value("debian_version", "")}};
    if (st.contains("error")) {
        r["error"] = st["error"];
        r["error_code"] = st.value("error_code", "");
    }
    if (install_pid_ > 0) r["progress"] = install_progress_;
    if (c.caller.kind == Caller::App && installed) {
        if (auto u = find_user_by_uid(paths_.rootfs, c.caller.uid))
            r["user"] = {{"name", u->name}, {"uid", u->uid}, {"home", u->home}};
        else
            r["user"] = nullptr;
        // The caller's Linux programs still running (the Terminal keeps itself in the
        // foreground while there are any, so Android does not cut their network).
        r["processes"] = container_.running() ? container_.processes_of(c.caller.uid).size() : 0;
    }
    c.ch.send(r);
}

void Daemon::op_install(int fd, Conn& c, const json& msg) {
    if (!(c.caller.privileged() || c.caller.kind == Caller::App))
        return reply_error(c, "denied", "not allowed");
    InstallOptions opts;
    opts.repair = msg.value("repair", false);
    std::string user = msg.value("user", "");
    if (!user.empty()) {
        if (!valid_user_name(user))
            return reply_error(c, "bad_user", "user names start with a lowercase letter and use a-z, 0-9, _ and -");
        if (c.caller.kind == Caller::App) {
            opts.user_uid = c.caller.uid;
            opts.android_user = c.caller.android_user;
        } else {
            opts.android_user = msg.value("android_user", -1);
            if (opts.android_user < 0) return reply_error(c, "bad_request", "android_user required");
            if (terminal_app_id() == 0) return reply_error(c, "no_app", "the Terminal app is not installed");
            opts.user_uid = static_cast<uid_t>(opts.android_user) * kPerUserRange + terminal_app_id();
        }
        opts.user = user;
    }
    if (msg.contains("locales") && msg["locales"].is_array()) {
        for (auto& l : msg["locales"]) {
            if (l.is_string() && valid_locale(l.get<std::string>())) opts.locales.push_back(l.get<std::string>());
        }
    }
    if (opts.locales.empty()) opts.locales = default_locales();

    if (!installing()) {
        json st = read_state(paths_);
        if (st.value("state", "") == "installed" && is_dir(paths_.rootfs + "/usr") && !opts.repair &&
            opts.user.empty()) {
            c.ch.send({{"event", "done"}, {"ok", true}});
            return;
        }
        int sp[2];
        if (socketpair(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0, sp) != 0)
            return reply_error(c, "io", strerror(errno));
        pid_t pid = fork();
        if (pid < 0) {
            close(sp[0]);
            close(sp[1]);
            return reply_error(c, "io", strerror(errno));
        }
        if (pid == 0) {
            reset_signals();
            close_fds_except({0, 1, 2, sp[1]});
            prctl(PR_SET_NAME, "gbos-install", 0, 0, 0);
            Channel events(sp[1]);
            _exit(install_worker(paths_, opts, events));
        }
        close(sp[1]);
        install_pid_ = pid;
        install_cancelled_ = false;
        install_done_sent_ = false;
        install_progress_ = {{"phase", "resolve"}, {"percent", 0}};
        install_log_.clear();
        install_events_ = Channel(sp[0]);
        fcntl(sp[0], F_SETFL, O_NONBLOCK);
        loop_.add(sp[0], POLLIN, [this](short ev) { on_install_events(ev); });
        logi("install started (pid %d, user '%s')", pid, opts.user.c_str());
    } else {
        // Already running: show where it is, including the log so far.
        json ev = install_progress_;
        ev["event"] = "progress";
        c.ch.send(ev);
        for (auto& line : install_log_) c.ch.send({{"event", "log"}, {"line", line}});
    }
    install_watchers_.insert(fd);
}

void Daemon::on_install_events(short) {
    if (install_events_.fd() < 0) return;
    std::vector<std::pair<json, std::vector<int>>> msgs;
    bool alive = install_events_.pump(&msgs);
    for (auto& [ev, fds] : msgs) {
        for (int f : fds) close(f);
        std::string kind = ev.value("event", "");
        if (kind == "progress") {
            install_progress_ = ev;
            install_progress_.erase("event");
        } else if (kind == "log") {
            install_log_.push_back(ev.value("line", ""));
            while (install_log_.size() > kLogTail) install_log_.pop_front();
        } else if (kind == "done") {
            install_finished(ev);
            continue;
        }
        for (int w : install_watchers_) {
            auto it = conns_.find(w);
            if (it != conns_.end()) it->second->ch.send(ev);
        }
    }
    if (!alive && install_events_.fd() >= 0) {
        loop_.remove(install_events_.fd());
        install_events_.close();
    }
}

void Daemon::install_finished(const json& done) {
    install_done_sent_ = true;
    logi("install finished: %s", done.dump().c_str());
    auto watchers = install_watchers_;
    for (int w : watchers) {
        auto it = conns_.find(w);
        if (it != conns_.end()) it->second->ch.send(done);
        drop(w);
    }
    install_watchers_.clear();
}

void Daemon::op_cancel(Conn& c) {
    if (!(c.caller.privileged() || c.caller.kind == Caller::App))
        return reply_error(c, "denied", "not allowed");
    if (installing()) {
        install_cancelled_ = true;
        kill(-install_pid_, SIGTERM);
        kill(install_pid_, SIGTERM);
    }
    c.ch.send({{"ok", true}});
}

bool Daemon::ensure_running(std::string* err) {
    if (container_.running()) return true;
    if (!container_.start(err)) return false;
    start_services();
    return true;
}

void Daemon::start_services() {
    std::string err;
    if (!dns_.start(&err)) loge("%s", err.c_str());
    if (host_fd_ < 0) {
        std::string path = paths_.run + "/host.sock";
        host_fd_ = socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC | SOCK_NONBLOCK, 0);
        sockaddr_un addr = {};
        addr.sun_family = AF_UNIX;
        strncpy(addr.sun_path, path.c_str(), sizeof addr.sun_path - 1);
        unlink(path.c_str());
        if (bind(host_fd_, reinterpret_cast<sockaddr*>(&addr), sizeof addr) != 0 ||
            chmod(path.c_str(), 0666) != 0 || listen(host_fd_, 16) != 0) {
            loge("host socket %s: %s", path.c_str(), strerror(errno));
            close(host_fd_);
            host_fd_ = -1;
        } else {
            loop_.add(host_fd_, POLLIN, [this](short) { on_accept(host_fd_, true); });
        }
    }
}

void Daemon::stop_services() {
    dns_.stop();
    if (host_fd_ >= 0) {
        loop_.remove(host_fd_);
        close(host_fd_);
        host_fd_ = -1;
        unlink((paths_.run + "/host.sock").c_str());
    }
}

void Daemon::op_spawn(int fd, Conn& c, const json& msg, std::vector<int>& fds) {
    if (!(c.caller.privileged() || c.caller.kind == Caller::App))
        return reply_error(c, "denied", "not allowed");
    json st = read_state(paths_);
    bool app = c.caller.kind == Caller::App;
    if (!is_dir(paths_.rootfs + "/usr") || (app && st.value("state", "") != "installed"))
        return reply_error(c, "not_installed", "the Linux environment is not installed");

    SpawnRequest req;
    req.tty = msg.value("tty", false);
    req.rows = msg.value("rows", 24);
    req.cols = msg.value("cols", 80);
    if (msg.contains("argv") && msg["argv"].is_array()) {
        for (auto& a : msg["argv"]) req.argv.push_back(a.get<std::string>());
    }
    req.cwd = msg.value("cwd", "");
    if (!req.cwd.empty() && req.cwd[0] != '/') return reply_error(c, "bad_request", "cwd must be absolute");

    std::optional<LinuxUser> user;
    if (app) {
        if (msg.contains("user") && msg["user"].is_string())
            return reply_error(c, "denied", "the Terminal runs as its own user");
        if (msg.value("droid", false)) {
            // The droid account shares this Terminal's uid (droidadd.sh); its sessions are the
            // Terminal's too, under the name and home apps for Android's terminal expect.
            std::string code, err;
            user = droid_of(c.caller, &code, &err);
            if (!user) return reply_error(c, code, err);
        } else {
            user = find_user_by_uid(paths_.rootfs, c.caller.uid);
        }
        if (!user) return reply_error(c, "no_user", "no Linux account for this Android user yet");
        req.android_user = c.caller.android_user;
    } else {
        std::string name = msg.value("user", "root");
        user = find_user_by_name(paths_.rootfs, name);
        if (!user) return reply_error(c, "no_user", "no such user: " + name);
        req.android_user = msg.value("android_user", -1);
        // A Terminal account (its uid is that user's Terminal uid) gets its real home here too.
        if (terminal_app_id() && user->uid % kPerUserRange == terminal_app_id())
            req.android_user = static_cast<int>(user->uid / kPerUserRange);
    }
    req.user = *user;

    std::string requested_lang;
    if (msg.contains("env") && msg["env"].is_array()) {
        for (auto& e : msg["env"]) {
            std::string kv = e.get<std::string>();
            size_t eq = kv.find('=');
            if (eq == std::string::npos || !valid_env_name(kv.substr(0, eq)))
                return reply_error(c, "bad_request", "invalid environment entry");
            std::string k = kv.substr(0, eq);
            if (app && !(k == "TERM" || k == "COLORTERM" || k == "LANG" || starts_with(k, "LC_") ||
                         k == "TERM_PROGRAM" || k == "TERM_PROGRAM_VERSION"))
                continue;
            if (k == "LANG") {
                requested_lang = kv.substr(eq + 1);
                continue;
            }
            req.env.push_back(kv);
        }
    }
    // The Terminal follows Android's language; a locale that is not generated yet falls back
    // to C.UTF-8 for this session and is generated in the background for the next one.
    if (!requested_lang.empty()) {
        auto avail = available_locales();
        if (avail.count(requested_lang)) {
            req.env.push_back("LANG=" + requested_lang);
        } else {
            req.env.push_back("LANG=C.UTF-8");
            if (valid_locale(requested_lang) && app) ensure_locale(requested_lang);
        }
    }

    if (msg.contains("binds") && msg["binds"].is_array() && !msg["binds"].empty()) {
        if (!c.caller.privileged()) return reply_error(c, "denied", "binds need root");
        for (auto& b : msg["binds"]) {
            auto f = split(b.get<std::string>(), ':');
            if (f.size() < 2 || f.size() > 3 || f[0].empty() || f[0][0] != '/' || f[1].empty() ||
                f[1][0] != '/' || f[1].find("..") != std::string::npos || f[1] == "/" ||
                (f.size() == 3 && f[2] != "ro" && f[2] != "rw"))
                return reply_error(c, "bad_request", "binds are HOST:GUEST[:ro]");
            req.binds.push_back({f[0], f[1], f.size() == 3 && f[2] == "ro"});
        }
    }
    if (!req.tty) {
        size_t max = c.caller.privileged() ? 4 : 3;
        if (fds.size() < 3 || fds.size() > max)
            // The kernel drops descriptors the service may not use (SELinux file receive): a
            // file of a type gbos_linuxd/gbos_linux are not allowed to read or write.
            return reply_error(c, "bad_request",
                               format("a session without a tty needs stdin, stdout and stderr; %zu "
                                      "arrived (SELinux refuses passing files the Linux "
                                      "environment may not use)", fds.size()));
        req.stdio = fds;
    }

    std::string err;
    if (!ensure_running(&err)) return reply_error(c, "start_failed", err);
    bool terminal_account = terminal_app_id() && req.user.uid % kPerUserRange == terminal_app_id() &&
                            req.android_user >= 0;
    if (terminal_account && !container_.ensure_home(req.android_user, req.user, &err))
        return reply_error(c, "home", err);
    container_.refresh_guest_files();

    pid_t monitor = container_.spawn(c.ch, req, &err);
    if (monitor < 0) return reply_error(c, "spawn_failed", err);
    sessions_[monitor] = req.android_user;
    // The monitor owns the connection and the received descriptors now.
    for (int& f : fds) {
        close(f);
        f = -1;
    }
    drop(fd);
}

void Daemon::fork_job(int fd, const std::function<void(Conn&)>& fn, std::function<void(int)> done) {
    auto it = conns_.find(fd);
    if (it == conns_.end()) return;
    pid_t pid = fork();
    if (pid < 0) {
        reply_error(*it->second, "io", strerror(errno));
        return;
    }
    if (pid == 0) {
        reset_signals();
        int keep = it->second->ch.fd();
        close_fds_except({0, 1, 2, keep});
        fcntl(keep, F_SETFL, 0);
        fn(*it->second);
        _exit(0);
    }
    jobs_[pid] = std::move(done);
    drop(fd);
}

void Daemon::op_useradd(int fd, Conn& c, const json& msg) {
    InstallOptions opts;
    opts.user = msg.value("name", "");
    if (c.caller.kind == Caller::App) {
        opts.user_uid = c.caller.uid;
        opts.android_user = c.caller.android_user;
    } else if (c.caller.privileged()) {
        opts.android_user = msg.value("android_user", -1);
        if (opts.android_user < 0 || terminal_app_id() == 0)
            return reply_error(c, "bad_request", "android_user required (and the Terminal app installed)");
        opts.user_uid = static_cast<uid_t>(opts.android_user) * kPerUserRange + terminal_app_id();
    } else {
        return reply_error(c, "denied", "not allowed");
    }
    if (!valid_user_name(opts.user))
        return reply_error(c, "bad_user", "user names start with a lowercase letter and use a-z, 0-9, _ and -");
    if (read_state(paths_).value("state", "") != "installed")
        return reply_error(c, "not_installed", "the Linux environment is not installed");
    std::string err;
    if (!ensure_running(&err)) return reply_error(c, "start_failed", err);
    Paths paths = paths_;
    fork_job(fd, [paths, opts](Conn& conn) {
        std::string e;
        if (create_user(paths, opts, nullptr, &e)) conn.ch.send({{"ok", true}});
        else conn.ch.send({{"ok", false}, {"code", "user"}, {"error", e}});
    });
}

std::optional<LinuxUser> Daemon::droid_of(const Caller& c, std::string* code, std::string* err) {
    auto droid = find_user_by_name(paths_.rootfs, kDroidAccount);
    if (!droid) {
        *code = "no_droid";
        *err = format("there is no %s account yet", kDroidAccount);
        return std::nullopt;
    }
    if (droid->uid != c.uid || droid->home != std::string("/home/") + kDroidAccount) {
        *code = "droid_taken";
        *err = format("the %s account belongs to another Android user", kDroidAccount);
        return std::nullopt;
    }
    return droid;
}

// The droid account for this Terminal user (apps written for Android's own Linux terminal log
// in as it); only after the user approved such an app's request in the Terminal.
void Daemon::op_droid_account(int fd, Conn& c) {
    if (c.caller.kind != Caller::App) return reply_error(c, "denied", "only for a Terminal user");
    if (read_state(paths_).value("state", "") != "installed")
        return reply_error(c, "not_installed", "the Linux environment is not installed");
    auto owner = find_user_by_uid(paths_.rootfs, c.caller.uid);
    if (!owner) return reply_error(c, "no_user", "no Linux account for this Android user yet");
    std::string err;
    if (!ensure_running(&err)) return reply_error(c, "start_failed", err);
    if (auto existing = find_user_by_name(paths_.rootfs, kDroidAccount)) {
        if (existing->uid != c.caller.uid)
            return reply_error(c, "droid_taken",
                               format("the %s account belongs to another Android user", kDroidAccount));
    }
    Paths paths = paths_;
    std::string name = owner->name;
    uid_t uid = c.caller.uid;
    int android_user = c.caller.android_user;
    fork_job(fd, [paths, name, uid, android_user](Conn& conn) {
        std::string e;
        if (create_droid(paths, name, uid, android_user, &e))
            conn.ch.send({{"ok", true}, {"user", kDroidAccount}, {"home", std::string("/home/") + kDroidAccount}});
        else
            conn.ch.send({{"ok", false}, {"code", "user"}, {"error", e}});
    });
}

// Starts the environment and mounts what the droid account sees: its home and the owner's
// shared storage (/mnt/android/<user>, /storage/emulated/<user>, /mnt/shared).
void Daemon::op_droid_prepare(Conn& c) {
    if (c.caller.kind != Caller::App) return reply_error(c, "denied", "only for a Terminal user");
    std::string code, err;
    auto droid = droid_of(c.caller, &code, &err);
    if (!droid) return reply_error(c, code, err);
    if (!ensure_running(&err)) return reply_error(c, "start_failed", err);
    if (!container_.ensure_home(c.caller.android_user, *droid, &err)) return reply_error(c, "home", err);
    c.ch.send({{"ok", true},
               {"user", droid->name},
               {"home", droid->home},
               {"shared", kDroidShared},
               {"storage", format("%s/%d", kGuestEmulatedStorage, c.caller.android_user)}});
}

// One chunk of a file an app copies into the droid home (Copy & Run): base64 data at offset;
// the last chunk carries final=true and the file mode.
void Daemon::op_droid_file(Conn& c, const json& msg) {
    if (c.caller.kind != Caller::App) return reply_error(c, "denied", "only for a Terminal user");
    std::string code, err;
    auto droid = droid_of(c.caller, &code, &err);
    if (!droid) return reply_error(c, code, err);
    std::string rel = msg.value("path", "");
    std::string problem = droid_path_problem(rel);
    if (!problem.empty()) return reply_error(c, "bad_path", problem);
    std::string data;
    std::string encoded = msg.value("data", "");
    if (encoded.size() > (kDroidChunkMax / 3 + 1) * 4 || !base64_decode(encoded, &data))
        return reply_error(c, "bad_request", "data must be base64, at most one chunk");
    if (!msg.contains("offset") || !msg["offset"].is_number_unsigned())
        return reply_error(c, "bad_request", "offset missing");
    uint64_t offset = msg["offset"].get<uint64_t>();
    bool final = msg.value("final", false);
    int mode = msg.value("mode", 0644);
    if (mode < 0 || mode > 0777) return reply_error(c, "bad_request", "mode is 0..0777");
    if (!ensure_running(&err)) return reply_error(c, "start_failed", err);
    if (!container_.ensure_home(c.caller.android_user, *droid, &err)) return reply_error(c, "home", err);
    if (!container_.write_home_file(*droid, rel, data, offset, final, static_cast<mode_t>(mode), &err))
        return reply_error(c, "write", err);
    json r = {{"ok", true}, {"size", offset + data.size()}};
    if (final) r["path"] = droid->home + "/" + rel;
    c.ch.send(r);
}

void Daemon::op_stop(Conn& c) {
    if (!(c.caller.privileged() || c.caller.kind == Caller::App))
        return reply_error(c, "denied", "not allowed");
    if (installing()) return reply_error(c, "busy", "an installation is running");
    stop_services();
    container_.stop(3000);
    sessions_.clear();
    c.ch.send({{"ok", true}});
}

void Daemon::op_end_user(Conn& c) {
    if (c.caller.kind != Caller::App) return reply_error(c, "denied", "only for a Terminal user");
    // Ends this user's programs only (other users and root jobs keep running).
    auto pids = container_.processes_of(c.caller.uid);
    for (pid_t p : pids) kill(p, SIGTERM);
    for (int i = 0; i < 30 && !container_.processes_of(c.caller.uid).empty(); i++) usleep(100 * 1000);
    for (pid_t p : container_.processes_of(c.caller.uid)) kill(p, SIGKILL);
    c.ch.send({{"ok", true}, {"ended", pids.size()}});
}

void Daemon::op_remove(int fd, Conn& c) {
    if (!(c.caller.privileged() || c.caller.kind == Caller::App))
        return reply_error(c, "denied", "not allowed");
    if (installing()) return reply_error(c, "busy", "an installation is running");
    std::vector<int> homes;  // Android users whose Linux home goes away with the environment
    if (c.caller.kind == Caller::App) {
        // Only when nobody else (another Android user's Terminal) has an account in it.
        if (auto passwd = read_file(paths_.rootfs + "/etc/passwd")) {
            for (auto& line : split(*passwd, '\n')) {
                auto f = split(line, ':');
                if (f.size() < 3) continue;
                uid_t uid = static_cast<uid_t>(strtoul(f[2].c_str(), nullptr, 10));
                if (uid != c.caller.uid && terminal_app_id() && uid % kPerUserRange == terminal_app_id())
                    return reply_error(c, "shared", "another Android user also uses this Linux environment");
            }
        }
        homes.push_back(c.caller.android_user);
    } else if (DIR* d = opendir("/data/misc_ce")) {
        while (struct dirent* e = readdir(d)) {
            char* end;
            long n = strtol(e->d_name, &end, 10);
            if (*end == 0 && e->d_name[0]) homes.push_back(static_cast<int>(n));
        }
        closedir(d);
    }
    stop_services();
    container_.stop(3000);
    sessions_.clear();
    json st = read_state(paths_);
    st["state"] = "removing";
    write_state(paths_, st);
    Paths paths = paths_;
    fork_job(fd, [paths, homes](Conn& conn) {
        // Homes bound for the environment go first (a later install binds them afresh).
        if (DIR* d = opendir(paths.homes.c_str())) {
            while (struct dirent* e = readdir(d)) {
                if (e->d_name[0] == '.') continue;
                std::string h = paths.homes + "/" + e->d_name;
                umount2(h.c_str(), MNT_DETACH);
                rmdir(h.c_str());
            }
            closedir(d);
        }
        bool ok = remove_tree(paths.rootfs) && remove_tree(paths.rootfs + ".new") &&
                  remove_tree(paths.cache);
        for (int u : homes) {
            std::string dir = ce_home_dir(u);
            ok = remove_tree(dir.substr(0, dir.rfind('/'))) && ok;
        }
        if (ok) {
            unlink(paths.state.c_str());
            conn.ch.send({{"ok", true}});
        } else {
            json s = read_state(paths);
            s["state"] = "failed";
            s["error"] = "removal incomplete";
            s["error_code"] = "io";
            write_state(paths, s);
            conn.ch.send({{"ok", false}, {"code", "io"}, {"error", "could not remove everything"}});
        }
    });
}

int Daemon::android_user_of(const Caller& c) {
    if (terminal_app_id() && c.uid % kPerUserRange == terminal_app_id())
        return static_cast<int>(c.uid / kPerUserRange);
    // Walk up to the session that started the process (sudo, scripts, ...).
    pid_t pid = c.pid;
    for (int i = 0; i < 64 && pid > 1; i++) {
        auto it = sessions_.find(pid);
        if (it != sessions_.end() && it->second >= 0) return it->second;
        auto stat = read_file(format("/proc/%d/stat", pid));
        if (!stat) break;
        size_t rp = stat->rfind(')');
        if (rp == std::string::npos) break;
        auto f = split(stat->substr(rp + 2), ' ');
        if (f.size() < 2) break;
        pid = static_cast<pid_t>(atoi(f[1].c_str()));
    }
    std::string out;
    if (run({"/system/bin/cmd", "activity", "get-current-user"}, &out) == 0) {
        out = trim(out);
        if (!out.empty() && isdigit(static_cast<unsigned char>(out[0]))) return atoi(out.c_str());
    }
    return 0;
}

void Daemon::op_open(int fd, Conn& c, const json& msg) {
    std::string url = msg.value("url", ""), path = msg.value("path", "");
    if (url.empty() == path.empty()) return reply_error(c, "bad_request", "url or path");
    for (char ch : url + path)
        if (static_cast<unsigned char>(ch) < 0x20) return reply_error(c, "bad_request", "control characters");
    int user = android_user_of(c.caller);
    std::vector<std::string> argv = {"/system/bin/cmd", "activity", "start-activity", "--user",
                                     std::to_string(user)};
    if (!url.empty()) {
        static const std::regex scheme("^([A-Za-z][A-Za-z0-9+.-]*):.+");
        std::smatch m;
        if (url.size() > 8192 || !std::regex_match(url, m, scheme))
            return reply_error(c, "bad_request", "not a URL");
        std::string s = m[1].str();
        for (auto& ch : s) ch = static_cast<char>(tolower(static_cast<unsigned char>(ch)));
        if (s == "file" || s == "content" || s == "intent" || s == "android-app" || s == "javascript")
            return reply_error(c, "denied", s + ": links are not opened");
        argv.insert(argv.end(), {"-a", "android.intent.action.VIEW", "-c",
                                 "android.intent.category.BROWSABLE", "-d", url});
    } else {
        if (path[0] != '/' || path.find("/../") != std::string::npos || path.size() > 4096)
            return reply_error(c, "bad_request", "absolute path expected");
        std::string host;
        std::string storage = format("%s/%d/emulated/%d", kGuestAndroidStorage, user, user);
        std::optional<LinuxUser> lu;
        if (terminal_app_id())
            lu = find_user_by_uid(paths_.rootfs, static_cast<uid_t>(user) * kPerUserRange + terminal_app_id());
        auto droid = find_user_by_name(paths_.rootfs, kDroidAccount);
        if (droid && lu && droid->uid != lu->uid) droid.reset();
        if (lu && (path == lu->home || starts_with(path, lu->home + "/"))) {
            host = ce_home_dir(user) + path.substr(lu->home.size());
        } else if (droid && (path == droid->home || starts_with(path, droid->home + "/"))) {
            host = ce_droid_dir(user) + path.substr(droid->home.size());
        } else if (path == storage || starts_with(path, storage + "/")) {
            host = format("/storage/emulated/%d", user) + path.substr(storage.size());
        } else if (std::string view = format("%s/%d", kGuestStorageView, user);
                   path == view || starts_with(path, view + "/")) {
            host = format("/storage/emulated/%d", user) + path.substr(view.size());
        } else {
            host = paths_.rootfs + path;
        }
        argv.insert(argv.end(), {"-n", std::string(kTerminalPackage) + "/.OpenFileActivity",
                                 "--es", "path", host});
    }
    fork_job(fd, [argv](Conn& conn) {
        std::string out;
        int st = run(argv, &out);
        if (st != 0 || out.find("Error") != std::string::npos) {
            std::string e = trim(out);
            size_t p = e.find("Error");
            conn.ch.send({{"ok", false}, {"code", "open"}, {"error", p == std::string::npos ? describe_status(st) : e.substr(p)}});
        } else {
            conn.ch.send({{"ok", true}});
        }
    });
}

}  // namespace
}  // namespace gbos

int main() {
    gbos::log_init("gbos-linuxd", getenv("GBOS_LINUX_STDERR") != nullptr);
    gbos::Daemon d;
    return d.serve();
}
