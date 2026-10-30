// gbos-vpd-executor: lazy AIDL service vendor.google.desktop.vpd_executor.IVpdExecutor/default.
//
// init starts it on demand (interface aidl ... / disabled / oneshot in gbos-vpd-executor.rc)
// when a client asks servicemanager for the service; it registers as a lazy service and exits
// once no client holds it, like mica's own executor.
#include <stdlib.h>
#include <sys/system_properties.h>

#include <android/binder_manager.h>
#include <android/binder_process.h>

#include "log.h"
#include "ro_source.h"
#include "vpd_executor.h"
#include "vpd_store.h"

namespace {

constexpr const char* kStoreDir = "/data/misc/gbos_vpd";

std::string GetProperty(const char* name) {
    std::string value;
    const prop_info* pi = __system_property_find(name);
    if (pi == nullptr) return value;
    __system_property_read_callback(
            pi,
            [](void* cookie, const char*, const char* v, uint32_t) {
                *static_cast<std::string*>(cookie) = v;
            },
            &value);
    return value;
}

}  // namespace

int main() {
    using gbos::vpd::VpdExecutor;

    // One binder thread serves the occasional calls; the main thread joins the pool.
    ABinderProcess_setThreadPoolMaxThreadCount(0);

    gbos::vpd::RwStore store(kStoreDir);
    if (gbos::vpd::Result r = store.Load(); !r.ok()) {
        // Reads of RO_VPD still work; RW calls retry the load and report the error until then.
        LOGE("RW_VPD store unavailable: %s", r.message.c_str());
    }

    auto service = ndk::SharedRefBase::make<VpdExecutor>(gbos::vpd::DeriveRoEntries(GetProperty),
                                                         &store);
    const std::string name = std::string(VpdExecutor::descriptor) + "/default";
    binder_status_t status =
            AServiceManager_registerLazyService(service->asBinder().get(), name.c_str());
    if (status != STATUS_OK) {
        LOGE("cannot register %s: status %d", name.c_str(), status);
        return EXIT_FAILURE;
    }
    LOGI("registered %s", name.c_str());
    ABinderProcess_joinThreadPool();
    return EXIT_FAILURE;  // joinThreadPool does not return
}
