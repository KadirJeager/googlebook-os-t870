// Installs the Linux environment: Debian's official container image (Docker Hub
// "library/debian", built by Debian's debuerreotype) is resolved through the OCI registry API,
// downloaded with the system curl, verified against its content digest, unpacked and then
// configured inside the environment by setup.sh (apt).
#pragma once

#include <string>
#include <vector>

#include "config.h"
#include "util.h"

namespace gbos {

struct InstallOptions {
    std::string user;                  // Linux account to create for the requesting Terminal
    uid_t user_uid = 0;                // its uid (the Terminal app uid of that Android user)
    int android_user = -1;
    std::vector<std::string> locales;  // e.g. tr_TR.UTF-8, first is the default LANG
    bool repair = false;               // rerun the configuration on an existing environment
};

// Image and package choices, from /system/etc/gbos-linux/linux.conf (key=value).
struct LinuxConf {
    std::string registry = "registry-1.docker.io";
    std::string auth = "https://auth.docker.io/token?service=registry.docker.io";
    std::string repository = "library/debian";
    std::string tag = "trixie";
    static LinuxConf load(const Paths& paths);
};

// State file (state.json) helpers; the worker owns the transitions.
json read_state(const Paths& paths);
bool write_state(const Paths& paths, const json& state);

// Runs in a forked worker. Progress and log lines go to `events` as JSON objects:
//   {"event":"progress","phase":P,"percent":X,...}  {"event":"log","line":L}
// Returns 0 on success; the last event is {"event":"done","ok":bool,...}.
int install_worker(const Paths& paths, const InstallOptions& opts, Channel& events);

// Creates (idempotently) the Linux account of a Terminal user; runs in a forked job.
bool create_user(const Paths& paths, const InstallOptions& opts, Channel* events, std::string* err);

// Creates (idempotently) the droid account (kDroidAccount) as a second name of the owner's
// Terminal account (droidadd.sh); runs in a forked job.
bool create_droid(const Paths& paths, const std::string& owner, uid_t uid, int android_user,
                  std::string* err);

// Generates a locale inside the environment (forked job).
bool generate_locale(const Paths& paths, const std::string& locale, std::string* err);

// Runs a shell script (text) as root inside the environment through the daemon's own spawn
// API, streaming its output lines and apt status lines (fd 3) to the callbacks.
int run_guest_script(const Paths& paths, const std::string& script,
                     const std::vector<std::string>& args,
                     const std::function<void(const std::string&)>& on_line,
                     const std::function<void(const std::string&)>& on_status, std::string* err);

}  // namespace gbos
