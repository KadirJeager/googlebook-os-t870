package android.hardware.camera2;
/** Compile-time stub of the hidden AIDL interface (the real one lives in framework.jar). */
public interface ICameraDeviceUser {
    int createStream(android.hardware.camera2.params.OutputConfiguration c) throws android.os.RemoteException;
    void deleteStream(int id) throws android.os.RemoteException;
    int[] endConfigure(int operatingMode, android.hardware.camera2.impl.CameraMetadataNative sessionParams, long startTimeMs) throws android.os.RemoteException;
    android.hardware.camera2.utils.SubmitInfo submitRequestList(CaptureRequest[] requests, boolean repeating) throws android.os.RemoteException;
    void disconnect() throws android.os.RemoteException;
    android.hardware.camera2.utils.OutputAndInputStreamIds configureStreams(android.hardware.camera2.utils.SessionConfigurationAndStreamIds s) throws android.os.RemoteException;
}
