/*
 * Reads the tablet's device-frame sensors through the platform sensor service, the way an app
 * does (NDK ASensorManager), and prints one averaged line per sensor:
 *
 *   <type> <name>|<vendor> n=<events> v=<mean value 0> <mean value 1> ...
 *
 * Used for the live measurement of the natural-orientation sensor layer (BENIOKU.md "Ölçüm"):
 * the same physical pose read through the vendor HAL and through the rebuilt one.
 *
 *   sensor-olcum [seconds]      (default 2; run from adb shell on the tablet)
 */
#include <android/looper.h>
#include <android/sensor.h>
#include <stdio.h>
#include <stdlib.h>
#include <time.h>

#define MAX_SENSORS 64
#define VALUES 6

static const int kTypes[] = {
        ASENSOR_TYPE_ACCELEROMETER, ASENSOR_TYPE_MAGNETIC_FIELD, ASENSOR_TYPE_GYROSCOPE,
        ASENSOR_TYPE_GRAVITY, ASENSOR_TYPE_LINEAR_ACCELERATION, ASENSOR_TYPE_ROTATION_VECTOR,
        ASENSOR_TYPE_MAGNETIC_FIELD_UNCALIBRATED, ASENSOR_TYPE_GAME_ROTATION_VECTOR,
        ASENSOR_TYPE_GYROSCOPE_UNCALIBRATED, ASENSOR_TYPE_GEOMAGNETIC_ROTATION_VECTOR,
        27 /* DEVICE_ORIENTATION */, ASENSOR_TYPE_ACCELEROMETER_UNCALIBRATED,
};

struct acc {
    const ASensor* sensor;
    int n;
    double sum[VALUES];
};

static double now(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return ts.tv_sec + ts.tv_nsec / 1e9;
}

int main(int argc, char** argv) {
    double seconds = argc > 1 ? atof(argv[1]) : 2.0;
    ASensorManager* sm = ASensorManager_getInstanceForPackage("gbos.dogal_yon.olcum");
    ALooper* looper = ALooper_prepare(ALOOPER_PREPARE_ALLOW_NON_CALLBACKS);
    ASensorEventQueue* q = ASensorManager_createEventQueue(sm, looper, 1, NULL, NULL);
    if (sm == NULL || q == NULL) {
        fprintf(stderr, "no sensor manager\n");
        return 1;
    }
    ASensorList list;
    int count = ASensorManager_getSensorList(sm, &list);
    struct acc accs[MAX_SENSORS] = {0};
    int used = 0;
    for (int i = 0; i < count && used < MAX_SENSORS; i++) {
        int type = ASensor_getType(list[i]);
        for (size_t t = 0; t < sizeof(kTypes) / sizeof(kTypes[0]); t++) {
            if (type != kTypes[t]) continue;
            /* Hardware sensors only: the platform's virtual sensors derive from these. */
            if (ASensor_getHandle(list[i]) >= 0x5f000000) continue;
            if (ASensorEventQueue_registerSensor(q, list[i], 20000, 0) == 0) {
                accs[used++].sensor = list[i];
            }
        }
    }
    double end = now() + seconds;
    while (now() < end) {
        ALooper_pollOnce(100, NULL, NULL, NULL);
        ASensorEvent ev[16];
        ssize_t n;
        while ((n = ASensorEventQueue_getEvents(q, ev, 16)) > 0) {
            for (ssize_t k = 0; k < n; k++) {
                for (int a = 0; a < used; a++) {
                    if (ASensor_getHandle(accs[a].sensor) != ev[k].sensor) continue;
                    accs[a].n++;
                    for (int v = 0; v < VALUES; v++) accs[a].sum[v] += ev[k].data[v];
                }
            }
        }
    }
    for (int a = 0; a < used; a++) {
        printf("%d %s|%s n=%d v=", ASensor_getType(accs[a].sensor), ASensor_getName(accs[a].sensor),
               ASensor_getVendor(accs[a].sensor), accs[a].n);
        for (int v = 0; v < VALUES; v++) printf(" %.4f", accs[a].n ? accs[a].sum[v] / accs[a].n : 0.0);
        printf("\n");
        ASensorEventQueue_disableSensor(q, accs[a].sensor);
    }
    ASensorManager_destroyEventQueue(sm, q);
    return 0;
}
