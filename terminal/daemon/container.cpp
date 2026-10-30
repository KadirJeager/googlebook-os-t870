#include "container.h"

#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <grp.h>
#include <poll.h>
#include <sched.h>
#include <signal.h>
#include <stdlib.h>
#include <string.h>
#include <sys/capability.h>
#include <sys/ioctl.h>
#include <sys/mount.h>
#include <sys/prctl.h>
#include <sys/signalfd.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <sys/sysmacros.h>
#include <sys/wait.h>
#include <sys/xattr.h>
#include <termios.h>
#include <unistd.h>

#include <algorithm>
#include <set>

#include "droid.h"

namespace gbos {

namespace {

constexpr const char* kManagedMarker = "# Managed by Googlebook Linux";

// ---- passwd / group parsing

struct PasswdEntry {
    std::string name;
    uid_t uid;
    gid_t gid;
    std::string home, shell;
};

std::vector<PasswdEntry> parse_passwd(const std::string& text) {
    std::vector<PasswdEntry> out;
    for (auto& line : split(text, '\n')) {
        if (line.empty() || line[0] == '#') continue;
        auto f = split(line, ':');
        if (f.size() < 7) continue;
        char* end = nullptr;
        unsigned long uid = strtoul(f[2].c_str(), &end, 10);
        if (*end) continue;
        unsigned long gid = strtoul(f[3].c_str(), &end, 10);
        if (*end) continue;
        out.push_back({f[0], static_cast<uid_t>(uid), static_cast<gid_t>(gid), f[5], f[6]});
    }
    return out;
}

std::vector<gid_t> member_groups(const std::string& group_text, const std::string& user) {
    std::vector<gid_t> out;
    for (auto& line : split(group_text, '\n')) {
        if (line.empty() || line[0] == '#') continue;
        auto f = split(line, ':');
        if (f.size() < 4) continue;
        for (auto& m : split(f[3], ',')) {
            if (m == user) {
                out.push_back(static_cast<gid_t>(strtoul(f[2].c_str(), nullptr, 10)));
                break;
            }
        }
    }
    return out;
}

// Runs fn in a child whose root is the environment (setns when running, else chroot), so
// symlinks planted inside can never redirect daemon file operations onto Android's files.
// The child's return value is the exit code; data written to the pipe fd comes back in *out.
int in_guest(const Paths& paths, int mnt_fd, const std::function<int(int out_fd)>& fn,
             std::string* out = nullptr) {
    int p[2];
    if (pipe2(p, O_CLOEXEC) != 0) return -1;
    pid_t pid = fork();
    if (pid < 0) {
        close(p[0]);
        close(p[1]);
        return -1;
    }
    if (pid == 0) {
        close(p[0]);
        if (mnt_fd >= 0) {
            if (setns(mnt_fd, CLONE_NEWNS) != 0) _exit(120);
        } else {
            if (chroot(paths.rootfs.c_str()) != 0) _exit(121);
        }
        if (chdir("/") != 0) _exit(122);
        _exit(fn(p[1]));
    }
    close(p[1]);
    std::string data;
    char buf[8192];
    ssize_t n;
    while ((n = TEMP_FAILURE_RETRY(read(p[0], buf, sizeof buf))) > 0) data.append(buf, n);
    close(p[0]);
    int status = 0;
    if (TEMP_FAILURE_RETRY(waitpid(pid, &status, 0)) < 0) return -1;
    if (out) *out = std::move(data);
    return WIFEXITED(status) ? WEXITSTATUS(status) : -1;
}

bool write_all(int fd, const std::string& s) {
    size_t off = 0;
    while (off < s.size()) {
        ssize_t n = TEMP_FAILURE_RETRY(write(fd, s.data() + off, s.size() - off));
        if (n <= 0) return false;
        off += n;
    }
    return true;
}

std::optional<std::string> guest_read(const Paths& paths, int mnt_fd, const std::string& path) {
    std::string data;
    int rc = in_guest(paths, mnt_fd, [&](int out) {
        auto content = read_file(path);
        if (!content) return 1;
        return write_all(out, *content) ? 0 : 2;
    }, &data);
    if (rc != 0) return std::nullopt;
    return data;
}

void set_selinux_label(const std::string& path, const char* label) {
    // Fails harmlessly when the policy does not know the type yet.
    lsetxattr(path.c_str(), "security.selinux", label, strlen(label) + 1, 0);
}

bool mount_or_err(const char* src, const std::string& target, const char* type,
                  unsigned long flags, const char* data, std::string* err) {
    if (mount(src, target.c_str(), type, flags, data) != 0) {
        *err = format("mount %s on %s: %s", src ? src : "(none)", target.c_str(),
                      strerror(errno));
        return false;
    }
    return true;
}

// Mounts private to the environment carry the environment's own SELinux types (the pty devices
// are shared with the Terminal app, see 60-gbos-linux.cil). Without that policy loaded the
// kernel rejects the context option, and the mount is made with the filesystem's default label.
bool mount_labeled(const char* src, const std::string& target, const char* type,
                   unsigned long flags, const std::string& data, const char* label,
                   std::string* err) {
    std::string with = data + (data.empty() ? "" : ",") + "context=" + label;
    if (mount(src, target.c_str(), type, flags, with.c_str()) == 0) return true;
    if (errno != EINVAL) {
        *err = format("mount %s on %s: %s", src, target.c_str(), strerror(errno));
        return false;
    }
    return mount_or_err(src, target, type, flags, data.c_str(), err);
}

constexpr const char* kTmpfsLabel = "u:object_r:gbos_linux_tmpfs:s0";
constexpr const char* kDevptsLabel = "u:object_r:gbos_linux_devpts:s0";

// A mount point inside the root filesystem: must be a real directory, never a symlink
// (mount(2) follows symlinks in the target).
bool mount_dir(const std::string& path, mode_t mode, std::string* err) {
    struct stat st;
    if (lstat(path.c_str(), &st) == 0) {
        if (S_ISDIR(st.st_mode)) return true;
        if (unlink(path.c_str()) != 0) {
            *err = format("%s is not a directory and cannot be replaced: %s", path.c_str(),
                          strerror(errno));
            return false;
        }
    }
    if (mkdir(path.c_str(), mode) != 0 && errno != EEXIST) {
        *err = format("mkdir %s: %s", path.c_str(), strerror(errno));
        return false;
    }
    return true;
}

bool is_mount_point(const std::string& path) {
    auto mi = read_file("/proc/self/mountinfo");
    if (!mi) return false;
    for (auto& line : split(*mi, '\n')) {
        auto f = split(line, ' ');
        if (f.size() > 4 && f[4] == path) return true;
    }
    return false;
}

std::string random_token() {
    unsigned char b[8] = {};
    int fd = open("/dev/urandom", O_RDONLY | O_CLOEXEC);
    if (fd >= 0) {
        if (read(fd, b, sizeof b) != sizeof b) {}
        close(fd);
    }
    std::string t;
    for (unsigned char c : b) t += format("%02x", c);
    return t;
}

std::string ns_id(int fd) {
    struct stat st;
    if (fstat(fd, &st) != 0) return {};
    return format("mnt:[%lu]", static_cast<unsigned long>(st.st_ino));
}

std::vector<pid_t> all_pids() {
    std::vector<pid_t> pids;
    DIR* d = opendir("/proc");
    if (!d) return pids;
    while (struct dirent* e = readdir(d)) {
        char* end;
        long pid = strtol(e->d_name, &end, 10);
        if (*end == 0 && pid > 0) pids.push_back(static_cast<pid_t>(pid));
    }
    closedir(d);
    return pids;
}

const int kKeptCaps[] = {
    CAP_CHOWN, CAP_DAC_OVERRIDE, CAP_FSETID, CAP_FOWNER, CAP_NET_RAW, CAP_SETGID, CAP_SETUID,
    CAP_SETFCAP, CAP_SETPCAP, CAP_NET_BIND_SERVICE, CAP_SYS_CHROOT, CAP_KILL, CAP_AUDIT_WRITE,
};

bool search_path(const std::string& file, const std::string& path_env, std::string* found) {
    if (file.find('/') != std::string::npos) {
        *found = file;
        return true;
    }
    for (auto& dir : split(path_env, ':')) {
        std::string candidate = (dir.empty() ? "." : dir) + "/" + file;
        if (access(candidate.c_str(), X_OK) == 0) {
            struct stat st;
            if (stat(candidate.c_str(), &st) == 0 && S_ISREG(st.st_mode)) {
                *found = candidate;
                return true;
            }
        }
    }
    return false;
}

std::string env_value(const std::vector<std::string>& env, const std::string& key) {
    for (auto it = env.rbegin(); it != env.rend(); ++it) {
        if (starts_with(*it, key + "=")) return it->substr(key.size() + 1);
    }
    return {};
}

void set_env(std::vector<std::string>* env, const std::string& key, const std::string& value) {
    for (auto& e : *env) {
        if (starts_with(e, key + "=")) {
            e = key + "=" + value;
            return;
        }
    }
    env->push_back(key + "=" + value);
}

}  // namespace

bool drop_capabilities_for_guest() {
    std::set<int> keep(std::begin(kKeptCaps), std::end(kKeptCaps));
    for (int cap = 0; cap <= 63; cap++) {
        if (keep.count(cap)) continue;
        if (prctl(PR_CAPBSET_DROP, cap, 0, 0, 0) != 0 && errno != EINVAL) return false;
    }
    prctl(PR_CAP_AMBIENT, PR_CAP_AMBIENT_CLEAR_ALL, 0, 0, 0);
    // Root keeps (inheritable | bounding) across execve: limit every set to the kept caps.
    struct __user_cap_header_struct hdr = {_LINUX_CAPABILITY_VERSION_3, 0};
    struct __user_cap_data_struct data[2] = {};
    if (syscall(SYS_capget, &hdr, data) != 0) return false;
    uint64_t mask = 0;
    for (int cap : kKeptCaps) mask |= 1ULL << cap;
    for (int i = 0; i < 2; i++) {
        uint32_t m = static_cast<uint32_t>(mask >> (32 * i));
        data[i].effective &= m;
        data[i].permitted &= m;
        data[i].inheritable = 0;
    }
    return syscall(SYS_capset, &hdr, data) == 0;
}

bool valid_user_name(const std::string& name) {
    if (name.empty() || name.size() > 32) return false;
    if (!(islower(static_cast<unsigned char>(name[0])) || name[0] == '_')) return false;
    for (char c : name) {
        if (!(islower(static_cast<unsigned char>(c)) || isdigit(static_cast<unsigned char>(c)) ||
              c == '_' || c == '-'))
            return false;
    }
    return true;
}

namespace {
std::optional<LinuxUser> find_user(const Paths& paths, int mnt_fd,
                                   const std::function<bool(const PasswdEntry&)>& match) {
    auto passwd = guest_read(paths, mnt_fd, "/etc/passwd");
    if (!passwd) return std::nullopt;
    for (auto& e : parse_passwd(*passwd)) {
        if (!match(e)) continue;
        LinuxUser u;
        u.name = e.name;
        u.uid = e.uid;
        u.gid = e.gid;
        u.home = e.home.empty() ? "/" : e.home;
        u.shell = e.shell.empty() ? "/bin/sh" : e.shell;
        if (auto group = guest_read(paths, mnt_fd, "/etc/group")) {
            u.groups = member_groups(*group, e.name);
        }
        return u;
    }
    return std::nullopt;
}
}  // namespace

std::optional<LinuxUser> find_user_by_uid(const std::string& rootfs, uid_t uid) {
    Paths p = Paths::get();
    p.rootfs = rootfs;
    return find_user(p, -1, [uid](const PasswdEntry& e) { return e.uid == uid && e.name != kDroidAccount; });
}

std::optional<LinuxUser> find_user_by_name(const std::string& rootfs, const std::string& name) {
    Paths p = Paths::get();
    p.rootfs = rootfs;
    return find_user(p, -1, [&name](const PasswdEntry& e) { return e.name == name; });
}

// ---- Container

Container::~Container() {
    if (mnt_fd_ >= 0) close(mnt_fd_);
    if (uts_fd_ >= 0) close(uts_fd_);
}

bool Container::rootfs_identity(dev_t* dev, ino_t* ino) const {
    struct stat st;
    if (stat(paths_.rootfs.c_str(), &st) != 0) return false;
    *dev = st.st_dev;
    *ino = st.st_ino;
    return true;
}

bool Container::prepare_host_mounts(std::string* err) {
    // base is private in Android's namespace, so nothing below it reaches app namespaces.
    if (!is_mount_point(paths_.base) &&
        mount(paths_.base.c_str(), paths_.base.c_str(), nullptr, MS_BIND, nullptr) != 0) {
        *err = format("bind %s: %s", paths_.base.c_str(), strerror(errno));
        return false;
    }
    if (mount(nullptr, paths_.base.c_str(), nullptr, MS_PRIVATE, nullptr) != 0) {
        *err = format("private %s: %s", paths_.base.c_str(), strerror(errno));
        return false;
    }
    for (auto& [dir, mode] : std::vector<std::pair<std::string, mode_t>>{
             {paths_.homes, 0755}, {paths_.exchange, 0700}}) {
        if (!mkdirs(dir, mode)) {
            *err = format("mkdir %s: %s", dir.c_str(), strerror(errno));
            return false;
        }
        if (!is_mount_point(dir) && mount(dir.c_str(), dir.c_str(), nullptr, MS_BIND, nullptr) != 0) {
            *err = format("bind %s: %s", dir.c_str(), strerror(errno));
            return false;
        }
        if (mount(nullptr, dir.c_str(), nullptr, MS_SHARED, nullptr) != 0) {
            *err = format("shared %s: %s", dir.c_str(), strerror(errno));
            return false;
        }
    }
    return true;
}

bool Container::start(std::string* err) {
    if (running()) return true;
    if (!is_dir(paths_.rootfs + "/usr")) {
        *err = "the Linux environment is not installed";
        return false;
    }
    if (!mkdirs(paths_.run, 0755)) {
        *err = format("mkdir %s: %s", paths_.run.c_str(), strerror(errno));
        return false;
    }
    int sp[2];
    if (socketpair(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0, sp) != 0) {
        *err = format("socketpair: %s", strerror(errno));
        return false;
    }
    pid_t pid = fork();
    if (pid < 0) {
        *err = format("fork: %s", strerror(errno));
        close(sp[0]);
        close(sp[1]);
        return false;
    }
    if (pid == 0) {
        close(sp[0]);
        reset_signals();
        Channel ch(sp[1]);
        std::string e;
        auto fail = [&](const std::string& msg) {
            ch.send({{"ok", false}, {"error", msg}});
            _exit(1);
        };
        const std::string& root = paths_.rootfs;
        if (unshare(CLONE_NEWNS | CLONE_NEWUTS) != 0) fail(format("unshare: %s", strerror(errno)));
        // Nothing done here may propagate back to Android; Android's mounts (storage coming
        // and going) still propagate in.
        if (!mount_or_err(nullptr, "/", nullptr, MS_REC | MS_SLAVE, nullptr, &e)) fail(e);
        if (sethostname(kHostname, strlen(kHostname)) != 0)
            fail(format("sethostname: %s", strerror(errno)));
        // The root must be a mount point for pivot_root. /data is nosuid; this bind is not,
        // so sudo and other setuid programs work. Device nodes only exist on our own /dev.
        if (!mount_or_err(root.c_str(), root, nullptr, MS_BIND, nullptr, &e)) fail(e);
        if (!mount_or_err(nullptr, root, nullptr, MS_REMOUNT | MS_BIND | MS_NODEV, nullptr, &e))
            fail(e);
        for (auto& [dir, mode] : std::vector<std::pair<std::string, mode_t>>{
                 {"/proc", 0555}, {"/sys", 0555}, {"/dev", 0755}, {"/run", 0755},
                 {"/tmp", 01777}, {"/mnt", 0755}, {"/home", 0755}}) {
            if (!mount_dir(root + dir, mode, &e)) fail(e);
        }
        // /home: the homes mount point (DE storage); each Terminal user's credential-encrypted
        // home is bound there in Android's namespace and propagates in.
        if (!mount_or_err(paths_.homes.c_str(), root + "/home", nullptr, MS_BIND | MS_REC, nullptr, &e) ||
            !mount_or_err(nullptr, root + "/home", nullptr, MS_REC | MS_SLAVE, nullptr, &e))
            fail(e);
        // Like Android's own /proc: other users' processes stay invisible (on kernels before 5.8
        // this mount shares Android's procfs instance and its hidepid=2 anyway).
        if (!mount_or_err("proc", root + "/proc", "proc", MS_NOSUID | MS_NODEV | MS_NOEXEC,
                          "hidepid=2", &e))
            fail(e);
        if (!mount_or_err("sysfs", root + "/sys", "sysfs",
                          MS_RDONLY | MS_NOSUID | MS_NODEV | MS_NOEXEC, nullptr, &e))
            fail(e);
        // /dev: a private tmpfs with the usual character devices and a private devpts.
        std::string dev = root + "/dev";
        if (!mount_labeled("tmpfs", dev, "tmpfs", MS_NOSUID | MS_NOEXEC, "mode=755,size=1m",
                           kTmpfsLabel, &e))
            fail(e);
        struct {
            const char* name;
            unsigned maj, min;
        } nodes[] = {{"null", 1, 3},   {"zero", 1, 5},    {"full", 1, 7},
                     {"random", 1, 8}, {"urandom", 1, 9}, {"tty", 5, 0}};
        for (auto& n : nodes) {
            std::string p = dev + "/" + n.name;
            if (mknod(p.c_str(), S_IFCHR | 0666, makedev(n.maj, n.min)) != 0 ||
                chmod(p.c_str(), 0666) != 0)
                fail(format("mknod %s: %s", p.c_str(), strerror(errno)));
        }
        mkdir((dev + "/pts").c_str(), 0755);
        mkdir((dev + "/shm").c_str(), 01777);
        if (!mount_labeled("devpts", dev + "/pts", "devpts", MS_NOSUID | MS_NOEXEC,
                           "newinstance,ptmxmode=0666,mode=0620,gid=5", kDevptsLabel, &e))
            fail(e);
        if (!mount_labeled("tmpfs", dev + "/shm", "tmpfs", MS_NOSUID | MS_NODEV, "mode=1777",
                           kTmpfsLabel, &e))
            fail(e);
        symlink("pts/ptmx", (dev + "/ptmx").c_str());
        symlink("/proc/self/fd", (dev + "/fd").c_str());
        symlink("/proc/self/fd/0", (dev + "/stdin").c_str());
        symlink("/proc/self/fd/1", (dev + "/stdout").c_str());
        symlink("/proc/self/fd/2", (dev + "/stderr").c_str());
        // /run is volatile as on any Debian system; /tmp is a tmpfs as on Debian 13.
        std::string run = root + "/run";
        if (!mount_labeled("tmpfs", run, "tmpfs", MS_NOSUID | MS_NODEV, "mode=755,size=10%",
                           kTmpfsLabel, &e))
            fail(e);
        mkdir((run + "/lock").c_str(), 01777);
        chmod((run + "/lock").c_str(), 01777);
        mkdir((run + "/user").c_str(), 0755);
        mkdir((run + "/googlebook").c_str(), 0755);
        if (!mount_or_err(paths_.run.c_str(), run + "/googlebook", nullptr, MS_BIND, nullptr, &e))
            fail(e);
        std::string exchange = root + kGuestExchange;
        mkdir(exchange.c_str(), 0700);
        if (!mount_or_err(paths_.exchange.c_str(), exchange, nullptr, MS_BIND | MS_REC, nullptr, &e) ||
            !mount_or_err(nullptr, exchange, nullptr, MS_REC | MS_SLAVE, nullptr, &e))
            fail(e);
        if (!mount_labeled("tmpfs", root + "/tmp", "tmpfs", MS_NOSUID | MS_NODEV,
                           "mode=1777,size=50%", kTmpfsLabel, &e))
            fail(e);
        // Android's shared storage. /mnt/user (root:media_rw 0750) comes in whole, followed
        // as storage mounts and unmounts; each Android user's part is bound at
        // /mnt/android/<user> when its Terminal first opens a session (ensure_home), like the
        // /storage view Android gives that user's apps.
        std::string users = root + kGuestAndroidUsers;
        if (!mount_dir(users, 0700, &e)) fail(e);
        if (is_dir("/mnt/user")) {
            if (!mount_or_err("/mnt/user", users, nullptr, MS_BIND | MS_REC, nullptr, &e)) fail(e);
            if (!mount_or_err(nullptr, users, nullptr, MS_REC | MS_SLAVE, nullptr, &e)) fail(e);
        }
        std::string android = root + kGuestAndroidStorage;
        if (!mount_dir(android, 0755, &e)) fail(e);
        if (!mount_labeled("tmpfs", android, "tmpfs", MS_NOSUID | MS_NODEV | MS_NOEXEC,
                           "mode=755,size=64k", kTmpfsLabel, &e))
            fail(e);
        // Switch roots and forget Android's root entirely.
        if (chdir(root.c_str()) != 0) fail(format("chdir: %s", strerror(errno)));
        mkdir(".oldroot", 0700);
        if (syscall(SYS_pivot_root, ".", ".oldroot") != 0)
            fail(format("pivot_root: %s", strerror(errno)));
        if (chdir("/") != 0) fail(format("chdir /: %s", strerror(errno)));
        if (umount2("/.oldroot", MNT_DETACH) != 0)
            fail(format("umount old root: %s", strerror(errno)));
        rmdir("/.oldroot");
        int mnt = open("/proc/self/ns/mnt", O_RDONLY | O_CLOEXEC);
        int uts = open("/proc/self/ns/uts", O_RDONLY | O_CLOEXEC);
        if (mnt < 0 || uts < 0) fail(format("open namespaces: %s", strerror(errno)));
        ch.send({{"ok", true}}, {mnt, uts});
        _exit(0);
    }
    close(sp[1]);
    Channel ch(sp[0]);
    json msg;
    std::vector<int> fds;
    bool got = ch.recv(&msg, &fds);
    int status = 0;
    waitpid(pid, &status, 0);
    if (!got || !msg.value("ok", false) || fds.size() != 2) {
        for (int fd : fds) close(fd);
        *err = got ? msg.value("error", std::string("setup failed"))
                   : "environment setup process died: " + describe_status(status);
        return false;
    }
    mnt_fd_ = fds[0];
    uts_fd_ = fds[1];
    logi("environment started (%s)", ns_id(mnt_fd_).c_str());
    refresh_guest_files();
    return true;
}

bool Container::adopt() {
    if (running()) return true;
    dev_t dev;
    ino_t ino;
    if (!rootfs_identity(&dev, &ino)) return false;
    std::string own = format("/proc/%d/ns/mnt", getpid());
    char own_ns[64] = {};
    readlink(own.c_str(), own_ns, sizeof own_ns - 1);
    for (pid_t pid : all_pids()) {
        struct stat st;
        if (stat(format("/proc/%d/root", pid).c_str(), &st) != 0) continue;
        if (st.st_dev != dev || st.st_ino != ino) continue;
        char ns[64] = {};
        if (readlink(format("/proc/%d/ns/mnt", pid).c_str(), ns, sizeof ns - 1) <= 0) continue;
        if (!strcmp(ns, own_ns)) continue;  // chrooted, not inside the environment
        int mnt = open(format("/proc/%d/ns/mnt", pid).c_str(), O_RDONLY | O_CLOEXEC);
        int uts = open(format("/proc/%d/ns/uts", pid).c_str(), O_RDONLY | O_CLOEXEC);
        if (mnt < 0 || uts < 0) {
            if (mnt >= 0) close(mnt);
            if (uts >= 0) close(uts);
            continue;
        }
        mnt_fd_ = mnt;
        uts_fd_ = uts;
        logi("adopted running environment (%s) from pid %d", ns_id(mnt_fd_).c_str(), pid);
        return true;
    }
    return false;
}

std::vector<pid_t> Container::processes() const {
    std::vector<pid_t> out;
    if (!running()) return out;
    std::string id = ns_id(mnt_fd_);
    for (pid_t pid : all_pids()) {
        char ns[64] = {};
        if (readlink(format("/proc/%d/ns/mnt", pid).c_str(), ns, sizeof ns - 1) <= 0) continue;
        if (id == ns) out.push_back(pid);
    }
    return out;
}

std::vector<pid_t> Container::processes_of(uid_t uid) const {
    std::vector<pid_t> out;
    for (pid_t pid : processes()) {
        auto st = read_file(format("/proc/%d/status", pid));
        if (!st) continue;
        size_t p = st->find("\nUid:");
        if (p == std::string::npos) continue;
        if (static_cast<uid_t>(strtoul(st->c_str() + p + 5, nullptr, 10)) != uid) continue;
        // The storage view's bindfs runs as the user but is the environment's, not a program
        // of the user's: it neither keeps the Terminal in the foreground nor ends with "Stop".
        auto cmd = read_file(format("/proc/%d/cmdline", pid), 256);
        if (cmd && cmd->compare(0, strlen(kStorageViewName) + 1, std::string(kStorageViewName) + '\0') == 0) continue;
        out.push_back(pid);
    }
    return out;
}

void Container::stop(int grace_ms) {
    if (!running()) return;
    auto pids = processes();
    for (pid_t p : pids) kill(p, SIGTERM);
    // Exited processes become zombies without a namespace and drop out of processes(); the
    // daemon reaps them (it is the child subreaper) from its SIGCHLD handler.
    for (int waited = 0; waited < grace_ms && !processes().empty(); waited += 100) {
        usleep(100 * 1000);
    }
    for (pid_t p : processes()) kill(p, SIGKILL);
    for (int i = 0; i < 40 && !processes().empty(); i++) usleep(50 * 1000);
    close(mnt_fd_);
    close(uts_fd_);
    mnt_fd_ = uts_fd_ = -1;
    logi("environment stopped (%zu processes)", pids.size());
}

bool Container::enter(std::string* err) const {
    if (setns(mnt_fd_, CLONE_NEWNS) != 0 || setns(uts_fd_, CLONE_NEWUTS) != 0) {
        *err = format("setns: %s", strerror(errno));
        return false;
    }
    if (chdir("/") != 0) {
        *err = format("chdir: %s", strerror(errno));
        return false;
    }
    return true;
}

void Container::refresh_guest_files() {
    std::string tz = get_property("persist.sys.timezone");
    std::string helper;
    if (auto h = read_file(paths_.sysdir + "/bin/googlebook-open")) helper = std::move(*h);
    // Root tools the image provides for the environment (/usr/local/sbin), kept current here.
    std::vector<std::pair<std::string, std::string>> sbin;
    if (DIR* d = opendir((paths_.sysdir + "/sbin").c_str())) {
        while (struct dirent* e = readdir(d)) {
            if (e->d_name[0] == '.') continue;
            if (auto content = read_file(paths_.sysdir + "/sbin/" + e->d_name))
                sbin.emplace_back(e->d_name, std::move(*content));
        }
        closedir(d);
    }
    // Debian packages the image carries for the environment, where setup.sh and droidadd.sh
    // install them from (apt resolves their dependencies from Debian's archive).
    std::vector<std::pair<std::string, std::string>> debs;
    if (DIR* d = opendir((paths_.sysdir + "/debs").c_str())) {
        while (struct dirent* e = readdir(d)) {
            if (e->d_name[0] == '.') continue;
            if (auto content = read_file(paths_.sysdir + "/debs/" + e->d_name, 64 << 20))
                debs.emplace_back(e->d_name, std::move(*content));
        }
        closedir(d);
    }
    in_guest(paths_, mnt_fd_, [&](int) {
        auto managed = [](const std::string& path) {
            auto cur = read_file(path);
            return !cur || cur->find(kManagedMarker) != std::string::npos;
        };
        auto update = [](const std::string& path, const std::string& content, mode_t mode) {
            auto cur = read_file(path);
            if (cur && *cur == content) return;
            if (!write_file_atomic(path, content, mode))
                logw("cannot write %s: %s", path.c_str(), strerror(errno));
        };
        update("/etc/hostname", std::string(kHostname) + "\n", 0644);
        if (managed("/etc/hosts")) {
            update("/etc/hosts",
                   std::string(kManagedMarker) + "; remove this line to edit the file yourself.\n"
                   "127.0.0.1\tlocalhost\n127.0.1.1\t" + kHostname + "\n"
                   "::1\t\tlocalhost ip6-localhost ip6-loopback\n"
                   "ff02::1\t\tip6-allnodes\nff02::2\t\tip6-allrouters\n", 0644);
        }
        // Name resolution goes through Android's resolver (Private DNS, VPN, per-network
        // servers) via the daemon's forwarder.
        struct stat st;
        if (lstat("/etc/resolv.conf", &st) != 0 || S_ISREG(st.st_mode)) {
            if (managed("/etc/resolv.conf")) {
                update("/etc/resolv.conf",
                       std::string(kManagedMarker) + "; remove this line to edit the file yourself.\n"
                       "nameserver " + kDnsAddress + "\noptions edns0\n", 0644);
            }
        }
        if (!tz.empty() && tz.find("..") == std::string::npos) {
            std::string zone = "/usr/share/zoneinfo/" + tz;
            char cur[PATH_MAX] = {};
            ssize_t n = readlink("/etc/localtime", cur, sizeof cur - 1);
            if (access(zone.c_str(), R_OK) == 0 && (n < 0 || zone != std::string(cur, n))) {
                unlink("/etc/localtime.gbos");
                if (symlink(zone.c_str(), "/etc/localtime.gbos") == 0)
                    rename("/etc/localtime.gbos", "/etc/localtime");
                update("/etc/timezone", tz + "\n", 0644);
            }
        }
        if (!helper.empty()) {
            mkdirs("/usr/local/bin", 0755);
            update("/usr/local/bin/xdg-open", helper, 0755);
        }
        if (!sbin.empty()) mkdirs("/usr/local/sbin", 0755);
        for (auto& [name, content] : sbin) update("/usr/local/sbin/" + name, content, 0755);
        if (!debs.empty()) mkdirs("/var/cache/googlebook/debs", 0755);
        for (auto& [name, content] : debs) update("/var/cache/googlebook/debs/" + name, content, 0644);
        return 0;
    });
}

bool Container::ensure_home(int android_user, const LinuxUser& user, std::string* err) {
    if (!running()) {
        *err = "environment not running";
        return false;
    }
    // Terminal accounts live in /home/<name>, the mount point the homes propagate to.
    if (user.home != "/home/" + user.name || !valid_user_name(user.name)) {
        *err = "the home directory of " + user.name + " must be /home/" + user.name;
        return false;
    }
    std::string user_ce = format("/data/misc_ce/%d", android_user);
    if (!is_dir(user_ce)) {
        *err = format("storage of Android user %d is not available (user locked?)", android_user);
        return false;
    }
    std::string ce = user.name == kDroidAccount ? ce_droid_dir(android_user) : ce_home_dir(android_user);
    std::string base = ce.substr(0, ce.rfind('/'));
    bool fresh = !is_dir(ce);
    if (!mkdirs(base, 0711) || (fresh && mkdir(ce.c_str(), 0700) != 0 && errno != EEXIST)) {
        *err = format("cannot create %s: %s", ce.c_str(), strerror(errno));
        return false;
    }
    if (fresh) {
        set_selinux_label(base, "u:object_r:gbos_linux_data_file:s0");
        set_selinux_label(ce, "u:object_r:gbos_linux_data_file:s0");
    }
    struct stat st;
    if (lstat(ce.c_str(), &st) != 0 || !S_ISDIR(st.st_mode)) {
        *err = ce + " is not a directory";
        return false;
    }
    if (st.st_uid != user.uid || st.st_gid != user.gid) {
        // The home belongs to this Android user's Terminal uid; repair after a uid change.
        lchown(ce.c_str(), user.uid, user.gid);
    }
    std::string target = user.home;
    std::string host = paths_.homes + "/" + user.name;
    if (!is_mount_point(host)) {
        if (lstat(host.c_str(), &st) == 0 && !S_ISDIR(st.st_mode)) unlink(host.c_str());
        if (mkdir(host.c_str(), 0700) != 0 && errno != EEXIST) {
            *err = format("mkdir %s: %s", host.c_str(), strerror(errno));
            return false;
        }
        if (mount(ce.c_str(), host.c_str(), nullptr, MS_BIND, nullptr) != 0 ||
            mount(nullptr, host.c_str(), nullptr, MS_REMOUNT | MS_BIND | MS_NOSUID | MS_NODEV,
                  nullptr) != 0) {
            *err = format("bind %s: %s", host.c_str(), strerror(errno));
            return false;
        }
    }
    // This user's shared storage at /mnt/android/<user> (a bind inside the environment of its
    // own copy of /mnt/user/<user>, so it keeps following the FUSE mount).
    std::string storage_src = format("%s/%d", kGuestAndroidUsers, android_user);
    std::string storage = format("%s/%d", kGuestAndroidStorage, android_user);
    std::string view = format("%s/%d", kGuestStorageView, android_user);
    std::string raw = format("%s/%d/emulated/%d", kGuestAndroidStorage, android_user, android_user);
    in_guest(paths_, mnt_fd_, [&](int) {
        if (!is_mount_point(storage) && is_dir(storage_src)) {
            mkdir(storage.c_str(), 0755);
            if (mount(storage_src.c_str(), storage.c_str(), nullptr, MS_BIND | MS_REC, nullptr) != 0 ||
                mount(nullptr, storage.c_str(), nullptr, MS_REC | MS_SLAVE, nullptr) != 0) {
                logw("bind %s: %s", storage.c_str(), strerror(errno));
                return 1;
            }
        }
        return 0;
    });
    std::string view_err;
    if (!ensure_storage_view(android_user, user, &view_err)) logw("%s", view_err.c_str());
    // Android's own name for it, /storage/emulated/<user>, and ~/android lead to the view. A
    // link made by an earlier version (to the raw mount) moves; anything else someone made
    // there is left alone.
    std::string emulated = format("%s/%d", kGuestEmulatedStorage, android_user);
    std::string home_link = user.home + "/android";
    in_guest(paths_, mnt_fd_, [&](int) {
        mkdirs(kGuestEmulatedStorage, 0755);
        for (const std::string& link : {emulated, home_link}) {
            char cur[PATH_MAX] = {};
            ssize_t n = readlink(link.c_str(), cur, sizeof cur - 1);
            struct stat lst;
            bool absent = lstat(link.c_str(), &lst) != 0 && errno == ENOENT;
            if (!absent && !(n > 0 && std::string(cur, n) == raw)) continue;
            if (link == home_link && absent) continue;  // a new home gets it from skel below
            std::string tmp = link + ".gbos-link";
            unlink(tmp.c_str());
            if (symlink(view.c_str(), tmp.c_str()) != 0 || rename(tmp.c_str(), link.c_str()) != 0) {
                logw("link %s: %s", link.c_str(), strerror(errno));
                unlink(tmp.c_str());
                continue;
            }
            if (link == home_link) lchown(link.c_str(), user.uid, user.gid);
        }
        return 0;
    });
    // A new (or wiped) home starts from /etc/skel, like any new Debian account.
    DIR* d = opendir(ce.c_str());
    bool empty = true;
    if (d) {
        while (struct dirent* e = readdir(d)) {
            if (strcmp(e->d_name, ".") && strcmp(e->d_name, "..")) {
                empty = false;
                break;
            }
        }
        closedir(d);
    }
    if (empty) {
        std::string owner = format("%u:%u", user.uid, user.gid);
        std::string link = view;
        int status = run_root({"/bin/sh", "-c",
                               "cp -rT /etc/skel \"$1\" && ln -sfn \"$3\" \"$1/android\" && "
                               "chown -hR \"$2\" \"$1\"",
                               "sh", target, owner, link},
                              -1);
        if (status != 0) logw("populating %s: %s", target.c_str(), describe_status(status).c_str());
    }
    return true;
}

namespace {
// Type of the mount at `path` inside this namespace ("" if none), from /proc/self/mountinfo.
std::string mount_type(const std::string& path) {
    auto info = read_file("/proc/self/mountinfo");
    if (!info) return {};
    std::string type;
    for (auto& line : split(*info, '\n')) {
        auto f = split(line, ' ');
        if (f.size() < 10 || f[4] != path) continue;
        for (size_t i = 6; i + 1 < f.size(); i++) {
            if (f[i] == "-") {
                type = f[i + 1];  // the last mount on the path is the visible one
                break;
            }
        }
    }
    return type;
}
}  // namespace

// The user's shared storage as Linux sees it, /mnt/storage/<user>: Android reports every file
// there as rw-rw---- (MediaProvider's FUSE, chmod has no effect) and mounts it noexec for apps,
// so nothing kept there could run. Android's own Linux terminal shares it into its VM with
// owner and mode mapped (crosvm virtio-fs, uid/gid + mask), and apps written for it rely on
// that (Antigravity runs its language server shim from the project folder; ./gradlew). The
// same mapping here is bindfs over the raw mount: owned by the user, rwx for the user and
// nobody else, chmod/chown accepted and ignored. bindfs runs as the user itself, so every
// access still goes through MediaProvider with the Terminal's own rights (its All files
// access); the service only makes the FUSE mount (/dev/fuse, "fd=" mount) and hands the device
// to bindfs ("/dev/fd/N" mount point, libfuse 3). Without bindfs in the environment the view is
// a plain bind of the raw mount.
bool Container::ensure_storage_view(int android_user, const LinuxUser& user, std::string* err) {
    std::string view = format("%s/%d", kGuestStorageView, android_user);
    std::string raw = format("%s/%d/emulated/%d", kGuestAndroidStorage, android_user, android_user);
    bool bindfs = access((paths_.rootfs + "/usr/bin/bindfs").c_str(), X_OK) == 0;
    // 0 nothing to do, 1 needs the bindfs view, 2 needs the plain bind.
    int need = in_guest(paths_, mnt_fd_, [&](int) {
        // bindfs resolves its mount point "/dev/fd/N", which leads to the device's path: the
        // node must exist here (root only; nothing inside opens it, SELinux would not let it).
        struct stat dst;
        if (bindfs && lstat("/dev/fuse", &dst) != 0 &&
            mknod("/dev/fuse", S_IFCHR | 0600, makedev(10, 229)) != 0)
            logw("mknod /dev/fuse: %s", strerror(errno));
        mkdirs(kGuestStorageView, 0755);
        mkdir(view.c_str(), 0700);
        std::string type = mount_type(view);
        if (type == "fuse.bindfs") {
            struct stat st;
            if (stat(view.c_str(), &st) == 0) return 0;  // served
            umount2(view.c_str(), MNT_DETACH);           // its bindfs ended
            type = mount_type(view);
        }
        if (!type.empty() && !bindfs) return 0;          // the plain bind, and nothing better
        while (!type.empty()) {                          // the plain bind, now bindfs is there
            if (umount2(view.c_str(), MNT_DETACH) != 0) break;
            type = mount_type(view);
        }
        if (!is_dir(raw)) return 3;                      // storage not there (user locked)
        return bindfs ? 1 : 2;
    });
    if (need == 0) return true;
    if (need == 3) {
        *err = format("%s is not available", raw.c_str());
        return false;
    }
    if (need == 2) {
        int rc = in_guest(paths_, mnt_fd_, [&](int) {
            return mount(raw.c_str(), view.c_str(), nullptr, MS_BIND | MS_REC, nullptr) == 0 ? 0 : 1;
        });
        if (rc != 0) *err = format("bind %s: failed", view.c_str());
        return rc == 0;
    }
    int fuse = open("/dev/fuse", O_RDWR | O_CLOEXEC);
    if (fuse < 0) {
        *err = format("/dev/fuse: %s", strerror(errno));
        return false;
    }
    std::string data = format("fd=%d,rootmode=40000,user_id=%u,group_id=%u,allow_other,default_permissions",
                              fuse, user.uid, user.gid);
    int rc = in_guest(paths_, mnt_fd_, [&](int) {
        return mount("gbos-storage", view.c_str(), "fuse.bindfs", MS_NOSUID | MS_NODEV,
                     data.c_str()) == 0 ? 0 : errno;
    });
    if (rc != 0) {
        close(fuse);
        *err = format("mount %s: %s", view.c_str(), rc > 0 ? strerror(rc) : "failed");
        return false;
    }
    std::string log_path = format("/run/%s-%d.log", kStorageViewName, android_user);
    pid_t pid = fork();
    if (pid == 0) {
        reset_signals();
        std::string e;
        if (!enter(&e)) _exit(126);
        setsid();
        int devnull = open("/dev/null", O_RDWR | O_CLOEXEC);
        // bindfs's own messages, for the daemon's report when it does not come up.
        int log = open(log_path.c_str(), O_WRONLY | O_CREAT | O_TRUNC | O_NOFOLLOW | O_CLOEXEC, 0600);
        dup2(devnull, 0);
        dup2(devnull, 1);
        dup2(log >= 0 ? log : devnull, 2);
        if (dup2(fuse, 3) != 3) _exit(126);
        close_fds_except({0, 1, 2, 3});
        std::vector<gid_t> groups = user.groups;
        groups.push_back(user.gid);
        // bindfs resolves the raw path through /mnt/user/<user> (root:<user's everybody>, 0710):
        // the Android user's own everybody group, android_user * 100000 + 9997 (9997 itself only
        // for user 0; found with Android user 10 on the tablet, 24 Oct 2026)
        groups.push_back(static_cast<gid_t>(android_user * kPerUserRange + kAidEverybody));
        drop_capabilities_for_guest();
        int fd = open("/proc/thread-self/attr/exec", O_WRONLY | O_CLOEXEC);
        if (fd >= 0) {
            write(fd, kGuestSeLabel, strlen(kGuestSeLabel));
            close(fd);
        }
        if (setgroups(groups.size(), groups.data()) != 0 ||
            setresgid(user.gid, user.gid, user.gid) != 0 || setresuid(user.uid, user.uid, user.uid) != 0)
            _exit(126);
        std::string force_user = format("--force-user=%u", user.uid);
        std::string force_group = format("--force-group=%u", user.gid);
        const char* argv[] = {kStorageViewName, "-f", "--perms=u=rwx:go=", "--chmod-ignore",
                              "--chown-ignore", "--chgrp-ignore", force_user.c_str(),
                              force_group.c_str(), raw.c_str(), "/dev/fd/3", nullptr};
        const char* envp[] = {"PATH=/usr/sbin:/usr/bin:/sbin:/bin", "LANG=C.UTF-8", nullptr};
        execve("/usr/bin/bindfs", const_cast<char* const*>(argv), const_cast<char* const*>(envp));
        _exit(127);
    }
    close(fuse);
    if (pid < 0) {
        *err = format("fork: %s", strerror(errno));
        in_guest(paths_, mnt_fd_, [&](int) { return umount2(view.c_str(), MNT_DETACH); });
        return false;
    }
    // The first request waits for bindfs to answer FUSE_INIT; a bindfs that could not start
    // aborts the connection and the mount is taken down again.
    int ok = in_guest(paths_, mnt_fd_, [&](int) {
        struct stat st;
        alarm(10);
        if (stat(view.c_str(), &st) == 0) return 0;
        umount2(view.c_str(), MNT_DETACH);
        return 1;
    });
    if (ok != 0) {
        kill(pid, SIGKILL);
        auto said = guest_read(paths_, mnt_fd_, log_path);
        *err = format("bindfs for %s did not start: %s", view.c_str(),
                      said && !trim(*said).empty() ? trim(*said).c_str() : "no message");
        return false;
    }
    logi("storage view %s served by pid %d", view.c_str(), pid);
    return true;
}

bool Container::write_home_file(const LinuxUser& user, const std::string& rel,
                                const std::string& data, uint64_t offset, bool final, mode_t mode,
                                std::string* err) {
    if (!running()) {
        *err = "environment not running";
        return false;
    }
    std::string problem = droid_path_problem(rel);
    if (!problem.empty()) {
        *err = problem;
        return false;
    }
    if (offset > kDroidFileMax || data.size() > kDroidFileMax - offset) {
        *err = format("files larger than %zu MB cannot be copied", kDroidFileMax >> 20);
        return false;
    }
    std::string message;
    int rc = in_guest(paths_, mnt_fd_, [&](int out) {
        auto fail = [&](const std::string& what) {
            write_all(out, format("%s: %s", what.c_str(), strerror(errno)));
            return 1;
        };
        // From here on the user's own rights: nothing a root process could reach through a
        // link the user planted.
        if (setgroups(0, nullptr) != 0 || setresgid(user.gid, user.gid, user.gid) != 0 ||
            setresuid(user.uid, user.uid, user.uid) != 0)
            return fail("drop privileges");
        umask(022);
        int dir = open(user.home.c_str(), O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
        if (dir < 0) return fail(user.home);
        auto parts = split(rel, '/');
        for (size_t i = 0; i + 1 < parts.size(); i++) {
            if (mkdirat(dir, parts[i].c_str(), 0755) != 0 && errno != EEXIST) return fail(parts[i]);
            int next = openat(dir, parts[i].c_str(), O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
            if (next < 0) return fail(parts[i]);
            close(dir);
            dir = next;
        }
        const std::string& name = parts.back();
        std::string part = "." + name + ".gbos-part";
        if (part.size() > 255) part = ".gbos-part-" + std::to_string(std::hash<std::string>{}(name));
        int flags = O_WRONLY | O_NOFOLLOW | O_CLOEXEC | (offset == 0 ? O_CREAT | O_TRUNC : 0);
        int fd = openat(dir, part.c_str(), flags, 0600);
        if (fd < 0) return fail(offset == 0 ? part : part + " (no transfer in progress)");
        struct stat st;
        if (fstat(fd, &st) != 0 || !S_ISREG(st.st_mode)) return fail(part);
        if (static_cast<uint64_t>(st.st_size) != offset) {
            write_all(out, format("chunk at %llu, the file has %lld bytes",
                                  static_cast<unsigned long long>(offset), static_cast<long long>(st.st_size)));
            return 1;
        }
        if (lseek(fd, 0, SEEK_END) < 0 || !write_all(fd, data)) return fail(part);
        if (final) {
            if (fsync(fd) != 0 || fchmod(fd, mode & 07777 & ~(S_ISUID | S_ISGID)) != 0) return fail(part);
            struct stat dst;
            if (fstatat(dir, name.c_str(), &dst, AT_SYMLINK_NOFOLLOW) == 0 && S_ISDIR(dst.st_mode)) {
                errno = EISDIR;
                return fail(name);
            }
            if (renameat(dir, part.c_str(), dir, name.c_str()) != 0) return fail(name);
        }
        close(fd);
        close(dir);
        return 0;
    }, &message);
    if (rc != 0) {
        *err = message.empty() ? format("writing %s failed", rel.c_str()) : message;
        return false;
    }
    return true;
}

int Container::run_root(const std::vector<std::string>& argv, int out_fd, int extra_fd) {
    if (!running()) return -1;
    pid_t pid = fork();
    if (pid < 0) return -1;
    if (pid == 0) {
        reset_signals();
        std::string err;
        if (!enter(&err)) _exit(126);
        setsid();
        int devnull = open("/dev/null", O_RDWR | O_CLOEXEC);
        int out = out_fd >= 0 ? out_fd : devnull;
        dup2(devnull, 0);
        dup2(out, 1);
        dup2(out, 2);
        if (extra_fd >= 0) dup2(extra_fd, 3);
        close_fds_except(extra_fd >= 0 ? std::vector<int>{0, 1, 2, 3} : std::vector<int>{0, 1, 2});
        umask(022);
        setgroups(0, nullptr);
        drop_capabilities_for_guest();
        int fd = open("/proc/thread-self/attr/exec", O_WRONLY | O_CLOEXEC);
        if (fd >= 0) {
            write(fd, kGuestSeLabel, strlen(kGuestSeLabel));
            close(fd);
        }
        std::vector<std::string> env = {
            "PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
            "HOME=/root", "USER=root", "LOGNAME=root", "LANG=C.UTF-8",
            "DEBIAN_FRONTEND=noninteractive", "TERM=dumb"};
        std::vector<char*> envp, args;
        for (auto& e : env) envp.push_back(const_cast<char*>(e.c_str()));
        envp.push_back(nullptr);
        for (auto& a : argv) args.push_back(const_cast<char*>(a.c_str()));
        args.push_back(nullptr);
        execve(args[0], args.data(), envp.data());
        if (errno == EACCES) {  // no SELinux transition allowed: run in the daemon's domain
            fd = open("/proc/thread-self/attr/exec", O_WRONLY | O_CLOEXEC);
            if (fd >= 0) {
                write(fd, "", 0);
                close(fd);
            }
            execve(args[0], args.data(), envp.data());
        }
        dprintf(2, "%s: %s\n", argv[0].c_str(), strerror(errno));
        _exit(127);
    }
    int status = 0;
    if (TEMP_FAILURE_RETRY(waitpid(pid, &status, 0)) < 0) return -1;
    return status;
}

pid_t Container::spawn(Channel& conn, SpawnRequest& req, std::string* err) {
    if (!running()) {
        *err = "environment not running";
        return -1;
    }
    // Per-session binds travel through the exchange mount: bound here, in Android's namespace,
    // they propagate into the environment, where the monitor binds them again into its own
    // private namespace; then they are removed here.
    std::string stage;
    std::vector<std::string> guest_src;
    auto cleanup_stage = [&]() {
        if (stage.empty()) return;
        for (size_t i = 0; i < req.binds.size(); i++) {
            std::string entry = stage + "/" + std::to_string(i);
            umount2(entry.c_str(), MNT_DETACH);
            if (rmdir(entry.c_str()) != 0) unlink(entry.c_str());
        }
        rmdir(stage.c_str());
    };
    if (!req.binds.empty()) {
        std::string token = random_token();
        stage = paths_.exchange + "/" + token;
        if (mkdir(stage.c_str(), 0700) != 0) {
            *err = format("mkdir %s: %s", stage.c_str(), strerror(errno));
            return -1;
        }
        for (size_t i = 0; i < req.binds.size(); i++) {
            auto& b = req.binds[i];
            struct stat st;
            std::string entry = stage + "/" + std::to_string(i);
            bool ok = stat(b.host.c_str(), &st) == 0;
            if (ok) {
                if (S_ISDIR(st.st_mode)) ok = mkdir(entry.c_str(), 0700) == 0;
                else {
                    int f = open(entry.c_str(), O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC, 0600);
                    ok = f >= 0;
                    if (f >= 0) close(f);
                }
            }
            ok = ok && mount(b.host.c_str(), entry.c_str(), nullptr, MS_BIND | MS_REC, nullptr) == 0;
            if (ok && b.read_only)
                ok = mount(nullptr, entry.c_str(), nullptr, MS_REMOUNT | MS_BIND | MS_RDONLY, nullptr) == 0;
            if (!ok) {
                *err = format("bind source %s: %s", b.host.c_str(), strerror(errno));
                cleanup_stage();
                return -1;
            }
            guest_src.push_back(std::string(kGuestExchange) + "/" + token + "/" + std::to_string(i));
        }
    }
    int ready[2] = {-1, -1};
    if (pipe2(ready, O_CLOEXEC) != 0) {
        *err = format("pipe: %s", strerror(errno));
        cleanup_stage();
        return -1;
    }
    pid_t monitor = fork();
    if (monitor < 0) {
        *err = format("fork: %s", strerror(errno));
        close(ready[0]);
        close(ready[1]);
        cleanup_stage();
        return -1;
    }
    if (monitor > 0) {
        close(ready[1]);
        if (!stage.empty()) {
            // Wait until the monitor holds its own binds (or failed), then take ours down.
            pollfd pfd = {ready[0], POLLIN, 0};
            poll(&pfd, 1, 10000);
            cleanup_stage();
        }
        close(ready[0]);
        return monitor;
    }

    // ---- session monitor
    reset_signals();
    prctl(PR_SET_NAME, "gbos-session", 0, 0, 0);
    int connfd = conn.fd();
    close(ready[0]);
    std::vector<int> keep = {connfd, mnt_fd_, uts_fd_, ready[1]};
    for (int fd : req.stdio)
        if (fd >= 0) keep.push_back(fd);
    close_fds_except(keep);
    // Occupy 0..2 so no descriptor opened from here on can be clobbered by the dup2 calls
    // that set up the session's standard streams.
    for (int i = 0; i < 3; i++) {
        if (fcntl(i, F_GETFD) < 0) open("/dev/null", O_RDWR);
    }
    auto fail = [&](const std::string& msg) {
        conn.send({{"ok", false}, {"error", msg}, {"code", "spawn_failed"}});
        _exit(1);
    };
    std::string e;
    if (!enter(&e)) fail(e);
    if (!req.binds.empty()) {
        if (unshare(CLONE_NEWNS) != 0) fail(format("unshare: %s", strerror(errno)));
        mount(nullptr, "/", nullptr, MS_REC | MS_SLAVE, nullptr);
        for (size_t i = 0; i < req.binds.size(); i++) {
            auto& b = req.binds[i];
            struct stat st;
            if (stat(guest_src[i].c_str(), &st) != 0)
                fail(format("bind %s: %s", b.guest.c_str(), strerror(errno)));
            if (S_ISDIR(st.st_mode)) {
                if (!mkdirs(b.guest, 0755)) fail(format("mkdir %s: %s", b.guest.c_str(), strerror(errno)));
            } else {
                std::string parent = b.guest.substr(0, b.guest.rfind('/'));
                if (!parent.empty()) mkdirs(parent, 0755);
                int f = open(b.guest.c_str(), O_WRONLY | O_CREAT | O_CLOEXEC, 0644);
                if (f >= 0) close(f);
            }
            if (mount(guest_src[i].c_str(), b.guest.c_str(), nullptr, MS_BIND | MS_REC, nullptr) != 0)
                fail(format("bind %s: %s", b.guest.c_str(), strerror(errno)));
            if (b.read_only &&
                mount(nullptr, b.guest.c_str(), nullptr, MS_REMOUNT | MS_BIND | MS_RDONLY, nullptr) != 0)
                fail(format("read-only %s: %s", b.guest.c_str(), strerror(errno)));
        }
    }
    // Tell the daemon its staged binds can go (it only waits when there are binds; writing to
    // a pipe nobody reads would kill this process with SIGPIPE).
    if (!req.binds.empty() && write(ready[1], "1", 1) < 0) {}
    close(ready[1]);

    int master = -1;
    std::string slave_name;
    if (req.tty) {
        master = open("/dev/ptmx", O_RDWR | O_NOCTTY | O_CLOEXEC);
        if (master < 0) fail(format("open /dev/ptmx: %s", strerror(errno)));
        char name[64];
        if (unlockpt(master) != 0 || ptsname_r(master, name, sizeof name) != 0)
            fail(format("pty: %s", strerror(errno)));
        slave_name = name;
        struct winsize ws = {};
        ws.ws_row = static_cast<unsigned short>(std::clamp(req.rows, 1, 10000));
        ws.ws_col = static_cast<unsigned short>(std::clamp(req.cols, 1, 10000));
        ioctl(master, TIOCSWINSZ, &ws);
    }

    // Environment and program.
    const LinuxUser& u = req.user;
    std::vector<std::string> env = req.env;
    set_env(&env, "HOME", u.home);
    set_env(&env, "USER", u.name);
    set_env(&env, "LOGNAME", u.name);
    set_env(&env, "SHELL", u.shell);
    if (env_value(env, "PATH").empty()) {
        set_env(&env, "PATH", u.uid == 0
                                  ? "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
                                  : "/usr/local/bin:/usr/bin:/bin:/usr/local/games:/usr/games");
    }
    if (env_value(env, "TERM").empty()) set_env(&env, "TERM", req.tty ? "xterm-256color" : "dumb");
    if (env_value(env, "LANG").empty()) set_env(&env, "LANG", "C.UTF-8");
    set_env(&env, "XDG_RUNTIME_DIR", format("/run/user/%u", u.uid));
    if (env_value(env, "BROWSER").empty()) set_env(&env, "BROWSER", "xdg-open");
    if (req.android_user >= 0) set_env(&env, "ANDROID_USER_ID", std::to_string(req.android_user));

    std::vector<std::string> argv = req.argv;
    std::string program;
    bool login = argv.empty();
    if (login) {
        program = u.shell;
        std::string base = u.shell.substr(u.shell.rfind('/') + 1);
        argv = {"-" + base};
    }

    int errpipe[2];
    if (pipe2(errpipe, O_CLOEXEC) != 0) fail(format("pipe: %s", strerror(errno)));
    pid_t child = fork();
    if (child < 0) fail(format("fork: %s", strerror(errno)));
    if (child == 0) {
        close(errpipe[0]);
        // First byte: the shell's status for this failure (126 cannot execute, 127 not found).
        auto die = [&](const std::string& msg, char status = 125) {
            write_all(errpipe[1], std::string(1, status) + msg);
            _exit(127);
        };
        setsid();
        if (req.tty) {
            int slave = open(slave_name.c_str(), O_RDWR);
            if (slave < 0) die(format("open %s: %s", slave_name.c_str(), strerror(errno)));
            ioctl(slave, TIOCSCTTY, 0);
            dup2(slave, 0);
            dup2(slave, 1);
            dup2(slave, 2);
        } else {
            int devnull = open("/dev/null", O_RDWR);
            std::vector<int> moved;
            for (int fd : req.stdio) {
                // dup first: an incoming fd may itself be numbered like a target
                moved.push_back(fd >= 0 ? fcntl(fd, F_DUPFD_CLOEXEC, 10) : -1);
            }
            for (size_t i = 0; i < std::max<size_t>(3, moved.size()); i++) {
                int src = i < moved.size() && moved[i] >= 0 ? moved[i] : devnull;
                dup2(src, static_cast<int>(i));
            }
        }
        std::vector<int> keep_child = {0, 1, 2, errpipe[1]};
        if (!req.tty && req.stdio.size() > 3) keep_child.push_back(3);
        close_fds_except(keep_child);
        if (int fd = open("/proc/self/oom_score_adj", O_WRONLY | O_CLOEXEC); fd >= 0) {
            write(fd, "200", 3);
            close(fd);
        }
        umask(022);
        if (u.uid != 0) {
            // The run directory is created as root, then owned by the user.
            std::string xdg = format("/run/user/%u", u.uid);
            if (mkdir(xdg.c_str(), 0700) == 0) chown(xdg.c_str(), u.uid, u.gid);
        }
        if (!drop_capabilities_for_guest()) die(format("capabilities: %s", strerror(errno)));
        std::vector<gid_t> groups = u.groups;
        if (u.uid != 0) {
            // Network sockets need inet/net_raw on kernels with Android's paranoid networking;
            // everybody opens /mnt/user/<user> as it does for apps.
            for (gid_t g : {static_cast<gid_t>(kAidInet), static_cast<gid_t>(kAidNetRaw),
                            static_cast<gid_t>(kAidEverybody)})
                if (std::find(groups.begin(), groups.end(), g) == groups.end()) groups.push_back(g);
        }
        if (setgroups(groups.size(), groups.data()) != 0)
            die(format("setgroups: %s", strerror(errno)));
        if (setresgid(u.gid, u.gid, u.gid) != 0) die(format("setgid: %s", strerror(errno)));
        if (setresuid(u.uid, u.uid, u.uid) != 0) die(format("setuid: %s", strerror(errno)));
        std::string cwd = req.cwd.empty() ? u.home : req.cwd;
        if (chdir(cwd.c_str()) != 0) {
            if (!req.cwd.empty()) die(format("cd %s: %s", cwd.c_str(), strerror(errno)));
            chdir("/");
        }
        if (!login && !search_path(argv[0], env_value(env, "PATH"), &program))
            die(argv[0] + ": command not found", 127);
        std::vector<char*> args, envp;
        for (auto& a : argv) args.push_back(const_cast<char*>(a.c_str()));
        args.push_back(nullptr);
        for (auto& x : env) envp.push_back(const_cast<char*>(x.c_str()));
        envp.push_back(nullptr);
        bool labeled = false;
        if (int fd = open("/proc/thread-self/attr/exec", O_WRONLY | O_CLOEXEC); fd >= 0) {
            labeled = write(fd, kGuestSeLabel, strlen(kGuestSeLabel)) > 0;
            close(fd);
        }
        execve(program.c_str(), args.data(), envp.data());
        if (errno == EACCES && labeled) {
            if (int fd = open("/proc/thread-self/attr/exec", O_WRONLY | O_CLOEXEC); fd >= 0) {
                write(fd, "", 0);
                close(fd);
            }
            execve(program.c_str(), args.data(), envp.data());
        }
        if (errno == ENOEXEC) {  // a script without #!: run it with /bin/sh like a shell would
            std::vector<char*> sh = {const_cast<char*>("/bin/sh"), const_cast<char*>(program.c_str())};
            for (size_t i = 1; i + 1 < args.size(); i++) sh.push_back(args[i]);
            sh.push_back(nullptr);
            execve("/bin/sh", sh.data(), envp.data());
        }
        die(format("%s: %s", program.c_str(), strerror(errno)), errno == ENOENT ? 127 : 126);
    }
    close(errpipe[1]);
    for (int fd : req.stdio)
        if (fd >= 0) close(fd);
    std::string exec_error;
    char buf[512];
    ssize_t n;
    while ((n = TEMP_FAILURE_RETRY(read(errpipe[0], buf, sizeof buf))) > 0) exec_error.append(buf, n);
    close(errpipe[0]);
    if (!exec_error.empty()) {
        int status;
        waitpid(child, &status, 0);
        conn.send({{"ok", false}, {"error", exec_error.substr(1)}, {"code", "exec_failed"},
                   {"status", static_cast<int>(static_cast<unsigned char>(exec_error[0]))}});
        _exit(1);
    }
    std::vector<int> fds;
    if (master >= 0) fds.push_back(master);
    conn.send({{"ok", true}, {"pid", child}}, fds);
    if (master >= 0) close(master);  // the client holds the only master: closing it hangs up

    sigset_t mask;
    sigemptyset(&mask);
    sigaddset(&mask, SIGCHLD);
    sigprocmask(SIG_BLOCK, &mask, nullptr);
    int sfd = signalfd(-1, &mask, SFD_CLOEXEC);
    while (true) {
        int status;
        pid_t r = waitpid(child, &status, WNOHANG);
        if (r == child) {
            {
                conn.send({{"event", "exit"},
                           {"code", WIFEXITED(status) ? WEXITSTATUS(status) : -1},
                           {"signal", WIFSIGNALED(status) ? WTERMSIG(status) : 0},
                           {"status", exit_code_of(status)}});
            }
            _exit(0);
        }
        struct pollfd pfds[2] = {{sfd, POLLIN, 0}, {connfd, POLLIN, 0}};
        if (poll(pfds, 2, -1) < 0 && errno != EINTR) _exit(1);
        if (pfds[0].revents & POLLIN) {
            struct signalfd_siginfo si;
            read(sfd, &si, sizeof si);
        }
        if ((pfds[1].revents & (POLLIN | POLLHUP | POLLERR))) {
            std::vector<std::pair<json, std::vector<int>>> msgs;
            bool alive = conn.pump(&msgs);
            for (auto& [m, mfds] : msgs) {
                for (int fd : mfds) close(fd);
                if (m.value("op", "") == "signal") {
                    int sig = m.value("sig", 0);
                    if (sig > 0 && sig < NSIG) kill(-child, sig);
                }
            }
            if (!alive) {
                // Like a closed ssh connection: hang up the session's process group (a tty
                // session also gets its hangup from the kernel when the master closes). The
                // daemon is the child subreaper and reaps whatever outlives the monitor.
                kill(-child, SIGHUP);
                kill(-child, SIGCONT);
                _exit(0);
            }
        }
    }
}

}  // namespace gbos
