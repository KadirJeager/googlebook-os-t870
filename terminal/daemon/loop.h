// Minimal single-threaded poll(2) event loop with timers.
#pragma once

#include <poll.h>

#include <chrono>
#include <functional>
#include <map>
#include <vector>

namespace gbos {

class Loop {
public:
    using IoCallback = std::function<void(short revents)>;
    using Clock = std::chrono::steady_clock;

    void add(int fd, short events, IoCallback cb) { watches_[fd] = {events, std::move(cb)}; }
    void set_events(int fd, short events) {
        auto it = watches_.find(fd);
        if (it != watches_.end()) it->second.events = events;
    }
    void remove(int fd) { watches_.erase(fd); }
    bool watching(int fd) const { return watches_.count(fd) != 0; }

    // Returns an id usable with cancel().
    uint64_t after(std::chrono::milliseconds delay, std::function<void()> cb) {
        uint64_t id = ++next_id_;
        timers_.emplace(Clock::now() + delay, Timer{id, std::move(cb)});
        return id;
    }
    void cancel(uint64_t id) {
        for (auto it = timers_.begin(); it != timers_.end(); ++it) {
            if (it->second.id == id) {
                timers_.erase(it);
                return;
            }
        }
    }

    void run_once() {
        int timeout = -1;
        if (!timers_.empty()) {
            auto d = std::chrono::duration_cast<std::chrono::milliseconds>(timers_.begin()->first -
                                                                           Clock::now());
            timeout = d.count() < 0 ? 0 : static_cast<int>(std::min<long long>(d.count(), 60000));
        }
        std::vector<pollfd> pfds;
        pfds.reserve(watches_.size());
        for (auto& [fd, w] : watches_) pfds.push_back({fd, w.events, 0});
        int n = poll(pfds.data(), pfds.size(), timeout);
        if (n > 0) {
            for (auto& p : pfds) {
                if (!p.revents) continue;
                auto it = watches_.find(p.fd);  // a callback may have removed it
                if (it == watches_.end()) continue;
                IoCallback cb = it->second.cb;  // copy: the callback may remove itself
                cb(p.revents);
            }
        }
        auto now = Clock::now();
        while (!timers_.empty() && timers_.begin()->first <= now) {
            auto cb = std::move(timers_.begin()->second.cb);
            timers_.erase(timers_.begin());
            cb();
        }
    }

private:
    struct Watch {
        short events;
        IoCallback cb;
    };
    struct Timer {
        uint64_t id;
        std::function<void()> cb;
    };
    std::map<int, Watch> watches_;
    std::multimap<Clock::time_point, Timer> timers_;
    uint64_t next_id_ = 0;
};

}  // namespace gbos
