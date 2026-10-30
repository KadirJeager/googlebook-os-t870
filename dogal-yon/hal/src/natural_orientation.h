/*
 * Googlebook OS on SM-T870: the frame change of a landscape natural orientation
 * (dogal-yon/hal/BENIOKU.md, yamalar/dogal_yon.py).
 *
 * The panel scans out portrait. With ro.surface_flinger.primary_display_orientation =
 * ORIENTATION_<R>, the device's natural orientation is the panel rotated R degrees clockwise
 * (R = GBOS_PANEL_ROTATION, yamalar/dogal_yon.py PANEL_ROTATION, the single source). The Android
 * sensor coordinate system (X right, Y up, Z out of the screen, in the natural orientation) and
 * the camera orientation values are defined relative to the natural orientation, so everything
 * the vendor HALs report relative to the portrait panel is re-expressed here:
 *
 *   vector v (old frame) -> R_z(R) v : for R = 90, (x, y, z) -> (-y, x, z); the panel's right edge
 *                                       (old +X) is the new top (+Y)
 *   device orientation d -> (d - R/90) mod 4
 *   quaternion q (device -> world)   -> q * q_z(-R), so that q' rotates new-frame vectors to the
 *                                       same world vectors (v_old = R_z(-R) v_new)
 *   heading h (degrees from north of the device's +Y) -> h + R
 *   sensor placement [R | t] (AINFO_SENSOR_PLACEMENT) -> [R R_z(R)^T | R_z(R) t]
 *   camera sensor orientation O      -> O - R (back), O + R (front); both keep JPEG orientation
 *                                       and preview transforms physically unchanged, because the
 *                                       display rotation the framework pairs them with changes
 *                                       by the same R (CameraCharacteristics#SENSOR_ORIENTATION)
 *
 * The inverse of every transform is the same transform with -R (sensor data injection).
 *
 * No Android dependency: testler/test_dogal_yon_hal.py compiles this header for the host and
 * checks it against independent float math and real tablet readings.
 */
#pragma once

#include <cmath>
#include <cstdint>
#include <cstring>

#ifndef GBOS_PANEL_ROTATION
#error "GBOS_PANEL_ROTATION (yamalar/dogal_yon.py PANEL_ROTATION) must be defined"
#endif

namespace gbos {
namespace natural_orientation {

inline constexpr int kPanelRotation = GBOS_PANEL_ROTATION;
static_assert(kPanelRotation >= 0 && kPanelRotation < 360 && kPanelRotation % 90 == 0,
              "PANEL_ROTATION must be 0, 90, 180 or 270");

// Rotation by r degrees (any multiple of 90, negative allowed) as exact integer cos/sin.
struct Quarter {
    int c, s;
};
constexpr int normalize(int r) {
    return ((r % 360) + 360) % 360;
}
constexpr Quarter quarter(int r) {
    switch (normalize(r)) {
        case 0: return {1, 0};
        case 90: return {0, 1};
        case 180: return {-1, 0};
        default: return {0, -1};
    }
}

// A vector's (x, y) in the old device frame -> the new frame: R_z(r) (x, y).
inline void rotate_vector(float* x, float* y, int r) {
    const Quarter q = quarter(r);
    const float ox = *x, oy = *y;
    *x = q.c * ox - q.s * oy;
    *y = q.s * ox + q.c * oy;
}

// Per-axis "supported" flags (LIMITED_AXES sensors) follow their axis, without sign.
inline void rotate_axis_flags(float* xs, float* ys, int r) {
    if (normalize(r) % 180 != 0) {
        const float t = *xs;
        *xs = *ys;
        *ys = t;
    }
}

// q (x, y, z, w), the rotation from the old device frame to the world frame, -> q * q_z(-r).
// Hemisphere w >= 0 is kept, so x, y, z alone still determine the rotation (the contract the
// rotation vector sensors had before values[3] existed, SensorManager.getRotationMatrixFromVector).
inline void rotate_quaternion(float q[4], int r) {
    // q_z(-r) = (0, 0, -sin(r/2), cos(r/2)); exact for multiples of 90.
    constexpr float h = 0.70710678118654752440f;
    float a, c;
    switch (normalize(r)) {
        case 0: a = 1; c = 0; break;
        case 90: a = h; c = -h; break;
        case 180: a = 0; c = -1; break;
        default: a = -h; c = -h; break;
    }
    const float x = q[0], y = q[1], z = q[2], w = q[3];
    q[0] = x * a + y * c;
    q[1] = y * a - x * c;
    q[2] = z * a + w * c;
    q[3] = w * a - z * c;
    if (q[3] < 0) {
        for (int i = 0; i < 4; i++) q[i] = -q[i];
    }
}

// DEVICE_ORIENTATION value 0..3 (counter-clockwise quarter turns from the natural orientation).
inline float rotate_device_orientation(float d, int r) {
    const int v = static_cast<int>(std::lround(d));
    return static_cast<float>(((v - normalize(r) / 90) % 4 + 4) % 4);
}

// HEADING degrees in [0, 360).
inline float rotate_heading(float h, int r) {
    float v = std::fmod(h + static_cast<float>(normalize(r)), 360.0f);
    return v < 0 ? v + 360.0f : v;
}

// AINFO_SENSOR_PLACEMENT, 3x4 row-major [R | t] (AdditionalInfo.aidl): R takes a device-frame
// vector into the sensor's local frame, t is the sensor's position in the device frame. With
// v_old = R_z^T v_new: [R R_z^T | R_z t]. Each row of R is a covector: (r0, r1) -> R_z (r0, r1).
inline void rotate_placement(float m[12], int r) {
    for (int i = 0; i < 3; i++) rotate_vector(&m[4 * i], &m[4 * i + 1], r);
    rotate_vector(&m[3], &m[7], r);
}

// Camera SENSOR_ORIENTATION (degrees). lens_facing: ANDROID_LENS_FACING_* (0 front, 1 back,
// 2 external); external cameras have no orientation relative to the device and keep theirs.
inline int32_t rotate_camera_orientation(int32_t o, uint8_t lens_facing, int r) {
    if (lens_facing == 0) return normalize(o + r);
    if (lens_facing == 1) return normalize(o - r);
    return o;
}

// How a sensor type's payload depends on the device frame. Numbers: AOSP SensorType
// (hardware/interfaces sensors/aidl, frozen V3); testler/test_dogal_yon_hal.py checks that every
// type of that file is listed here.
enum class Kind : uint8_t {
    kUnchanged,        // scalar, event or own-frame data (HEAD_TRACKER: head frame)
    kVector3,          // data[0..2]
    kUncalibrated3,    // data[0..2] value, data[3..5] bias
    kLimitedAxes,      // data[0..2] value, data[3..5] supported flags
    kLimitedAxesUncal, // data[0..2] value, data[3..5] bias, data[6..8] supported flags
    kQuaternion,       // data[0..3] x, y, z, w (data[4]: heading accuracy, unchanged)
    kDeviceOrientation,// data[0]
    kHeading,          // data[0] degrees (data[1]: accuracy, unchanged)
    kAdditionalInfo,   // AdditionalInfo: type, serial, 14 payload words
    kHidden,           // device-frame data without a defined transform: not offered
};

constexpr Kind kind_of(int32_t sensor_type) {
    switch (sensor_type) {
        case 1:   // ACCELEROMETER
        case 2:   // MAGNETIC_FIELD
        case 4:   // GYROSCOPE
        case 9:   // GRAVITY
        case 10:  // LINEAR_ACCELERATION
            return Kind::kVector3;
        case 14:  // MAGNETIC_FIELD_UNCALIBRATED
        case 16:  // GYROSCOPE_UNCALIBRATED
        case 35:  // ACCELEROMETER_UNCALIBRATED
            return Kind::kUncalibrated3;
        case 38:  // ACCELEROMETER_LIMITED_AXES
        case 39:  // GYROSCOPE_LIMITED_AXES
            return Kind::kLimitedAxes;
        case 40:  // ACCELEROMETER_LIMITED_AXES_UNCALIBRATED
        case 41:  // GYROSCOPE_LIMITED_AXES_UNCALIBRATED
            return Kind::kLimitedAxesUncal;
        case 11:  // ROTATION_VECTOR
        case 15:  // GAME_ROTATION_VECTOR
        case 20:  // GEOMAGNETIC_ROTATION_VECTOR
            return Kind::kQuaternion;
        case 27:  // DEVICE_ORIENTATION
            return Kind::kDeviceOrientation;
        case 42:  // HEADING
            return Kind::kHeading;
        case 33:  // ADDITIONAL_INFO
            return Kind::kAdditionalInfo;
        case 3:   // ORIENTATION (deprecated azimuth/pitch/roll; sensorservice computes its own
                  // from the transformed rotation vector)
        case 28:  // POSE_6DOF (rotation, translation and their deltas; no reference transform)
            return Kind::kHidden;
        default:
            // META_DATA, LIGHT, PRESSURE, PROXIMITY, RELATIVE_HUMIDITY, AMBIENT_TEMPERATURE,
            // SIGNIFICANT_MOTION, STEP_DETECTOR, STEP_COUNTER, HEART_RATE, TILT_DETECTOR,
            // WAKE_GESTURE, GLANCE_GESTURE, PICK_UP_GESTURE, WRIST_TILT_GESTURE,
            // STATIONARY_DETECT, MOTION_DETECT, HEART_BEAT, DYNAMIC_SENSOR_META,
            // LOW_LATENCY_OFFBODY_DETECT, HINGE_ANGLE, HEAD_TRACKER (head frame),
            // MOISTURE_INTRUSION, and vendor types from DEVICE_PRIVATE_BASE on (their semantics
            // are the vendor's; the tablet's Samsung types are scalar or event values)
            return Kind::kUnchanged;
    }
}

// Sensors the vendor HAL offers whose reading the framework computes better itself, so the HAL
// does not offer them and their events are dropped (HalProxySamsung getSensorsList /
// postEventsToMessageQueue; BENIOKU.md "Oto döndürme").
//
// DEVICE_ORIENTATION (27): Samsung's sensorhub makes the decision on its own and the framework
// takes it as it is. WindowOrientationListener.OrientationSensorJudge has no tilt tolerance and
// no hysteresis, only a 500 ms delay after a touch, so a slight in-plane turn flips the display
// and back (Kadir, 27 Oct 2026). Without the sensor the framework falls back to AccelSensorJudge:
// the accelerometer is low-pass filtered (200 ms), tilt is gated by
// config_autoRotationTiltTolerance and a rotation must pass a 23 degree hysteresis band around
// the quarter turns. The transform above still carries the type (kind_of) because injected
// DEVICE_ORIENTATION data, if any, is re-expressed like every other event.
inline constexpr bool framework_preferred(int32_t sensor_type) {
    return sensor_type == 27;  // DEVICE_ORIENTATION
}

// The AdditionalInfo type whose payload is expressed in the device frame (AdditionalInfo.aidl).
// The others are scalars, timing, per-channel or sensor-local data (AINFO_VEC3_CALIBRATION
// calibrates the raw reading in the sensor's own frame) and stay.
inline constexpr int32_t kAinfoSensorPlacement = 0x10003;

// Transforms an event payload in place: d is the 64-byte EventPayload union as 16 floats
// (V1_0::EventPayload; AdditionalInfo: type and serial as int32 in d[0], d[1], payload d[2..15]).
// Returns false for kHidden (the event must not be delivered).
inline bool rotate_payload(Kind kind, float d[16], int r) {
    switch (kind) {
        case Kind::kUnchanged:
            return true;
        case Kind::kVector3:
            rotate_vector(&d[0], &d[1], r);
            return true;
        case Kind::kUncalibrated3:
            rotate_vector(&d[0], &d[1], r);
            rotate_vector(&d[3], &d[4], r);
            return true;
        case Kind::kLimitedAxes:
            rotate_vector(&d[0], &d[1], r);
            rotate_axis_flags(&d[3], &d[4], r);
            return true;
        case Kind::kLimitedAxesUncal:
            rotate_vector(&d[0], &d[1], r);
            rotate_vector(&d[3], &d[4], r);
            rotate_axis_flags(&d[6], &d[7], r);
            return true;
        case Kind::kQuaternion:
            rotate_quaternion(d, r);
            return true;
        case Kind::kDeviceOrientation:
            d[0] = rotate_device_orientation(d[0], r);
            return true;
        case Kind::kHeading:
            d[0] = rotate_heading(d[0], r);
            return true;
        case Kind::kAdditionalInfo: {
            int32_t type;
            std::memcpy(&type, &d[0], sizeof(type));
            if (type == kAinfoSensorPlacement) rotate_placement(&d[2], r);
            return true;
        }
        case Kind::kHidden:
            return false;
    }
    return true;
}

}  // namespace natural_orientation
}  // namespace gbos
