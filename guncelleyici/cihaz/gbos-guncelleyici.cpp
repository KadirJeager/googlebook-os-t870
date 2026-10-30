// gbos-guncelleyici: command line of the Googlebook OS updater (root, or adb shell while
// ro.debuggable=1). Sends the request to gbos-guncelleyicid and prints the reply.
//
//   gbos-guncelleyici durum
//   gbos-guncelleyici hazirla | iptal
//   gbos-guncelleyici kur [--ayni]     install the ready set in /data/gbos-ota/gelen (--ayni: also
//                                      when it is not newer than the running version)
//   gbos-guncelleyici geri             write the previous version back
//   gbos-guncelleyici --bekle KOMUT    start, then print the state until it settles
//
// Exit: 0 on "basladi"/state, 1 on a refusal or failure, 2 on usage.
#include <errno.h>
#include <stdio.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <unistd.h>

#include <string>

namespace {

constexpr const char* kSocket = "/dev/socket/gbos_guncelleyici";

int request(const std::string& req, std::string* out) {
    int fd = socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0);
    if (fd < 0) return -1;
    sockaddr_un addr = {};
    addr.sun_family = AF_UNIX;
    strncpy(addr.sun_path, kSocket, sizeof addr.sun_path - 1);
    if (connect(fd, reinterpret_cast<sockaddr*>(&addr), sizeof addr) != 0) {
        fprintf(stderr, "gbos-guncelleyici: servise bağlanılamadı: %s\n", strerror(errno));
        close(fd);
        return -1;
    }
    std::string line = req + "\n";
    if (write(fd, line.data(), line.size()) != static_cast<ssize_t>(line.size())) {
        close(fd);
        return -1;
    }
    char buf[4096];
    ssize_t n;
    while ((n = read(fd, buf, sizeof buf)) > 0) out->append(buf, n);
    close(fd);
    return 0;
}

std::string field(const std::string& state, const char* key) {
    std::string k = std::string(key) + "=";
    size_t pos = 0;
    while (pos < state.size()) {
        size_t end = state.find('\n', pos);
        if (end == std::string::npos) end = state.size();
        if (state.compare(pos, k.size(), k) == 0) return state.substr(pos + k.size(), end - pos - k.size());
        pos = end + 1;
    }
    return "";
}

int usage() {
    fputs("kullanım: gbos-guncelleyici [--bekle] durum|hazirla|iptal|kur [--ayni]|geri\n", stderr);
    return 2;
}

}  // namespace

int main(int argc, char** argv) {
    int i = 1;
    bool wait = false;
    if (i < argc && strcmp(argv[i], "--bekle") == 0) {
        wait = true;
        i++;
    }
    if (i >= argc) return usage();
    std::string req = argv[i++];
    if (req == "kur" && i < argc && strcmp(argv[i], "--ayni") == 0) {
        req += " --ayni";
        i++;
    }
    if (i != argc) return usage();
    std::string before;
    if (wait && request("durum", &before) != 0) return 1;
    std::string out;
    if (request(req, &out) != 0) return 1;
    fputs(out.c_str(), stdout);
    if (out.rfind("hata:", 0) == 0) return 1;
    if (!wait || req == "durum") return 0;
    // Follow the state until the operation settles (kur ends in a reboot). A state written
    // before the request does not count.
    std::string last, since = field(before, "zaman");
    bool started = false;
    for (int polls = 0;; polls++) {
        sleep(2);
        std::string state;
        if (request("durum", &state) != 0) return 1;
        if (!started) {
            if (field(state, "zaman") == since) {
                if (polls < 15) continue;
                fputs("işlem başlamadı (başka bir işlem sürüyor olabilir)\n", stderr);
                return 1;
            }
            started = true;
        }
        std::string d = field(state, "durum"), m = field(state, "mesaj");
        std::string now = d + ": " + m;
        if (now != last) {
            printf("%s\n", now.c_str());
            fflush(stdout);
            last = now;
        }
        if (d == "hata") return 1;
        if (d != "hazirlaniyor" && d != "kuruluyor" && d != "geri-donuluyor") return 0;
    }
}
