/*
 * Opens each camera through the platform camera service (NDK Camera2, as an app does), captures
 * frames into a YUV image reader and prints per camera:
 *
 *   <id> facing=<0 front|1 back|2 external> orientation=<SENSOR_ORIENTATION>
 *        frames=<YUV images received> pose=<LENS_POSE_ROTATION of the last capture result>
 *
 * Used for the live measurement of the natural-orientation camera layer (BENIOKU.md "Ölçüm"):
 * the rebuilt provider must stream, and the dynamic pose in capture results must agree with the
 * static characteristics.
 *
 *   kamera-olcum [seconds per camera]      (default 2; run as root from adb shell on the tablet)
 */
#include <android/binder_process.h>
#include <camera/NdkCameraDevice.h>
#include <camera/NdkCameraManager.h>
#include <camera/NdkCameraMetadataTags.h>
#include <media/NdkImageReader.h>
#include <signal.h>
#include <stdatomic.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

static atomic_int g_frames;
static float g_pose[4];
static atomic_int g_has_pose;

static void on_image(void* ctx, AImageReader* reader) {
    (void)ctx;
    AImage* image = NULL;
    if (AImageReader_acquireLatestImage(reader, &image) == AMEDIA_OK && image != NULL) {
        atomic_fetch_add(&g_frames, 1);
        AImage_delete(image);
    }
}

static void on_completed(void* ctx, ACameraCaptureSession* s, ACaptureRequest* r,
                         const ACameraMetadata* result) {
    (void)ctx, (void)s, (void)r;
    ACameraMetadata_const_entry e;
    if (ACameraMetadata_getConstEntry(result, ACAMERA_LENS_POSE_ROTATION, &e) == ACAMERA_OK && e.count == 4) {
        memcpy(g_pose, e.data.f, sizeof(g_pose));
        atomic_store(&g_has_pose, 1);
    }
}

static void on_disconnected(void* ctx, ACameraDevice* d) { (void)ctx, (void)d; }
static void on_error(void* ctx, ACameraDevice* d, int err) {
    (void)ctx, (void)d;
    fprintf(stderr, "device error %d\n", err);
}
static void on_session(void* ctx, ACameraCaptureSession* s) { (void)ctx, (void)s; }

static int i32(const ACameraMetadata* m, uint32_t tag) {
    ACameraMetadata_const_entry e;
    if (ACameraMetadata_getConstEntry(m, tag, &e) != ACAMERA_OK || e.count < 1) return -1;
    return e.type == ACAMERA_TYPE_BYTE ? e.data.u8[0] : e.data.i32[0];
}

int main(int argc, char** argv) {
    int seconds = argc > 1 ? atoi(argv[1]) : 2;
    /* Opening a camera makes system_server collect stack traces of the camera processes, which
     * sends SIGQUIT (an ART trace request) to this client too; it must not end the measurement. */
    signal(SIGQUIT, SIG_IGN);
    setvbuf(stdout, NULL, _IOLBF, 0);
    /* Device and capture callbacks and image buffers arrive over binder. */
    ABinderProcess_setThreadPoolMaxThreadCount(4);
    ABinderProcess_startThreadPool();
    ACameraManager* mgr = ACameraManager_create();
    ACameraIdList* ids = NULL;
    if (ACameraManager_getCameraIdList(mgr, &ids) != ACAMERA_OK) {
        fprintf(stderr, "no camera id list\n");
        return 1;
    }
    for (int i = 0; i < ids->numCameras; i++) {
        const char* id = ids->cameraIds[i];
        ACameraMetadata* chars = NULL;
        ACameraManager_getCameraCharacteristics(mgr, id, &chars);
        int facing = i32(chars, ACAMERA_LENS_FACING), orientation = i32(chars, ACAMERA_SENSOR_ORIENTATION);
        ACameraMetadata_free(chars);

        atomic_store(&g_frames, 0);
        atomic_store(&g_has_pose, 0);
        AImageReader* reader = NULL;
        AImageReader_new(640, 480, AIMAGE_FORMAT_YUV_420_888, 4, &reader);
        AImageReader_ImageListener il = {.context = NULL, .onImageAvailable = on_image};
        AImageReader_setImageListener(reader, &il);
        ANativeWindow* window = NULL;
        AImageReader_getWindow(reader, &window);

        ACameraDevice_StateCallbacks dcb = {.onDisconnected = on_disconnected, .onError = on_error};
        ACameraDevice* dev = NULL;
        camera_status_t st = ACameraManager_openCamera(mgr, id, &dcb, &dev);
        if (st != ACAMERA_OK) {
            printf("%s facing=%d orientation=%d open-failed=%d\n", id, facing, orientation, st);
            AImageReader_delete(reader);
            continue;
        }
        ACaptureSessionOutputContainer* outs = NULL;
        ACaptureSessionOutput* out = NULL;
        ACameraOutputTarget* target = NULL;
        ACaptureRequest* req = NULL;
        ACameraCaptureSession* session = NULL;
        ACaptureSessionOutputContainer_create(&outs);
        ACaptureSessionOutput_create(window, &out);
        ACaptureSessionOutputContainer_add(outs, out);
        ACameraOutputTarget_create(window, &target);
        ACameraDevice_createCaptureRequest(dev, TEMPLATE_PREVIEW, &req);
        ACaptureRequest_addTarget(req, target);
        ACameraCaptureSession_stateCallbacks scb = {.onClosed = on_session, .onReady = on_session,
                                                    .onActive = on_session};
        st = ACameraDevice_createCaptureSession(dev, outs, &scb, &session);
        if (st == ACAMERA_OK) {
            ACameraCaptureSession_captureCallbacks ccb = {.onCaptureCompleted = on_completed};
            ACameraCaptureSession_setRepeatingRequest(session, &ccb, 1, &req, NULL);
            sleep(seconds);
            ACameraCaptureSession_stopRepeating(session);
            ACameraCaptureSession_close(session);
        }
        printf("%s facing=%d orientation=%d frames=%d pose=", id, facing, orientation,
               atomic_load(&g_frames));
        if (atomic_load(&g_has_pose))
            printf("%.4f %.4f %.4f %.4f\n", g_pose[0], g_pose[1], g_pose[2], g_pose[3]);
        else
            printf("none\n");
        ACaptureRequest_free(req);
        ACameraOutputTarget_free(target);
        ACaptureSessionOutput_free(out);
        ACaptureSessionOutputContainer_free(outs);
        ACameraDevice_close(dev);
        AImageReader_delete(reader);
        usleep(500000);
    }
    ACameraManager_deleteCameraIdList(ids);
    ACameraManager_delete(mgr);
    return 0;
}
