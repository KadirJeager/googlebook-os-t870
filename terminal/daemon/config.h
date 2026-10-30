// Paths and fixed names shared by the daemon, the client and the image installer
// (yamalar/terminal.py writes the files these paths point to).
#pragma once

#include <string>

namespace gbos {

// Control socket created by init ("socket gbos_linux stream 0666 root root ...").
constexpr const char* kControlSocketName = "gbos_linux";
constexpr const char* kControlSocketPath = "/dev/socket/gbos_linux";

// The Terminal app: its uid (in any Android user) may open sessions as its own Linux user.
constexpr const char* kTerminalPackage = "com.gbos.terminal";

// Read-only files shipped in the system image.
constexpr const char* kSystemDir = "/system/etc/gbos-linux";

// Runtime state on /data (device-encrypted, available from boot).
struct Paths {
    std::string base;      // /data/gbos-linux
    std::string rootfs;    // base/rootfs
    std::string cache;     // base/cache: downloaded image layers
    std::string run;       // base/run: host.sock, bind-mounted to /run/googlebook inside
    std::string state;     // base/state.json
    std::string sysdir;    // /system/etc/gbos-linux
    std::string socket;    // control socket path (clients)
    std::string apk;       // Terminal.apk: com.gbos.terminal.Fetch runs from it (HTTPS)
    std::string homes;     // base/homes: shared mount, the environment's /home
    std::string exchange;  // base/exchange: shared mount for per-session binds

    static Paths get();    // honours GBOS_LINUX_{BASE,SYSDIR,SOCKET,APK}
};

// Per Android user, credential-encrypted: the Linux home of that user's Terminal identity.
std::string ce_home_dir(int android_user);
// Per Android user, credential-encrypted: the home of the droid account (kDroidAccount).
std::string ce_droid_dir(int android_user);

// Android's Linux terminal (com.android.virtualization.terminal) runs its VM as the user
// "droid" with home /home/droid and the owner's shared storage at /mnt/shared; apps written for
// it (Antigravity) log in as that user and use those paths. The environment offers the same
// account as a second name of one Android user's Terminal account: same uid and gid (so Android
// treats its processes, network and files as the Terminal's), its own home. It belongs to the
// first Android user that asks for it.
constexpr const char* kDroidAccount = "droid";
constexpr const char* kDroidShared = "/mnt/shared";
// Largest file an app may copy into the droid home (Copy & Run), and the size of one chunk.
constexpr size_t kDroidFileMax = 64u << 20;
constexpr size_t kDroidChunkMax = 96u << 10;

// Inside the environment.
constexpr const char* kHostname = "googlebook";
constexpr const char* kGuestRunDir = "/run/googlebook";
constexpr const char* kGuestHostSocket = "/run/googlebook/host.sock";
constexpr const char* kGuestAndroidStorage = "/mnt/android";  // /mnt/android/<user> = /mnt/user/<user>
constexpr const char* kGuestAndroidUsers = "/mnt/.android-users";  // rbind of /mnt/user (root only)
constexpr const char* kGuestExchange = "/run/.gbos-exchange";  // slave of base/exchange
// Android's own path of a user's shared storage, /storage/emulated/<user>, exists inside too
// (a link to kGuestStorageView/<user>): paths Android apps show work unchanged.
constexpr const char* kGuestEmulatedStorage = "/storage/emulated";
// The user's shared storage as Linux uses it (owner and modes mapped, Container::
// ensure_storage_view); /storage/emulated/<user>, ~/android and /mnt/shared lead here.
constexpr const char* kGuestStorageView = "/mnt/storage";
// argv[0] of the bindfs process serving a view (not counted among the user's programs).
constexpr const char* kStorageViewName = "gbos-storage-view";
constexpr const char* kDnsAddress = "127.0.0.53";

// SELinux domain the guest processes run in (written to /proc/thread-self/attr/exec).
constexpr const char* kGuestSeLabel = "u:r:gbos_linux:s0";

// Android uid layout (system/core/libcutils/include/private/android_filesystem_config.h).
constexpr int kPerUserRange = 100000;
constexpr int kAidShell = 2000;
constexpr int kAidInet = 3003;
constexpr int kAidNetRaw = 3004;
constexpr int kAidEverybody = 9997;  // apps' group for /mnt/user/<user> (shared storage)

// Exit codes of `gbos-linux run`.
constexpr int kExitToolError = 125;
constexpr int kExitNotInstalled = 69;  // EX_UNAVAILABLE

}  // namespace gbos
