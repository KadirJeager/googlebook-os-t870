// gbos-guncelleyicid: the updater's service. init starts it (gbos-guncelleyici.rc) as root in
// the gbos_guncelleyici domain with the socket /dev/socket/gbos_guncelleyici; the "Sistem
// güncellemesi" app and `gbos-guncelleyici` (root, adb shell) send one request line each:
//
//   durum                 the state lines of gbos-guncelleyici-is durum, then EOF
//   hazirla | iptal | kur started in the background; the reply is "basladi" (progress: durum)
//   geri                  root and adb shell only
//
// The caller is identified by SO_PEERCRED: root; adb shell (uid 2000) only while
// ro.debuggable=1; the app com.gbos.guncelleyici in any Android user (its app id from
// /data/system/packages.list). Every operation runs gbos-guncelleyici-is, which serialises them
// with its own lock. The service holds no state of its own.
#include <errno.h>
#include <fcntl.h>
#include <poll.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/system_properties.h>
#include <sys/types.h>
#include <sys/un.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

#include <android/log.h>

#include <string>
#include <vector>

namespace {

constexpr const char* kSocketName = "gbos_guncelleyici";
constexpr const char* kScript = "/system/bin/gbos-guncelleyici-is";
constexpr const char* kPackage = "com.gbos.guncelleyici";
constexpr const char* kPackagesList = "/data/system/packages.list";
constexpr uid_t kAidShell = 2000;
constexpr uid_t kPerUserRange = 100000;
constexpr int kRequestTimeoutMs = 5000;
constexpr int kDurumTimeoutS = 20;

void logi(const char* fmt, const char* a = "", const char* b = "") {
    __android_log_print(ANDROID_LOG_INFO, "gbos-guncelleyicid", fmt, a, b);
}

bool debuggable() {
    char value[PROP_VALUE_MAX] = {};
    __system_property_get("ro.debuggable", value);
    return strcmp(value, "1") == 0;
}

// App id (uid % 100000) of the updater app, 0 when it is not installed.
uid_t app_id() {
    FILE* f = fopen(kPackagesList, "re");
    if (!f) return 0;
    char line[4096];
    uid_t found = 0;
    size_t plen = strlen(kPackage);
    while (fgets(line, sizeof line, f)) {
        if (strncmp(line, kPackage, plen) == 0 && line[plen] == ' ') {
            found = static_cast<uid_t>(strtoul(line + plen + 1, nullptr, 10)) % kPerUserRange;
            break;
        }
    }
    fclose(f);
    return found;
}

enum class Caller { None, App, Admin };

Caller identify(int fd, uid_t* uid_out) {
    struct ucred cred = {};
    socklen_t len = sizeof cred;
    if (getsockopt(fd, SOL_SOCKET, SO_PEERCRED, &cred, &len) != 0) return Caller::None;
    *uid_out = cred.uid;
    if (cred.uid == 0) return Caller::Admin;
    if (cred.uid == kAidShell && debuggable()) return Caller::Admin;
    uid_t app = app_id();
    if (app != 0 && cred.uid % kPerUserRange == app) return Caller::App;
    return Caller::None;
}

bool read_request(int fd, std::string* out) {
    char buf[256];
    size_t used = 0;
    while (used < sizeof buf) {
        struct pollfd p = {fd, POLLIN, 0};
        if (poll(&p, 1, kRequestTimeoutMs) <= 0) return false;
        ssize_t n = read(fd, buf + used, sizeof buf - used);
        if (n <= 0) return false;
        for (ssize_t i = 0; i < n; i++) {
            if (buf[used + i] == '\n') {
                out->assign(buf, used + i);
                return true;
            }
        }
        used += n;
    }
    return false;
}

void reply(int fd, const std::string& text) {
    std::string s = text + "\n";
    const char* p = s.data();
    size_t left = s.size();
    while (left) {
        ssize_t n = send(fd, p, left, MSG_NOSIGNAL);
        if (n <= 0) return;
        p += n;
        left -= n;
    }
}

[[noreturn]] void exec_script(const std::vector<std::string>& args) {
    std::vector<char*> argv;
    argv.push_back(const_cast<char*>(kScript));
    for (auto& a : args) argv.push_back(const_cast<char*>(a.c_str()));
    argv.push_back(nullptr);
    execv(kScript, argv.data());
    _exit(127);
}

// durum: the script's output goes straight to the caller.
void run_durum(int fd) {
    pid_t pid = fork();
    if (pid == 0) {
        int null = open("/dev/null", O_RDWR | O_CLOEXEC);
        dup2(null, 0);
        dup2(fd, 1);
        dup2(null, 2);
        exec_script({"durum"});
    }
    if (pid < 0) {
        reply(fd, "hata: fork");
        return;
    }
    for (int i = 0; i < kDurumTimeoutS * 10; i++) {
        if (waitpid(pid, nullptr, WNOHANG) == pid) return;
        usleep(100000);
    }
    kill(pid, SIGKILL);
    waitpid(pid, nullptr, 0);
}

// Long operations run detached; their progress is in the state file (durum).
bool run_detached(const std::vector<std::string>& args) {
    pid_t pid = fork();
    if (pid < 0) return false;
    if (pid == 0) {
        // grandchild, so nothing waits on it here
        if (fork() != 0) _exit(0);
        setsid();
        int null = open("/dev/null", O_RDWR | O_CLOEXEC);
        dup2(null, 0);
        dup2(null, 1);
        dup2(null, 2);
        for (int fd = 3; fd < 256; fd++) close(fd);
        exec_script(args);
    }
    waitpid(pid, nullptr, 0);
    return true;
}

void serve(int client) {
    uid_t uid = 0;
    Caller who = identify(client, &uid);
    std::string req;
    if (!read_request(client, &req)) return;
    std::string uid_s = std::to_string(uid);
    if (who == Caller::None) {
        logi("uid %s refused (%s)", uid_s.c_str(), req.c_str());
        reply(client, "hata: yetki-yok");
        return;
    }
    if (req == "durum") {
        run_durum(client);
        return;
    }
    bool app_ok = req == "hazirla" || req == "iptal" || req == "kur";
    bool admin_only = req == "geri" || req == "kur --ayni";
    if (!app_ok && !admin_only) {
        reply(client, "hata: bilinmeyen-istek");
        return;
    }
    if (admin_only && who != Caller::Admin) {
        reply(client, "hata: yetki-yok");
        return;
    }
    logi("uid %s: %s", uid_s.c_str(), req.c_str());
    std::vector<std::string> args = {req};
    if (req == "kur --ayni") args = {"kur", "--ayni"};
    reply(client, run_detached(args) ? "basladi" : "hata: fork");
}

int control_socket() {
    const char* env = getenv("ANDROID_SOCKET_gbos_guncelleyici");
    if (!env) return -1;
    int fd = atoi(env);
    fcntl(fd, F_SETFD, FD_CLOEXEC);
    return fd;
}

}  // namespace

int main() {
    signal(SIGPIPE, SIG_IGN);
    int sock = control_socket();
    if (sock < 0 || listen(sock, 8) != 0) {
        logi("no control socket %s", kSocketName);
        return 1;
    }
    logi("ready");
    for (;;) {
        int client = accept4(sock, nullptr, nullptr, SOCK_CLOEXEC);
        if (client < 0) {
            if (errno == EINTR) continue;
            sleep(1);
            continue;
        }
        serve(client);
        close(client);
    }
}
