// RO_VPD of the tablet: the factory data a Googlebook reads from its firmware flash, limited to
// what this tablet really knows about itself.
//
// Only keys whose value the tablet provides are present:
//   serial_number  the device serial number the bootloader reports (ro.boot.serialno, else
//                  ro.serialno)
//   model_name     the model the Samsung bootloader reports (ro.boot.em.model, else the vendor
//                  partition's ro.product.vendor.model)
// Every other key the Googlebook callers ask for (mlb_serial_number, imei, meid, mfg_sku_id,
// region, oem_key2, kb_info, ubind_attribute, ...) has no source on this tablet and is absent,
// which callers see exactly as on a Googlebook whose VPD lacks the key: an entry with a null
// value. Nothing is invented.
#pragma once

#include <functional>
#include <string>
#include <vector>

#include "vpd_store.h"

namespace gbos::vpd {

// Reads a system property; returns "" when it is unset.
using PropertyReader = std::function<std::string(const char* name)>;

std::vector<Entry> DeriveRoEntries(const PropertyReader& get_property);

}  // namespace gbos::vpd
