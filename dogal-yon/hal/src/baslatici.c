/*
 * Googlebook OS on SM-T870: starts a vendor HAL that runs from /product (dogal-yon/hal/BENIOKU.md).
 *
 * The HAL itself is a library built exactly like the LineageOS vendor binary: against the vendor
 * partition's own shared libraries (libc++, libutils, libhidlbase, ...), so every C++ object it
 * shares with the vendor sub-HALs and the camera module comes from one copy of those libraries.
 * An executable under /product runs in linkerconfig's "product" section, whose namespaces cannot
 * reach /vendor/lib64 except through the exported "sphal" namespace. This launcher
 *
 *   1. loads the HAL library's vendor dependencies (GBOS_ON_YUKLEME, its DT_NEEDED entries that
 *      are not LLNDK) into "sphal", the namespace libvndksupport/libhidl also load vendor
 *      modules into (android_load_sphal_library), so each vendor library exists once;
 *   2. creates a namespace that clones "sphal" (search paths, permitted paths, LLNDK links and the
 *      libraries now loaded in it: ANDROID_NAMESPACE_TYPE_SHARED, bionic linker.cpp
 *      create_namespace) and additionally sees <root>/lib64/gbos-dogal-yon;
 *   3. loads <root>/lib64/gbos-dogal-yon/<own name>.so there and runs its gbos_hal_main(), the
 *      vendor service's unchanged main().
 *
 * <root> is the launcher's own location, <root>/bin/hw/<own name>: /product on the device, or
 * /data/local/tests/product for a manual test (also a "product" section directory in the
 * tablet's ld.config.txt). Any failure is logged and the process exits non-zero; init restarts it
 * like any crashed HAL.
 */
#include <android/dlext.h>
#include <android/log.h>
#include <dlfcn.h>
#include <limits.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/auxv.h>

#include "bionic/dlext_namespaces.h"

#ifndef GBOS_ON_YUKLEME
#error "GBOS_ON_YUKLEME: the HAL library's vendor DT_NEEDED entries, as string literals"
#endif

#define ETIKET "gbos-dogal-yon"
#define KUTUPHANE_DIZINI "/lib64/gbos-dogal-yon"
#define ALT_YOL "/bin/hw/"

static const char* const kOnYukleme[] = {GBOS_ON_YUKLEME};

static int hata(const char* fmt, ...) {
    char msg[1024];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(msg, sizeof(msg), fmt, ap);
    va_end(ap);
    __android_log_write(ANDROID_LOG_FATAL, ETIKET, msg);
    fprintf(stderr, "%s: %s\n", ETIKET, msg);
    return 1;
}

int main(void) {
    const char* exe = (const char*)getauxval(AT_EXECFN);
    if (exe == NULL || exe[0] != '/') return hata("no absolute AT_EXECFN");
    const char* name = strrchr(exe, '/') + 1;
    const size_t prefix = (size_t)(name - exe), sub = strlen(ALT_YOL);
    if (*name == '\0' || prefix < sub + 1 || memcmp(exe + prefix - sub, ALT_YOL, sub) != 0)
        return hata("%s is not <root>" ALT_YOL "<name>", exe);
    const int root_len = (int)(prefix - sub);

    char dir[PATH_MAX], lib[PATH_MAX], ns_name[128];
    if (snprintf(dir, sizeof(dir), "%.*s" KUTUPHANE_DIZINI, root_len, exe) >= (int)sizeof(dir) ||
        snprintf(lib, sizeof(lib), "%s/%s.so", dir, name) >= (int)sizeof(lib))
        return hata("path too long: %s", exe);
    snprintf(ns_name, sizeof(ns_name), "gbos_%s", name);

    struct android_namespace_t* sphal = android_get_exported_namespace("sphal");
    if (sphal == NULL) return hata("linker namespace \"sphal\" is not exported here");

    const android_dlextinfo in_sphal = {.flags = ANDROID_DLEXT_USE_NAMESPACE, .library_namespace = sphal};
    for (size_t i = 0; i < sizeof(kOnYukleme) / sizeof(kOnYukleme[0]); i++) {
        if (android_dlopen_ext(kOnYukleme[i], RTLD_NOW | RTLD_NODELETE, &in_sphal) == NULL)
            return hata("sphal: %s", dlerror());
    }

    struct android_namespace_t* ns = android_create_namespace(
            ns_name, dir, dir, ANDROID_NAMESPACE_TYPE_SHARED_ISOLATED, dir, sphal);
    if (ns == NULL) return hata("android_create_namespace(%s): %s", dir, dlerror());

    const android_dlextinfo in_ns = {.flags = ANDROID_DLEXT_USE_NAMESPACE, .library_namespace = ns};
    void* handle = android_dlopen_ext(lib, RTLD_NOW | RTLD_NODELETE, &in_ns);
    if (handle == NULL) return hata("%s", dlerror());
    int (*hal_main)(void) = (int (*)(void))dlsym(handle, "gbos_hal_main");
    if (hal_main == NULL) return hata("%s: %s", lib, dlerror());

    __android_log_print(ANDROID_LOG_INFO, ETIKET, "%s: starting %s", name, lib);
    return hal_main();
}
