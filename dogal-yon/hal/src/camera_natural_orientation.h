/*
 * Googlebook OS on SM-T870: camera metadata for a landscape natural orientation
 * (dogal-yon/hal/BENIOKU.md).
 */
#pragma once

#include <system/camera_metadata.h>

#include <cstdint>
#include <vector>

namespace gbos {
namespace natural_orientation {

// When src holds an entry defined relative to the device's natural orientation
// (android.sensor.orientation, android.lens.poseRotation, android.lens.poseTranslation), writes
// a copy of src with those entries re-expressed for the landscape natural orientation to *out
// and returns true; returns false and leaves *out alone when there is nothing to change.
bool rotate_camera_metadata(const camera_metadata_t* src, std::vector<uint8_t>* out);

}  // namespace natural_orientation
}  // namespace gbos
