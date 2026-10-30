#include "config.h"

#include <stdlib.h>

#include "util.h"

namespace gbos {

namespace {
std::string env_or(const char* name, const char* fallback) {
    const char* v = getenv(name);
    return v && *v ? v : fallback;
}
}  // namespace

Paths Paths::get() {
    Paths p;
    p.base = env_or("GBOS_LINUX_BASE", "/data/gbos-linux");
    p.rootfs = p.base + "/rootfs";
    p.cache = p.base + "/cache";
    p.run = p.base + "/run";
    p.state = p.base + "/state.json";
    p.homes = p.base + "/homes";
    p.exchange = p.base + "/exchange";
    p.sysdir = env_or("GBOS_LINUX_SYSDIR", kSystemDir);
    p.socket = env_or("GBOS_LINUX_SOCKET", kControlSocketPath);
    p.apk = env_or("GBOS_LINUX_APK", "/system/priv-app/Terminal/Terminal.apk");
    return p;
}

std::string ce_home_dir(int android_user) {
    return format("/data/misc_ce/%d/gbos-linux/home", android_user);
}

std::string ce_droid_dir(int android_user) {
    return format("/data/misc_ce/%d/gbos-linux/%s", android_user, kDroidAccount);
}

}  // namespace gbos
