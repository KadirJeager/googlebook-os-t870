/*
 * Googlebook OS on SM-T870: camera metadata for a landscape natural orientation
 * (dogal-yon/hal/BENIOKU.md).
 *
 * android.sensor.orientation is the clockwise rotation that makes the sensor image upright in the
 * natural orientation; android.lens.poseRotation (x, y, z, w: Android sensor coordinates ->
 * camera coordinates) and android.lens.poseTranslation (Android sensor coordinates) are expressed
 * in the device frame of the natural orientation. natural_orientation.h has the transforms.
 * Poses relative to an automotive frame and the orientation of external cameras do not depend on
 * the device frame and stay.
 */
#define LOG_TAG "gbos-dogal-yon"

#include "camera_natural_orientation.h"

#include <log/log.h>
#include <natural_orientation.h>

namespace gbos {
namespace natural_orientation {

namespace {

bool find_u8(const camera_metadata_t* md, uint32_t tag, uint8_t* value) {
    camera_metadata_ro_entry_t e;
    if (find_camera_metadata_ro_entry(md, tag, &e) != 0 || e.count != 1 || e.type != TYPE_BYTE) {
        return false;
    }
    *value = e.data.u8[0];
    return true;
}

bool update(camera_metadata_t* md, uint32_t tag, const void* data, size_t count) {
    camera_metadata_entry_t e;
    return find_camera_metadata_entry(md, tag, &e) == 0 &&
           update_camera_metadata_entry(md, e.index, data, count, nullptr) == 0;
}

}  // namespace

bool rotate_camera_metadata(const camera_metadata_t* src, std::vector<uint8_t>* out) {
    camera_metadata_ro_entry_t orientation, rotation, translation;
    const bool hasOrientation =
            find_camera_metadata_ro_entry(src, ANDROID_SENSOR_ORIENTATION, &orientation) == 0 &&
            orientation.count == 1 && orientation.type == TYPE_INT32;
    const bool hasRotation =
            find_camera_metadata_ro_entry(src, ANDROID_LENS_POSE_ROTATION, &rotation) == 0 &&
            rotation.count == 4 && rotation.type == TYPE_FLOAT;
    const bool hasTranslation =
            find_camera_metadata_ro_entry(src, ANDROID_LENS_POSE_TRANSLATION, &translation) == 0 &&
            translation.count == 3 && translation.type == TYPE_FLOAT;
    if (!hasOrientation && !hasRotation && !hasTranslation) return false;

    uint8_t facing = 0xff, reference = ANDROID_LENS_POSE_REFERENCE_PRIMARY_CAMERA;
    const bool hasFacing = find_u8(src, ANDROID_LENS_FACING, &facing);
    find_u8(src, ANDROID_LENS_POSE_REFERENCE, &reference);
    const bool external = hasFacing && facing == ANDROID_LENS_FACING_EXTERNAL;
    const bool rotateOrientation = hasOrientation && hasFacing && !external;
    const bool rotatePose = !external && reference != ANDROID_LENS_POSE_REFERENCE_AUTOMOTIVE &&
                            (hasRotation || hasTranslation);
    if (hasOrientation && !hasFacing) {
        ALOGE("sensor orientation without lens facing; left at %d", orientation.data.i32[0]);
    }
    if (!rotateOrientation && !rotatePose) return false;

    camera_metadata_t* copy = clone_camera_metadata(src);
    if (copy == nullptr) {
        ALOGE("cannot copy camera metadata; left unchanged");
        return false;
    }
    bool ok = true;
    if (rotateOrientation) {
        const int32_t before = orientation.data.i32[0];
        const int32_t after = rotate_camera_orientation(before, facing, kPanelRotation);
        ok &= update(copy, ANDROID_SENSOR_ORIENTATION, &after, 1);
        ALOGI("natural orientation: lens facing %u, sensor orientation %d -> %d", facing, before,
              after);
    }
    if (rotatePose && hasRotation) {
        float q[4] = {rotation.data.f[0], rotation.data.f[1], rotation.data.f[2],
                      rotation.data.f[3]};
        rotate_quaternion(q, kPanelRotation);
        ok &= update(copy, ANDROID_LENS_POSE_ROTATION, q, 4);
    }
    if (rotatePose && hasTranslation) {
        float t[3] = {translation.data.f[0], translation.data.f[1], translation.data.f[2]};
        rotate_vector(&t[0], &t[1], kPanelRotation);
        ok &= update(copy, ANDROID_LENS_POSE_TRANSLATION, t, 3);
    }
    if (ok) {
        const uint8_t* bytes = reinterpret_cast<const uint8_t*>(copy);
        out->assign(bytes, bytes + get_camera_metadata_size(copy));
    } else {
        ALOGE("cannot update camera metadata; left unchanged");
    }
    free_camera_metadata(copy);
    return ok;
}

}  // namespace natural_orientation
}  // namespace gbos
