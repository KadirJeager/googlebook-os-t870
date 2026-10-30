// vendor.google.desktop.vpd_executor.IVpdExecutor/default for the SM-T870.
//
// Behaviour follows mica's executor (vendor/google/desktop/vpd-executor, which runs
// `vpd -i RO_VPD|RW_VPD -l|-g|-s|-d`) as its callers observe it:
//   getEntries   every entry of the region
//   getValue     VpdEntry{key, value}; value is null when the region has no such key
//   updateEntries sets each entry, deletes it when the value is null
//   failures     ServiceSpecificException(-1, message), the code the original returns
// Regions other than RO (0) and RW (1) are rejected with IllegalArgumentException, and RO_VPD is
// read-only: it holds factory data, which on this tablet is derived from the device itself.
#pragma once

#include <mutex>

#include <aidl/vendor/google/desktop/vpd_executor/BnVpdExecutor.h>

#include "ro_source.h"
#include "vpd_store.h"

namespace gbos::vpd {

class VpdExecutor : public aidl::vendor::google::desktop::vpd_executor::BnVpdExecutor {
  public:
    using AidlEntry = aidl::vendor::google::desktop::vpd_executor::VpdEntry;

    VpdExecutor(std::vector<Entry> ro_entries, RwStore* rw_store);

    ndk::ScopedAStatus getEntries(int8_t region, std::vector<AidlEntry>* out) override;
    ndk::ScopedAStatus getValue(int8_t region, const std::string& key,
                                std::optional<AidlEntry>* out) override;
    ndk::ScopedAStatus updateEntries(int8_t region, const std::vector<AidlEntry>& entries) override;

    // The error code mica's executor puts in every ServiceSpecificException.
    static constexpr int32_t kServiceError = -1;

  private:
    // Callers are the platform's own services; anything else is refused.
    ndk::ScopedAStatus CheckCaller() const;
    // Loads the RW store if an earlier attempt failed. Requires mutex_.
    Result EnsureRwLoaded();

    const std::vector<Entry> ro_entries_;
    std::mutex mutex_;
    RwStore* const rw_store_;  // guarded by mutex_
};

}  // namespace gbos::vpd
