#include "util.h"

#include <android/log.h>
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <signal.h>
#include <stdarg.h>
#include <string.h>
#include <poll.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/system_properties.h>
#include <sys/un.h>
#include <sys/wait.h>
#include <unistd.h>

#include <algorithm>

namespace gbos {

namespace {
const char* g_tag = "gbos-linux";
bool g_stderr = true;

void vlog(int prio, const char* fmt, va_list ap) {
    va_list ap2;
    va_copy(ap2, ap);
    __android_log_vprint(prio, g_tag, fmt, ap);
    if (g_stderr) {
        const char* p = prio >= ANDROID_LOG_ERROR ? "E" : prio >= ANDROID_LOG_WARN ? "W" : "I";
        fprintf(stderr, "%s %s: ", g_tag, p);
        vfprintf(stderr, fmt, ap2);
        fputc('\n', stderr);
    }
    va_end(ap2);
}
}  // namespace

void log_init(const char* tag, bool to_stderr) {
    g_tag = tag;
    g_stderr = to_stderr;
}

void logi(const char* fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    vlog(ANDROID_LOG_INFO, fmt, ap);
    va_end(ap);
}
void logw(const char* fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    vlog(ANDROID_LOG_WARN, fmt, ap);
    va_end(ap);
}
void loge(const char* fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    vlog(ANDROID_LOG_ERROR, fmt, ap);
    va_end(ap);
}

std::string strerr(int err) { return strerror(err); }

std::string format(const char* fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    char* s = nullptr;
    int n = vasprintf(&s, fmt, ap);
    va_end(ap);
    if (n < 0) return {};
    std::string r(s, n);
    free(s);
    return r;
}

std::optional<std::string> read_file(const std::string& path, size_t max) {
    int fd = open(path.c_str(), O_RDONLY | O_CLOEXEC);
    if (fd < 0) return std::nullopt;
    std::string data;
    char buf[8192];
    while (true) {
        ssize_t n = TEMP_FAILURE_RETRY(read(fd, buf, sizeof buf));
        if (n < 0) {
            int e = errno;
            close(fd);
            errno = e;
            return std::nullopt;
        }
        if (n == 0) break;
        data.append(buf, n);
        if (data.size() > max) {
            close(fd);
            errno = EFBIG;
            return std::nullopt;
        }
    }
    close(fd);
    return data;
}

bool write_file_atomic(const std::string& path, const std::string& data, mode_t mode, uid_t uid,
                       gid_t gid) {
    std::string tmp = path + ".tmp-gbos";
    int fd = open(tmp.c_str(), O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC | O_NOFOLLOW, mode);
    if (fd < 0) return false;
    size_t off = 0;
    while (off < data.size()) {
        ssize_t n = TEMP_FAILURE_RETRY(write(fd, data.data() + off, data.size() - off));
        if (n <= 0) {
            int e = errno;
            close(fd);
            unlink(tmp.c_str());
            errno = e;
            return false;
        }
        off += n;
    }
    if (fchmod(fd, mode) != 0 || fchown(fd, uid, gid) != 0 || fsync(fd) != 0) {
        int e = errno;
        close(fd);
        unlink(tmp.c_str());
        errno = e;
        return false;
    }
    close(fd);
    if (rename(tmp.c_str(), path.c_str()) != 0) {
        int e = errno;
        unlink(tmp.c_str());
        errno = e;
        return false;
    }
    return true;
}

bool mkdirs(const std::string& path, mode_t mode, uid_t uid, gid_t gid) {
    if (path.empty()) return false;
    size_t pos = 1;
    while (true) {
        pos = path.find('/', pos);
        std::string part = path.substr(0, pos);
        if (mkdir(part.c_str(), mode) == 0) {
            if (chown(part.c_str(), uid, gid) != 0 || chmod(part.c_str(), mode) != 0) return false;
        } else if (errno != EEXIST) {
            return false;
        }
        if (pos == std::string::npos) break;
        pos++;
    }
    return is_dir(path);
}

bool path_exists(const std::string& path) {
    struct stat st;
    return lstat(path.c_str(), &st) == 0;
}

bool is_dir(const std::string& path) {
    struct stat st;
    return stat(path.c_str(), &st) == 0 && S_ISDIR(st.st_mode);
}

std::string trim(const std::string& s) {
    size_t a = s.find_first_not_of(" \t\r\n");
    if (a == std::string::npos) return {};
    size_t b = s.find_last_not_of(" \t\r\n");
    return s.substr(a, b - a + 1);
}

std::vector<std::string> split(const std::string& s, char sep) {
    std::vector<std::string> out;
    size_t start = 0;
    while (true) {
        size_t p = s.find(sep, start);
        out.push_back(s.substr(start, p == std::string::npos ? std::string::npos : p - start));
        if (p == std::string::npos) break;
        start = p + 1;
    }
    return out;
}

bool starts_with(const std::string& s, const std::string& prefix) {
    return s.size() >= prefix.size() && s.compare(0, prefix.size(), prefix) == 0;
}

namespace {
bool remove_tree_at(int dirfd, dev_t dev) {
    DIR* d = fdopendir(dirfd);
    if (!d) {
        close(dirfd);
        return false;
    }
    bool ok = true;
    while (struct dirent* e = readdir(d)) {
        if (!strcmp(e->d_name, ".") || !strcmp(e->d_name, "..")) continue;
        struct stat st;
        if (fstatat(dirfd, e->d_name, &st, AT_SYMLINK_NOFOLLOW) != 0) {
            ok = false;
            continue;
        }
        if (S_ISDIR(st.st_mode)) {
            if (st.st_dev != dev) {  // a mount point: never descend into another filesystem
                loge("remove_tree: refusing to cross mount at %s", e->d_name);
                ok = false;
                continue;
            }
            int sub = openat(dirfd, e->d_name, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
            if (sub < 0 || !remove_tree_at(sub, dev)) {
                ok = false;
                continue;
            }
            if (unlinkat(dirfd, e->d_name, AT_REMOVEDIR) != 0) ok = false;
        } else if (unlinkat(dirfd, e->d_name, 0) != 0) {
            ok = false;
        }
    }
    closedir(d);
    return ok;
}
}  // namespace

bool remove_tree(const std::string& path) {
    struct stat st;
    if (lstat(path.c_str(), &st) != 0) return errno == ENOENT;
    if (!S_ISDIR(st.st_mode)) return unlink(path.c_str()) == 0;
    int fd = open(path.c_str(), O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
    if (fd < 0) return false;
    if (!remove_tree_at(fd, st.st_dev)) return false;
    return rmdir(path.c_str()) == 0;
}

std::string get_property(const char* name, const std::string& fallback) {
    char value[PROP_VALUE_MAX] = {};
    int n = __system_property_get(name, value);
    return n > 0 ? std::string(value, n) : fallback;
}

void close_fds_except(const std::vector<int>& keep) {
    DIR* d = opendir("/proc/self/fd");
    if (!d) return;
    int self = dirfd(d);
    std::vector<int> to_close;
    while (struct dirent* e = readdir(d)) {
        if (e->d_name[0] == '.') continue;
        int fd = atoi(e->d_name);
        if (fd == self || std::find(keep.begin(), keep.end(), fd) != keep.end()) continue;
        to_close.push_back(fd);
    }
    closedir(d);
    for (int fd : to_close) close(fd);
}

void reset_signals() {
    sigset_t set;
    sigemptyset(&set);
    sigprocmask(SIG_SETMASK, &set, nullptr);
    for (int sig = 1; sig < NSIG; sig++) {
        if (sig == SIGKILL || sig == SIGSTOP) continue;
        signal(sig, SIG_DFL);
    }
}

int run(const std::vector<std::string>& argv, std::string* out,
        const std::vector<std::string>* env) {
    int pipefd[2] = {-1, -1};
    if (out && pipe2(pipefd, O_CLOEXEC) != 0) return -1;
    pid_t pid = fork();
    if (pid < 0) return -1;
    if (pid == 0) {
        reset_signals();
        int devnull = open("/dev/null", O_RDWR);
        dup2(devnull, 0);
        if (out) dup2(pipefd[1], 1);
        else dup2(devnull, 1);
        // stderr is kept: the daemon's stderr goes to the kernel log via init.
        std::vector<char*> args;
        for (auto& a : argv) args.push_back(const_cast<char*>(a.c_str()));
        args.push_back(nullptr);
        std::vector<char*> envp;
        if (env) {
            for (auto& e : *env) envp.push_back(const_cast<char*>(e.c_str()));
            envp.push_back(nullptr);
        }
        execve(args[0], args.data(), env ? envp.data() : environ);
        _exit(127);
    }
    if (out) {
        close(pipefd[1]);
        char buf[4096];
        ssize_t n;
        while ((n = TEMP_FAILURE_RETRY(read(pipefd[0], buf, sizeof buf))) > 0) out->append(buf, n);
        close(pipefd[0]);
    }
    int status = 0;
    if (TEMP_FAILURE_RETRY(waitpid(pid, &status, 0)) < 0) return -1;
    return status;
}

std::string describe_status(int status) {
    if (status == -1) return "could not start";
    if (WIFEXITED(status)) return format("exit code %d", WEXITSTATUS(status));
    if (WIFSIGNALED(status)) return format("killed by signal %d", WTERMSIG(status));
    return format("status 0x%x", status);
}

int exit_code_of(int status) {
    if (WIFEXITED(status)) return WEXITSTATUS(status);
    if (WIFSIGNALED(status)) return 128 + WTERMSIG(status);
    return 125;
}

// ---- Channel

Channel::~Channel() { close(); }

Channel::Channel(Channel&& o) noexcept
    : fd_(o.fd_), buf_(std::move(o.buf_)), pending_fds_(std::move(o.pending_fds_)),
      ready_(std::move(o.ready_)) {
    o.fd_ = -1;
    o.pending_fds_.clear();
    o.ready_.clear();
}

Channel& Channel::operator=(Channel&& o) noexcept {
    if (this != &o) {
        close();
        fd_ = o.fd_;
        buf_ = std::move(o.buf_);
        pending_fds_ = std::move(o.pending_fds_);
        ready_ = std::move(o.ready_);
        o.fd_ = -1;
        o.pending_fds_.clear();
        o.ready_.clear();
    }
    return *this;
}

int Channel::release() {
    int fd = fd_;
    fd_ = -1;
    return fd;
}

void Channel::close() {
    if (fd_ >= 0) ::close(fd_);
    fd_ = -1;
    for (int fd : pending_fds_) ::close(fd);
    pending_fds_.clear();
    for (auto& m : ready_)
        for (int fd : m.second) ::close(fd);
    ready_.clear();
}

bool Channel::send(const json& msg, const std::vector<int>& fds) {
    std::string line = msg.dump(-1, ' ', false, json::error_handler_t::replace) + "\n";
    size_t off = 0;
    bool first = true;
    while (off < line.size()) {
        struct iovec iov = {line.data() + off, line.size() - off};
        struct msghdr mh = {};
        mh.msg_iov = &iov;
        mh.msg_iovlen = 1;
        alignas(struct cmsghdr) char cbuf[CMSG_SPACE(sizeof(int) * kMaxFds)];
        if (first && !fds.empty()) {
            size_t n = std::min<size_t>(fds.size(), kMaxFds);
            mh.msg_control = cbuf;
            mh.msg_controllen = CMSG_SPACE(sizeof(int) * n);
            struct cmsghdr* c = CMSG_FIRSTHDR(&mh);
            c->cmsg_level = SOL_SOCKET;
            c->cmsg_type = SCM_RIGHTS;
            c->cmsg_len = CMSG_LEN(sizeof(int) * n);
            memcpy(CMSG_DATA(c), fds.data(), sizeof(int) * n);
        }
        ssize_t n = TEMP_FAILURE_RETRY(sendmsg(fd_, &mh, MSG_NOSIGNAL));
        if (n < 0) {
            if (errno == EAGAIN) {  // a non-blocking socket whose peer is slow: wait for room
                struct pollfd pfd = {fd_, POLLOUT, 0};
                if (poll(&pfd, 1, 5000) <= 0) {
                    errno = ETIMEDOUT;
                    return false;
                }
                continue;
            }
            return false;
        }
        off += n;
        first = false;
    }
    return true;
}

bool Channel::has_buffered() const { return !ready_.empty(); }

bool Channel::parse_buffered() {
    size_t nl;
    while ((nl = buf_.find('\n')) != std::string::npos) {
        std::string line = buf_.substr(0, nl);
        buf_.erase(0, nl + 1);
        json msg = json::parse(line, nullptr, false);
        if (msg.is_discarded() || !msg.is_object()) {
            errno = EBADMSG;
            return false;
        }
        // fds travel with the first byte of the message they belong to and every sender
        // writes one message per sendmsg, so fds received so far belong to this message.
        ready_.emplace_back(std::move(msg), std::move(pending_fds_));
        pending_fds_.clear();
    }
    if (buf_.size() > kMaxLine) {
        errno = EMSGSIZE;
        return false;
    }
    return true;
}

bool Channel::read_some(bool blocking, bool* eof) {
    *eof = false;
    char data[16384];
    struct iovec iov = {data, sizeof data};
    alignas(struct cmsghdr) char cbuf[CMSG_SPACE(sizeof(int) * kMaxFds)];
    struct msghdr mh = {};
    mh.msg_iov = &iov;
    mh.msg_iovlen = 1;
    mh.msg_control = cbuf;
    mh.msg_controllen = sizeof cbuf;
    ssize_t n = TEMP_FAILURE_RETRY(
        recvmsg(fd_, &mh, MSG_CMSG_CLOEXEC | (blocking ? 0 : MSG_DONTWAIT)));
    if (n < 0) return false;
    for (struct cmsghdr* c = CMSG_FIRSTHDR(&mh); c; c = CMSG_NXTHDR(&mh, c)) {
        if (c->cmsg_level == SOL_SOCKET && c->cmsg_type == SCM_RIGHTS) {
            size_t cnt = (c->cmsg_len - CMSG_LEN(0)) / sizeof(int);
            int* p = reinterpret_cast<int*>(CMSG_DATA(c));
            for (size_t i = 0; i < cnt; i++) pending_fds_.push_back(p[i]);
        }
    }
    if (n == 0) {
        *eof = true;
        return true;
    }
    buf_.append(data, n);
    return parse_buffered();
}

bool Channel::pump(std::vector<std::pair<json, std::vector<int>>>* out) {
    bool alive = true;
    while (true) {
        bool eof = false;
        if (!read_some(false, &eof)) {
            alive = errno == EAGAIN;
            break;
        }
        if (eof) {
            alive = false;
            errno = 0;
            break;
        }
    }
    while (!ready_.empty()) {
        out->push_back(std::move(ready_.front()));
        ready_.pop_front();
    }
    return alive;
}

bool Channel::recv(json* msg, std::vector<int>* fds) {
    while (ready_.empty()) {
        bool eof = false;
        if (!read_some(true, &eof)) return false;
        if (eof) {
            errno = 0;
            return false;
        }
    }
    *msg = std::move(ready_.front().first);
    if (fds) *fds = std::move(ready_.front().second);
    else
        for (int fd : ready_.front().second) ::close(fd);
    ready_.pop_front();
    return true;
}

int connect_unix(const std::string& path) {
    int fd = socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0);
    if (fd < 0) return -1;
    struct sockaddr_un addr = {};
    addr.sun_family = AF_UNIX;
    if (path.size() >= sizeof addr.sun_path) {
        close(fd);
        errno = ENAMETOOLONG;
        return -1;
    }
    memcpy(addr.sun_path, path.c_str(), path.size());
    if (TEMP_FAILURE_RETRY(connect(fd, reinterpret_cast<sockaddr*>(&addr), sizeof addr)) != 0) {
        int e = errno;
        close(fd);
        errno = e;
        return -1;
    }
    return fd;
}

bool peer_cred(int fd, pid_t* pid, uid_t* uid, gid_t* gid) {
    struct ucred cred = {};
    socklen_t len = sizeof cred;
    if (getsockopt(fd, SOL_SOCKET, SO_PEERCRED, &cred, &len) != 0) return false;
    if (pid) *pid = cred.pid;
    if (uid) *uid = cred.uid;
    if (gid) *gid = cred.gid;
    return true;
}

}  // namespace gbos
