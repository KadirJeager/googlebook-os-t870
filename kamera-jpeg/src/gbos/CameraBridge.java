package gbos;

import android.hardware.camera2.CaptureRequest;
import android.hardware.camera2.impl.CameraMetadataNative;
import android.hardware.camera2.impl.ICameraDeviceUserWrapper;
import android.hardware.camera2.params.OutputConfiguration;
import android.hardware.camera2.utils.OutputAndInputStreamIds;
import android.hardware.camera2.utils.SessionConfigurationAndStreamIds;
import android.hardware.camera2.utils.SubmitInfo;
import android.os.RemoteException;

/**
 * The few entry points merged into framework.jar's classes2.dex (yamalar/kamera_jpeg.py). The
 * patched ICameraDeviceUserWrapper calls these instead of its binder; everything else lives in
 * CameraJpegShim in its own dex. Keeping this class tiny keeps the method ids added to the
 * nearly full classes2.dex to a dozen.
 */
public final class CameraBridge {
    private CameraBridge() { }

    public static int createStream(ICameraDeviceUserWrapper w, OutputConfiguration c) throws RemoteException {
        return CameraJpegShim.createStream(w, c);
    }

    public static void deleteStream(ICameraDeviceUserWrapper w, int streamId) throws RemoteException {
        CameraJpegShim.deleteStream(w, streamId);
    }

    public static int[] endConfigure(ICameraDeviceUserWrapper w, int operatingMode, CameraMetadataNative sessionParams,
                                     long startTimeMs) throws RemoteException {
        return CameraJpegShim.endConfigure(w, operatingMode, sessionParams, startTimeMs);
    }

    public static SubmitInfo submitRequestList(ICameraDeviceUserWrapper w, CaptureRequest[] requests, boolean repeating)
            throws RemoteException {
        return CameraJpegShim.submitRequestList(w, requests, repeating);
    }

    public static OutputAndInputStreamIds configureStreams(ICameraDeviceUserWrapper w, SessionConfigurationAndStreamIds s)
            throws RemoteException {
        return CameraJpegShim.configureStreams(w, s);
    }

    public static void disconnect(ICameraDeviceUserWrapper w) throws RemoteException {
        CameraJpegShim.disconnect(w);
    }
}
