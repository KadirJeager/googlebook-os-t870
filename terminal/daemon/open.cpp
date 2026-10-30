// xdg-open for the Linux environment: asks Android (through gbos-linuxd's host socket at
// /run/googlebook/host.sock) to open a link in the default browser or a file in the app that
// handles it. Built as a static executable: it runs inside Debian, where Android's dynamic
// linker does not exist. Exit codes follow xdg-open(1).
#include <errno.h>
#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <unistd.h>

#include <string>

namespace {

constexpr const char* kSocket = "/run/googlebook/host.sock";

std::string json_string(const std::string& s) {
    std::string r = "\"";
    for (unsigned char c : s) {
        switch (c) {
            case '"': r += "\\\""; break;
            case '\\': r += "\\\\"; break;
            case '\n': r += "\\n"; break;
            case '\r': r += "\\r"; break;
            case '\t': r += "\\t"; break;
            default:
                if (c < 0x20) {
                    char b[8];
                    snprintf(b, sizeof b, "\\u%04x", c);
                    r += b;
                } else {
                    r += static_cast<char>(c);
                }
        }
    }
    return r + "\"";
}

// The "error" string of a reply (enough for the daemon's flat replies).
std::string reply_error(const std::string& reply) {
    size_t p = reply.find("\"error\":");
    if (p == std::string::npos) return "request failed";
    p = reply.find('"', p + 8);
    if (p == std::string::npos) return "request failed";
    std::string out;
    for (size_t i = p + 1; i < reply.size() && reply[i] != '"'; i++) {
        if (reply[i] == '\\' && i + 1 < reply.size()) i++;
        out += reply[i];
    }
    return out;
}

bool is_url(const char* s) {
    // scheme ":" as in RFC 3986; a single letter would be a Windows drive, not a scheme.
    const char* p = s;
    if (!((*p >= 'a' && *p <= 'z') || (*p >= 'A' && *p <= 'Z'))) return false;
    while (*p && *p != ':') {
        char c = *p;
        if (!((c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9') ||
              c == '+' || c == '-' || c == '.'))
            return false;
        p++;
    }
    return *p == ':' && p - s > 1 && p[1] != 0;
}

}  // namespace

int main(int argc, char** argv) {
    if (argc == 2 && (!strcmp(argv[1], "--help") || !strcmp(argv[1], "--manual"))) {
        printf("Usage: xdg-open { file | URL }\n"
               "Opens a link in Android's default browser or a file in the Android app that\n"
               "handles its type.\n");
        return 0;
    }
    if (argc == 2 && !strcmp(argv[1], "--version")) {
        printf("xdg-open (Googlebook Linux) 1.0\n");
        return 0;
    }
    if (argc != 2 || !argv[1][0]) {
        fprintf(stderr, "Usage: xdg-open { file | URL }\n");
        return 1;
    }
    const char* target = argv[1];
    std::string request;
    if (strncmp(target, "file://", 7) == 0) target += 7;  // a local file named as a URL
    if (is_url(target) && access(target, F_OK) != 0) {
        request = "{\"op\":\"open\",\"url\":" + json_string(target) + "}\n";
    } else {
        char real[PATH_MAX];
        if (!realpath(target, real)) {
            fprintf(stderr, "xdg-open: %s: %s\n", target, strerror(errno));
            return 2;
        }
        request = "{\"op\":\"open\",\"path\":" + json_string(real) + "}\n";
    }
    int fd = socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0);
    sockaddr_un addr = {};
    addr.sun_family = AF_UNIX;
    strncpy(addr.sun_path, kSocket, sizeof addr.sun_path - 1);
    if (fd < 0 || connect(fd, reinterpret_cast<sockaddr*>(&addr), sizeof addr) != 0) {
        fprintf(stderr, "xdg-open: Android is not reachable (%s): %s\n", kSocket, strerror(errno));
        return 3;
    }
    if (write(fd, request.data(), request.size()) != static_cast<ssize_t>(request.size())) {
        fprintf(stderr, "xdg-open: %s\n", strerror(errno));
        return 4;
    }
    std::string reply;
    char buf[1024];
    ssize_t n;
    while (reply.find('\n') == std::string::npos && (n = read(fd, buf, sizeof buf)) > 0) reply.append(buf, n);
    close(fd);
    if (reply.find("\"ok\":true") != std::string::npos) return 0;
    fprintf(stderr, "xdg-open: %s\n", reply.empty() ? "no answer from Android" : reply_error(reply).c_str());
    return 4;
}
