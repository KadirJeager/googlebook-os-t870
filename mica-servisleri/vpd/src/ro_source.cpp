#include "ro_source.h"

#include <initializer_list>

namespace gbos::vpd {
namespace {

struct RoKey {
    const char* key;
    std::initializer_list<const char*> properties;  // first one with a real value wins
};

const RoKey kRoKeys[] = {
        {"serial_number", {"ro.boot.serialno", "ro.serialno"}},
        {"model_name", {"ro.boot.em.model", "ro.product.vendor.model"}},
};

std::string Trim(const std::string& s) {
    const char* ws = " \t\r\n";
    size_t b = s.find_first_not_of(ws);
    if (b == std::string::npos) return "";
    return s.substr(b, s.find_last_not_of(ws) - b + 1);
}

// Android reports an unknown serial number as "unknown" (Build.UNKNOWN); that is not a value.
bool IsRealValue(const std::string& v) {
    return !v.empty() && v != "unknown" && IsValidValue(v);
}

}  // namespace

std::vector<Entry> DeriveRoEntries(const PropertyReader& get_property) {
    std::vector<Entry> entries;
    for (const RoKey& k : kRoKeys) {
        for (const char* prop : k.properties) {
            std::string v = Trim(get_property(prop));
            if (IsRealValue(v)) {
                entries.push_back({k.key, v});
                break;
            }
        }
    }
    return entries;
}

}  // namespace gbos::vpd
