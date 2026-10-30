// Shared helpers of gbos-linuxd (root daemon) and gbos-linux (client).
#pragma once

#include <sys/types.h>

#include <deque>
#include <functional>
#include <map>
#include <optional>
#include <string>
#include <vector>

#include "json.hpp"

using json = nlohmann::json;

namespace gbos {

// ---- logging: logcat (tag "gbos-linux") and stderr when it is a terminal or a pipe.
void log_init(const char* tag, bool to_stderr);
void logi(const char* fmt, ...) __attribute__((format(printf, 1, 2)));
void logw(const char* fmt, ...) __attribute__((format(printf, 1, 2)));
void loge(const char* fmt, ...) __attribute__((format(printf, 1, 2)));

std::string strerr(int err);
std::string format(const char* fmt, ...) __attribute__((format(printf, 1, 2)));

// ---- files
std::optional<std::string> read_file(const std::string& path, size_t max = 16 << 20);
// Writes atomically (temp file + fsync + rename) with the given mode and owner.
bool write_file_atomic(const std::string& path, const std::string& data, mode_t mode = 0644,
                       uid_t uid = 0, gid_t gid = 0);
bool mkdirs(const std::string& path, mode_t mode, uid_t uid = 0, gid_t gid = 0);
bool path_exists(const std::string& path);
bool is_dir(const std::string& path);
std::string trim(const std::string& s);
std::vector<std::string> split(const std::string& s, char sep);
bool starts_with(const std::string& s, const std::string& prefix);
// Removes a directory tree without following symlinks or crossing into other mounts.
bool remove_tree(const std::string& path);
std::string get_property(const char* name, const std::string& fallback = "");

// ---- processes
void close_fds_except(const std::vector<int>& keep);
void reset_signals();
// Runs argv (absolute path) and waits; returns the wait status, or -1 if it could not start.
// stdout is captured into *out when out != nullptr.
int run(const std::vector<std::string>& argv, std::string* out = nullptr,
        const std::vector<std::string>* env = nullptr);
std::string describe_status(int status);
int exit_code_of(int status);  // shell convention: code, or 128+signal

// ---- unix socket messages: one JSON object per line, optional fds (SCM_RIGHTS) attached.
constexpr size_t kMaxLine = 256 * 1024;
constexpr int kMaxFds = 8;

class Channel {
public:
    explicit Channel(int fd = -1) : fd_(fd) {}
    ~Channel();
    Channel(const Channel&) = delete;
    Channel& operator=(const Channel&) = delete;
    Channel(Channel&& o) noexcept;
    Channel& operator=(Channel&& o) noexcept;

    int fd() const { return fd_; }
    int release();
    void close();

    bool send(const json& msg, const std::vector<int>& fds = {});
    // Blocking read of the next message. Returns false on EOF/error (errno set; 0 for EOF).
    bool recv(json* msg, std::vector<int>* fds = nullptr);
    // Non-blocking variant for poll loops: reads what is available, returns complete messages.
    // Returns false once the peer has closed or failed.
    bool pump(std::vector<std::pair<json, std::vector<int>>>* out);
    bool has_buffered() const;

private:
    bool parse_buffered();
    bool read_some(bool blocking, bool* eof);
    int fd_;
    std::string buf_;
    std::vector<int> pending_fds_;
    std::deque<std::pair<json, std::vector<int>>> ready_;
};

int connect_unix(const std::string& path);
bool peer_cred(int fd, pid_t* pid, uid_t* uid, gid_t* gid);

}  // namespace gbos
