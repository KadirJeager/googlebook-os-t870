// The Linux environment: a Debian root filesystem under /data, entered through its own mount and
// UTS namespaces (pivot_root, so Android's own filesystem is not reachable from inside).
#pragma once

#include <sys/types.h>

#include <array>
#include <optional>
#include <string>
#include <vector>

#include "config.h"
#include "util.h"

namespace gbos {

struct LinuxUser {
    std::string name;
    uid_t uid = 0;
    gid_t gid = 0;
    std::string home;
    std::string shell;
    std::vector<gid_t> groups;  // supplementary groups from /etc/group
};

// Reads the environment's /etc/passwd and /etc/group (never the Android account database).
// By uid: the account that owns the uid, never the droid account sharing it (kDroidAccount).
std::optional<LinuxUser> find_user_by_uid(const std::string& rootfs, uid_t uid);
std::optional<LinuxUser> find_user_by_name(const std::string& rootfs, const std::string& name);
bool valid_user_name(const std::string& name);

struct BindMount {
    std::string host;   // absolute host path (opened by the daemon before entering)
    std::string guest;  // absolute path inside the environment
    bool read_only = false;
};

struct SpawnRequest {
    bool tty = false;
    int rows = 24, cols = 80;
    std::vector<std::string> argv;  // empty: the user's login shell
    std::string cwd;                // empty: home
    std::vector<std::string> env;   // KEY=VALUE, already validated
    LinuxUser user;
    int android_user = -1;          // Android user whose Terminal opened the session
    std::vector<BindMount> binds;   // private to this session (root callers only)
    std::vector<int> stdio;  // non-tty sessions: the caller's fds, become 0..n-1 (n <= 4)
};

class Container {
public:
    explicit Container(const Paths& paths) : paths_(paths) {}
    ~Container();

    bool running() const { return mnt_fd_ >= 0; }
    // Mount points in Android's namespace that reach into the environment by propagation
    // (homes and per-session binds): a mount made in another namespace cannot be bind-mounted
    // from inside (EINVAL before Linux 5.2's open_tree), but its propagation can.
    bool prepare_host_mounts(std::string* err);
    // Creates the namespaces and mounts; no-op when already running.
    bool start(std::string* err);
    // Finds an environment left running by a previous daemon instance.
    bool adopt();
    // Terminates every process inside and releases the namespaces.
    void stop(int grace_ms);
    std::vector<pid_t> processes() const;
    // Processes inside whose real uid is `uid` (a Terminal user's Linux programs).
    std::vector<pid_t> processes_of(uid_t uid) const;

    // Bind-mounts the user's credential-encrypted home at its passwd home directory (the
    // droid account has its own directory next to the owner's), makes the user's shared
    // storage reachable (/mnt/android/<user>, /storage/emulated/<user>).
    bool ensure_home(int android_user, const LinuxUser& user, std::string* err);
    // /mnt/storage/<user>: the user's shared storage with owner and modes mapped (bindfs), or
    // a plain bind when bindfs is not installed; repairs a view whose bindfs ended.
    bool ensure_storage_view(int android_user, const LinuxUser& user, std::string* err);
    // Writes one chunk of a file below the user's home as that user (no privileges, no
    // symlinks followed): offset 0 starts a hidden partial file, later chunks must continue it,
    // `final` moves it into place with `mode`. `rel` must pass droid_path_problem().
    bool write_home_file(const LinuxUser& user, const std::string& rel, const std::string& data,
                         uint64_t offset, bool final, mode_t mode, std::string* err);
    // Keeps /etc/resolv.conf, /etc/hosts, /etc/hostname, /etc/localtime and the integration
    // helpers current (called on start and before each session).
    void refresh_guest_files();

    // Forks the session monitor, which takes over `conn` and reports the exit status.
    pid_t spawn(Channel& conn, SpawnRequest& req, std::string* err);

    // Runs a command as root inside the environment and waits (setup and maintenance jobs).
    // stdout/stderr go to out_fd (or /dev/null when -1); returns the wait status or -1.
    int run_root(const std::vector<std::string>& argv, int out_fd, int extra_fd = -1);

private:
    bool rootfs_identity(dev_t* dev, ino_t* ino) const;
    bool enter(std::string* err) const;  // setns into the environment (forked children only)

    Paths paths_;
    int mnt_fd_ = -1;
    int uts_fd_ = -1;
};

// Capabilities kept in the bounding set of everything started inside: Docker's default set
// without CAP_MKNOD (there is no device cgroup to confine new device nodes).
bool drop_capabilities_for_guest();

}  // namespace gbos
