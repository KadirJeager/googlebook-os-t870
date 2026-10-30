// DNS for the Linux environment: a stub on 127.0.0.53:53 (UDP and TCP) that hands every query,
// unchanged, to Android's resolver (android_res_nsend), so Private DNS, VPNs and per-network
// servers apply exactly as they do for Android itself.
#pragma once

#include <netinet/in.h>
#include <sys/types.h>

#include <functional>
#include <map>
#include <string>

#include "loop.h"

namespace gbos {

class DnsForwarder {
public:
    // allow_uid decides whether a querying socket's owner may use the stub (Android apps other
    // than the Terminal must not get a resolver path around their own network restrictions).
    DnsForwarder(Loop& loop, std::function<bool(uid_t)> allow_uid)
        : loop_(loop), allow_uid_(std::move(allow_uid)) {}
    ~DnsForwarder() { stop(); }

    bool start(std::string* err);
    void stop();
    bool running() const { return udp_ >= 0; }

private:
    struct Pending {
        bool tcp;
        int client_fd;           // TCP connection, or -1
        sockaddr_in peer;        // UDP sender
        std::string query;
        size_t max_udp;
        uint64_t timer;
    };
    struct TcpConn {
        std::string in;
        bool busy = false;
        uint64_t idle_timer = 0;
    };

    void on_udp(short revents);
    void on_tcp_accept(short revents);
    void on_tcp_data(int fd, short revents);
    void send_query(bool tcp, int client_fd, const sockaddr_in& peer, std::string query);
    void on_answer(int nsend_fd, short revents);
    void finish(int nsend_fd, const std::string& answer);
    void reply(Pending& p, std::string answer);
    void close_tcp(int fd);
    void arm_idle(int fd);

    Loop& loop_;
    std::function<bool(uid_t)> allow_uid_;
    int udp_ = -1;
    int tcp_ = -1;
    std::map<int, Pending> pending_;   // keyed by android_res_nsend fd
    std::map<int, TcpConn> tcp_conns_;
};

// Owner uid of the local socket bound to addr:port (from /proc/net/{udp,tcp}{,6}); -1 if none.
long socket_owner(bool tcp, const sockaddr_in& addr);

}  // namespace gbos
