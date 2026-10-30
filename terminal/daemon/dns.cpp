#include "dns.h"

#include <android/multinetwork.h>
#include <arpa/inet.h>
#include <errno.h>
#include <fcntl.h>
#include <string.h>
#include <sys/socket.h>
#include <unistd.h>

#include "config.h"
#include "util.h"

namespace gbos {

namespace {

constexpr size_t kMaxPending = 512;
constexpr auto kQueryTimeout = std::chrono::seconds(15);
constexpr auto kTcpIdle = std::chrono::seconds(30);
constexpr size_t kHeader = 12;

uint16_t rd16(const std::string& m, size_t off) {
    return static_cast<uint16_t>((static_cast<uint8_t>(m[off]) << 8) | static_cast<uint8_t>(m[off + 1]));
}
void wr16(std::string* m, size_t off, uint16_t v) {
    (*m)[off] = static_cast<char>(v >> 8);
    (*m)[off + 1] = static_cast<char>(v & 0xff);
}

// Offset just past a (possibly compressed) domain name, or npos.
size_t skip_name(const std::string& m, size_t off) {
    for (int guard = 0; guard < 128; guard++) {
        if (off >= m.size()) return std::string::npos;
        uint8_t len = static_cast<uint8_t>(m[off]);
        if (len == 0) return off + 1;
        if ((len & 0xc0) == 0xc0) return off + 2 <= m.size() ? off + 2 : std::string::npos;
        if (len & 0xc0) return std::string::npos;
        off += 1 + len;
    }
    return std::string::npos;
}

// End of the question section (questions start right after the header).
size_t question_end(const std::string& m) {
    if (m.size() < kHeader) return std::string::npos;
    size_t off = kHeader;
    for (uint16_t i = 0, n = rd16(m, 4); i < n; i++) {
        off = skip_name(m, off);
        if (off == std::string::npos || off + 4 > m.size()) return std::string::npos;
        off += 4;
    }
    return off;
}

// The client's UDP payload limit: the EDNS0 OPT record's class field, or 512.
size_t udp_limit(const std::string& q) {
    size_t off = question_end(q);
    if (off == std::string::npos) return 512;
    unsigned records = rd16(q, 6) + rd16(q, 8) + rd16(q, 10);
    unsigned additional_from = rd16(q, 6) + rd16(q, 8);
    for (unsigned i = 0; i < records; i++) {
        off = skip_name(q, off);
        if (off == std::string::npos || off + 10 > q.size()) return 512;
        uint16_t type = rd16(q, off), cls = rd16(q, off + 2), rdlen = rd16(q, off + 8);
        if (i >= additional_from && type == 41) return std::max<size_t>(512, std::min<size_t>(cls, 65535));
        off += 10 + rdlen;
    }
    return 512;
}

// Header + question of `m` with the given flags and no records.
std::string header_only(const std::string& m, uint16_t flags) {
    size_t qend = question_end(m);
    if (qend == std::string::npos) qend = kHeader;
    std::string r = m.substr(0, qend);
    if (r.size() < kHeader) r.resize(kHeader, '\0');
    wr16(&r, 2, flags);
    if (qend == kHeader) wr16(&r, 4, 0);
    wr16(&r, 6, 0);
    wr16(&r, 8, 0);
    wr16(&r, 10, 0);
    return r;
}

std::string servfail(const std::string& query) {
    uint16_t flags = query.size() >= kHeader ? rd16(query, 2) : 0;
    flags = static_cast<uint16_t>(0x8000 | (flags & 0x7900) | 0x0080 | 2);  // QR, opcode+RD, RA, SERVFAIL
    return header_only(query, flags);
}

std::string truncated(const std::string& answer) {
    return header_only(answer, static_cast<uint16_t>(rd16(answer, 2) | 0x0200));  // TC
}

}  // namespace

long socket_owner(bool tcp, const sockaddr_in& addr) {
    std::string v4 = format("%08X:%04X", addr.sin_addr.s_addr, ntohs(addr.sin_port));
    std::string v6 = format("0000000000000000FFFF0000%08X:%04X", addr.sin_addr.s_addr,
                            ntohs(addr.sin_port));
    for (const char* file : tcp ? std::initializer_list<const char*>{"/proc/net/tcp", "/proc/net/tcp6"}
                                : std::initializer_list<const char*>{"/proc/net/udp", "/proc/net/udp6"}) {
        auto text = read_file(file);
        if (!text) continue;
        for (auto& line : split(*text, '\n')) {
            std::vector<std::string> f;
            for (auto& w : split(line, ' '))
                if (!w.empty()) f.push_back(w);
            if (f.size() < 8) continue;
            if (f[1] == v4 || f[1] == v6) return strtol(f[7].c_str(), nullptr, 10);
        }
    }
    return -1;
}

bool DnsForwarder::start(std::string* err) {
    if (running()) return true;
    sockaddr_in addr = {};
    addr.sin_family = AF_INET;
    addr.sin_port = htons(53);
    inet_pton(AF_INET, kDnsAddress, &addr.sin_addr);
    udp_ = socket(AF_INET, SOCK_DGRAM | SOCK_NONBLOCK | SOCK_CLOEXEC, 0);
    tcp_ = socket(AF_INET, SOCK_STREAM | SOCK_NONBLOCK | SOCK_CLOEXEC, 0);
    int one = 1;
    if (udp_ >= 0) setsockopt(udp_, SOL_SOCKET, SO_REUSEADDR, &one, sizeof one);
    if (tcp_ >= 0) setsockopt(tcp_, SOL_SOCKET, SO_REUSEADDR, &one, sizeof one);
    if (udp_ < 0 || tcp_ < 0 || bind(udp_, reinterpret_cast<sockaddr*>(&addr), sizeof addr) != 0 ||
        bind(tcp_, reinterpret_cast<sockaddr*>(&addr), sizeof addr) != 0 || listen(tcp_, 64) != 0) {
        *err = format("DNS stub on %s:53: %s", kDnsAddress, strerror(errno));
        stop();
        return false;
    }
    loop_.add(udp_, POLLIN, [this](short ev) { on_udp(ev); });
    loop_.add(tcp_, POLLIN, [this](short ev) { on_tcp_accept(ev); });
    logi("DNS stub listening on %s:53", kDnsAddress);
    return true;
}

void DnsForwarder::stop() {
    for (auto& [fd, p] : pending_) {
        loop_.remove(fd);
        loop_.cancel(p.timer);
        android_res_cancel(fd);
    }
    pending_.clear();
    for (auto& [fd, c] : tcp_conns_) {
        loop_.remove(fd);
        loop_.cancel(c.idle_timer);
        close(fd);
    }
    tcp_conns_.clear();
    if (udp_ >= 0) {
        loop_.remove(udp_);
        close(udp_);
    }
    if (tcp_ >= 0) {
        loop_.remove(tcp_);
        close(tcp_);
    }
    udp_ = tcp_ = -1;
}

void DnsForwarder::on_udp(short) {
    for (int i = 0; i < 64; i++) {
        char buf[4096];
        sockaddr_in peer = {};
        socklen_t len = sizeof peer;
        ssize_t n = recvfrom(udp_, buf, sizeof buf, 0, reinterpret_cast<sockaddr*>(&peer), &len);
        if (n < 0) return;
        if (n < static_cast<ssize_t>(kHeader)) continue;
        long owner = socket_owner(false, peer);
        if (owner < 0 || !allow_uid_(static_cast<uid_t>(owner))) continue;
        send_query(false, -1, peer, std::string(buf, n));
    }
}

void DnsForwarder::on_tcp_accept(short) {
    while (true) {
        sockaddr_in peer = {};
        socklen_t len = sizeof peer;
        int fd = accept4(tcp_, reinterpret_cast<sockaddr*>(&peer), &len, SOCK_NONBLOCK | SOCK_CLOEXEC);
        if (fd < 0) return;
        long owner = socket_owner(true, peer);
        if (owner < 0 || !allow_uid_(static_cast<uid_t>(owner)) || tcp_conns_.size() >= 64) {
            close(fd);
            continue;
        }
        tcp_conns_[fd] = TcpConn{};
        loop_.add(fd, POLLIN, [this, fd](short ev) { on_tcp_data(fd, ev); });
        arm_idle(fd);
    }
}

void DnsForwarder::arm_idle(int fd) {
    auto it = tcp_conns_.find(fd);
    if (it == tcp_conns_.end()) return;
    loop_.cancel(it->second.idle_timer);
    it->second.idle_timer = loop_.after(
        std::chrono::duration_cast<std::chrono::milliseconds>(kTcpIdle), [this, fd] {
            auto c = tcp_conns_.find(fd);
            if (c != tcp_conns_.end() && !c->second.busy) close_tcp(fd);
        });
}

void DnsForwarder::close_tcp(int fd) {
    auto it = tcp_conns_.find(fd);
    if (it == tcp_conns_.end()) return;
    loop_.cancel(it->second.idle_timer);
    loop_.remove(fd);
    close(fd);
    tcp_conns_.erase(it);
}

void DnsForwarder::on_tcp_data(int fd, short revents) {
    auto it = tcp_conns_.find(fd);
    if (it == tcp_conns_.end()) return;
    char buf[8192];
    while (true) {
        ssize_t n = read(fd, buf, sizeof buf);
        if (n > 0) {
            it->second.in.append(buf, n);
            if (it->second.in.size() > 70000) return close_tcp(fd);
            continue;
        }
        if (n == 0 || (errno != EAGAIN && errno != EINTR)) return close_tcp(fd);
        break;
    }
    (void)revents;
    TcpConn& c = it->second;
    if (c.busy || c.in.size() < 2) return;
    size_t len = rd16(c.in, 0);
    if (c.in.size() < 2 + len) return;
    std::string query = c.in.substr(2, len);
    c.in.erase(0, 2 + len);
    if (query.size() < kHeader) return close_tcp(fd);
    c.busy = true;
    loop_.set_events(fd, 0);  // one query at a time per connection
    send_query(true, fd, {}, std::move(query));
}

void DnsForwarder::send_query(bool tcp, int client_fd, const sockaddr_in& peer, std::string query) {
    Pending p{tcp, client_fd, peer, std::move(query), 0, 0};
    p.max_udp = tcp ? 65535 : udp_limit(p.query);
    if (pending_.size() >= kMaxPending) {
        reply(p, servfail(p.query));
        return;
    }
    int fd = android_res_nsend(0 /* NETWORK_UNSPECIFIED: Android's default network */,
                               reinterpret_cast<const uint8_t*>(p.query.data()), p.query.size(), 0);
    if (fd < 0) {
        logw("android_res_nsend: %s", strerror(-fd));
        reply(p, servfail(p.query));
        return;
    }
    p.timer = loop_.after(std::chrono::duration_cast<std::chrono::milliseconds>(kQueryTimeout),
                          [this, fd] {
                              auto it = pending_.find(fd);
                              if (it == pending_.end()) return;
                              Pending q = std::move(it->second);
                              pending_.erase(it);
                              loop_.remove(fd);
                              android_res_cancel(fd);
                              reply(q, servfail(q.query));
                          });
    pending_.emplace(fd, std::move(p));
    loop_.add(fd, POLLIN, [this, fd](short ev) { on_answer(fd, ev); });
}

void DnsForwarder::on_answer(int fd, short) {
    auto it = pending_.find(fd);
    if (it == pending_.end()) return;
    Pending p = std::move(it->second);
    pending_.erase(it);
    loop_.remove(fd);
    loop_.cancel(p.timer);
    uint8_t buf[65535];
    int rcode = 0;
    int n = android_res_nresult(fd, &rcode, buf, sizeof buf);  // closes fd
    if (n < 0) {
        reply(p, servfail(p.query));
        return;
    }
    reply(p, std::string(reinterpret_cast<char*>(buf), n));
}

void DnsForwarder::reply(Pending& p, std::string answer) {
    if (answer.size() < kHeader) answer = servfail(p.query);
    // The resolver answers with the query's id; keep the client's id in any case.
    answer[0] = p.query[0];
    answer[1] = p.query[1];
    if (!p.tcp) {
        if (answer.size() > p.max_udp) answer = truncated(answer);
        sendto(udp_, answer.data(), answer.size(), MSG_DONTWAIT,
               reinterpret_cast<const sockaddr*>(&p.peer), sizeof p.peer);
        return;
    }
    auto it = tcp_conns_.find(p.client_fd);
    if (it == tcp_conns_.end()) return;
    std::string framed(2, '\0');
    wr16(&framed, 0, static_cast<uint16_t>(answer.size()));
    framed += answer;
    // Answers are small; a blocking-ish write with a short poll keeps this simple.
    size_t off = 0;
    while (off < framed.size()) {
        ssize_t n = write(p.client_fd, framed.data() + off, framed.size() - off);
        if (n > 0) {
            off += n;
            continue;
        }
        if (n < 0 && errno == EAGAIN) {
            pollfd pfd = {p.client_fd, POLLOUT, 0};
            if (poll(&pfd, 1, 1000) > 0) continue;
        }
        close_tcp(p.client_fd);
        return;
    }
    it->second.busy = false;
    loop_.set_events(p.client_fd, POLLIN);
    arm_idle(p.client_fd);
    if (it->second.in.size() >= 2) on_tcp_data(p.client_fd, 0);  // a pipelined next query
}

}  // namespace gbos
