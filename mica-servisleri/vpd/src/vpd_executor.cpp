#include "vpd_executor.h"

#include <aidl/vendor/google/desktop/vpd_executor/VpdRegion.h>
#include <android/binder_ibinder.h>

#include "log.h"

namespace gbos::vpd {
namespace {

using aidl::vendor::google::desktop::vpd_executor::VpdRegion;

constexpr uid_t kRootUid = 0;
constexpr uid_t kSystemUid = 1000;

ndk::ScopedAStatus ServiceError(const std::string& message) {
    return ndk::ScopedAStatus::fromServiceSpecificErrorWithMessage(VpdExecutor::kServiceError,
                                                                  message.c_str());
}

ndk::ScopedAStatus IllegalArgument(const std::string& message) {
    return ndk::ScopedAStatus::fromExceptionCodeWithMessage(EX_ILLEGAL_ARGUMENT, message.c_str());
}

ndk::ScopedAStatus FromResult(const Result& r) {
    switch (r.status) {
        case Status::kOk:
            return ndk::ScopedAStatus::ok();
        case Status::kInvalidArgument:
            return IllegalArgument(r.message);
        case Status::kNoSpace:
        case Status::kIoError:
            return ServiceError(r.message);
    }
    return ServiceError(r.message);
}

bool IsRo(int8_t region) { return region == static_cast<int8_t>(VpdRegion::RO); }
bool IsRw(int8_t region) { return region == static_cast<int8_t>(VpdRegion::RW); }

std::string BadRegion(int8_t region) { return "invalid VPD region " + std::to_string(region); }

}  // namespace

VpdExecutor::VpdExecutor(std::vector<Entry> ro_entries, RwStore* rw_store)
    : ro_entries_(std::move(ro_entries)), rw_store_(rw_store) {}

ndk::ScopedAStatus VpdExecutor::CheckCaller() const {
    uid_t uid = AIBinder_getCallingUid();
    if (uid == kRootUid || uid == kSystemUid) return ndk::ScopedAStatus::ok();
    LOGW("refused call from uid %d", static_cast<int>(uid));
    return ndk::ScopedAStatus::fromExceptionCodeWithMessage(EX_SECURITY,
                                                            "caller may not access the VPD");
}

Result VpdExecutor::EnsureRwLoaded() {
    if (rw_store_->loaded()) return {};
    Result r = rw_store_->Load();
    if (!r.ok()) LOGE("RW_VPD store unavailable: %s", r.message.c_str());
    return r;
}

ndk::ScopedAStatus VpdExecutor::getEntries(int8_t region, std::vector<AidlEntry>* out) {
    if (auto s = CheckCaller(); !s.isOk()) return s;
    out->clear();
    if (IsRo(region)) {
        for (const Entry& e : ro_entries_) out->push_back({e.key, e.value});
        return ndk::ScopedAStatus::ok();
    }
    if (!IsRw(region)) return IllegalArgument(BadRegion(region));
    std::lock_guard<std::mutex> lock(mutex_);
    if (Result r = EnsureRwLoaded(); !r.ok()) return FromResult(r);
    for (const Entry& e : rw_store_->entries()) out->push_back({e.key, e.value});
    return ndk::ScopedAStatus::ok();
}

ndk::ScopedAStatus VpdExecutor::getValue(int8_t region, const std::string& key,
                                         std::optional<AidlEntry>* out) {
    if (auto s = CheckCaller(); !s.isOk()) return s;
    AidlEntry entry;
    entry.key = key;
    if (IsRo(region)) {
        for (const Entry& e : ro_entries_) {
            if (e.key == key) entry.value = e.value;
        }
    } else if (IsRw(region)) {
        std::lock_guard<std::mutex> lock(mutex_);
        if (Result r = EnsureRwLoaded(); !r.ok()) return FromResult(r);
        if (auto v = rw_store_->Get(key)) entry.value = *v;
    } else {
        return IllegalArgument(BadRegion(region));
    }
    *out = std::move(entry);
    return ndk::ScopedAStatus::ok();
}

ndk::ScopedAStatus VpdExecutor::updateEntries(int8_t region,
                                              const std::vector<AidlEntry>& entries) {
    if (auto s = CheckCaller(); !s.isOk()) return s;
    if (IsRo(region)) {
        return ServiceError("RO_VPD is read-only: it holds the tablet's own factory data");
    }
    if (!IsRw(region)) return IllegalArgument(BadRegion(region));
    std::vector<Update> updates;
    updates.reserve(entries.size());
    std::string keys;
    for (const AidlEntry& e : entries) {
        if (!e.key) return IllegalArgument("VPD entry without a key");
        updates.push_back({*e.key, e.value});
        keys += (keys.empty() ? "" : ", ") + *e.key + (e.value ? "" : " (delete)");
    }
    std::lock_guard<std::mutex> lock(mutex_);
    if (Result r = EnsureRwLoaded(); !r.ok()) return FromResult(r);
    Result r = rw_store_->Apply(updates);
    if (r.ok()) {
        LOGI("RW_VPD updated: %s", keys.c_str());
    } else {
        LOGE("RW_VPD update of %s failed: %s", keys.c_str(), r.message.c_str());
    }
    return FromResult(r);
}

}  // namespace gbos::vpd
