package co.aospa.sense.hal;

import android.app.ActivityThread;
import android.app.IActivityManager;
import android.content.ComponentName;
import android.content.Context;
import android.content.Intent;
import android.hardware.biometrics.common.CommonProps;
import android.hardware.biometrics.common.ComponentInfo;
import android.hardware.biometrics.common.ICancellationSignal;
import android.hardware.biometrics.common.OperationContext;
import android.hardware.biometrics.common.SensorStrength;
import android.hardware.biometrics.face.AcquiredInfo;
import android.hardware.biometrics.face.AuthenticationFrame;
import android.hardware.biometrics.face.BaseFrame;
import android.hardware.biometrics.face.EnrollmentFrame;
import android.hardware.biometrics.face.EnrollmentStageConfig;
import android.hardware.biometrics.face.Error;
import android.hardware.biometrics.face.FaceEnrollOptions;
import android.hardware.biometrics.face.FaceSensorType;
import android.hardware.biometrics.face.IFace;
import android.hardware.biometrics.face.ISession;
import android.hardware.biometrics.face.ISessionCallback;
import android.hardware.biometrics.face.SensorProps;
import android.hardware.common.NativeHandle;
import android.hardware.face.FaceManager;
import android.hardware.keymaster.HardwareAuthToken;
import android.hardware.keymaster.Timestamp;
import android.os.CancellationSignal;
import android.os.Handler;
import android.os.HandlerThread;
import android.os.IBinder;
import android.os.Looper;
import android.os.Parcel;
import android.os.RemoteException;
import android.os.ServiceManager;
import android.os.UserHandle;
import android.os.UserManager;
import android.util.Log;
import android.util.SparseArray;

import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.security.SecureRandom;
import java.util.ArrayList;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;

import vendor.aospa.biometrics.face.ISenseService;
import vendor.aospa.biometrics.face.ISenseServiceReceiver;

/**
 * IFace AIDL HAL backed by co.aospa.sense.
 *
 * Every user has its own SenseService instance (one app process per user) and its own face
 * template under that user's data directory. A session for user N only ever talks to the
 * SenseService running as user N. In headless system user mode user 0 never has a face
 * enrolled, so its sessions are answered locally and can never touch another user's data.
 *
 * The HAL is started by init and is unknown to ActivityManager, so it cannot bind to the app
 * with any process importance. Instead it keeps the user's SenseService started, which keeps
 * the app process out of the cached state (neither frozen nor killed), and the service registers
 * its binder with the HAL itself. Before an operation that uses the camera (authenticate,
 * enroll) the HAL starts the service with startForegroundService(): the caller is the system
 * uid, which ActivityManager allows to grant the service while-in-use camera access, and the
 * service then runs as a camera foreground service while the camera is open. Keyguard, not the
 * app, is in the foreground during authentication, so without this the camera is blocked.
 *
 * The sensor is declared WEAK (Class 2): the HardwareAuthToken MAC cannot be produced without
 * the TEE HMAC key, so the sensor must not claim STRONG. Class 2 is sufficient for keyguard.
 */
public class SenseFaceHalMain {
    private static final String TAG = "SenseFaceHal";
    private static final String SERVICE_NAME = "android.hardware.biometrics.face.IFace/default";
    private static final int SENSOR_ID = 1;
    private static final int TRANSACTION_REGISTER_SENSE_SERVICE = IBinder.FIRST_CALL_TRANSACTION + 100;
    private static final long BIND_TIMEOUT_MS = 3000L;
    /** Must match SenseService. */
    private static final String ACTION_KEEP_ALIVE = "co.aospa.sense.action.KEEP_ALIVE";
    private static final String ACTION_FOREGROUND = "co.aospa.sense.action.FOREGROUND";
    private static final ComponentName SENSE_SERVICE =
            new ComponentName("co.aospa.sense", "co.aospa.sense.SenseService");

    private static Context sSystemContext;
    private static Handler sConnHandler;
    private static Handler sWorkHandler;
    private static final SecureRandom sRandom = new SecureRandom();

    private static final Object sLock = new Object();
    /** Live SenseService binders keyed by the user the service runs as. Guarded by sLock. */
    private static final SparseArray<IBinder> sServiceBinders = new SparseArray<>();
    /** Latest open session per user. Guarded by sLock. */
    private static final SparseArray<SenseSession> sSessions = new SparseArray<>();

    static boolean isHeadlessSystemUser(int userId) {
        return userId == UserHandle.USER_SYSTEM && UserManager.isHeadlessSystemUserMode();
    }

    private static IBinder liveServiceBinder(int userId) {
        synchronized (sLock) {
            IBinder binder = sServiceBinders.get(userId);
            if (binder != null && !binder.isBinderAlive()) {
                sServiceBinders.remove(userId);
                binder = null;
            }
            return binder;
        }
    }

    /** Records the SenseService binder of {@code userId} and hands it to that user's session. */
    private static void onServiceBinderAvailable(int userId, IBinder binder) {
        if (binder == null) return;
        if (isHeadlessSystemUser(userId)) {
            Log.i(TAG, "Ignoring SenseService of headless system user");
            return;
        }
        SenseSession session;
        synchronized (sLock) {
            if (sServiceBinders.get(userId) == binder) return;
            sServiceBinders.put(userId, binder);
            session = sSessions.get(userId);
        }
        try {
            binder.linkToDeath(() -> {
                Log.w(TAG, "SenseService binder died for user " + userId);
                synchronized (sLock) {
                    if (sServiceBinders.get(userId) == binder) sServiceBinders.remove(userId);
                }
            }, 0);
        } catch (RemoteException e) {
            synchronized (sLock) {
                if (sServiceBinders.get(userId) == binder) sServiceBinders.remove(userId);
            }
            return;
        }
        if (session != null) {
            session.attachService(binder);
        } else {
            try {
                ISenseService.Stub.asInterface(binder).setCallback(new BootstrapReceiver(userId));
                Log.i(TAG, "Attached bootstrap receiver to SenseService for user " + userId);
            } catch (RemoteException e) {
                Log.e(TAG, "Failed to attach bootstrap receiver for user " + userId, e);
            }
        }
    }

    /**
     * Starts the SenseService of {@code userId}. With {@code foreground} it is started as a
     * foreground service so that it may use the camera while keyguard is in the foreground.
     * Returns false if the service could not be started (typically: the user is still locked).
     */
    private static boolean startSenseService(int userId, boolean foreground) {
        if (isHeadlessSystemUser(userId)) return false;
        try {
            IActivityManager am = IActivityManager.Stub.asInterface(
                    ServiceManager.getService(Context.ACTIVITY_SERVICE));
            Intent intent = new Intent(foreground ? ACTION_FOREGROUND : ACTION_KEEP_ALIVE);
            intent.setComponent(SENSE_SERVICE);
            ComponentName cn = am.startService(null, intent, null, foreground, "android", null,
                    userId);
            if (cn == null) {
                Log.w(TAG, "SenseService for user " + userId + " could not be started");
                return false;
            }
            return true;
        } catch (Throwable t) {
            Log.w(TAG, "startService for user " + userId + " failed", t);
            return false;
        }
    }

    private static void triggerPlatformEnroll(int userId, byte[] token, int[] disabledFeatures) {
        Log.i(TAG, "triggerPlatformEnroll userId=" + userId);
        sWorkHandler.post(() -> {
            try {
                FaceManager fm = sSystemContext.getSystemService(FaceManager.class);
                if (fm != null) {
                    fm.enroll(userId, token, new CancellationSignal(),
                            new FaceManager.EnrollmentCallback() {
                                @Override
                                public void onEnrollmentProgress(int remaining) {
                                    Log.i(TAG, "Platform FaceManager.enroll progress=" + remaining);
                                }
                                @Override
                                public void onEnrollmentHelp(int helpMsgId, CharSequence helpString) {
                                }
                                @Override
                                public void onEnrollmentError(int errMsgId, CharSequence errString) {
                                    Log.w(TAG, "Platform FaceManager.enroll err=" + errMsgId + " " + errString);
                                }
                            }, disabledFeatures);
                }
            } catch (Throwable t) {
                Log.e(TAG, "Platform FaceManager.enroll failed in triggerPlatformEnroll", t);
            }
        });
    }

    /**
     * Receiver installed on a user's SenseService before the framework opened a session for
     * that user. Events are forwarded to the user's session once one exists.
     */
    private static final class BootstrapReceiver extends ISenseServiceReceiver.Stub {
        private final int mUserId;

        BootstrapReceiver(int userId) {
            mUserId = userId;
        }

        private ISenseServiceReceiver target() {
            synchronized (sLock) {
                SenseSession session = sSessions.get(mUserId);
                return session != null ? session.mReceiver : null;
            }
        }

        @Override
        public void onEnrollResult(int faceId, int userId, int remaining) throws RemoteException {
            ISenseServiceReceiver t = target();
            if (t != null) t.onEnrollResult(faceId, userId, remaining);
        }
        @Override
        public void onAuthenticated(int faceId, int userId, byte[] token) throws RemoteException {
            ISenseServiceReceiver t = target();
            if (t != null) t.onAuthenticated(faceId, userId, token);
        }
        @Override
        public void onAcquired(int userId, int acquiredInfo, int vendorCode) throws RemoteException {
            ISenseServiceReceiver t = target();
            if (t != null) t.onAcquired(userId, acquiredInfo, vendorCode);
        }
        @Override
        public void onError(int error, int vendorCode) throws RemoteException {
            ISenseServiceReceiver t = target();
            if (t != null) t.onError(error, vendorCode);
        }
        @Override
        public void onRemoved(int[] faceIds, int userId) throws RemoteException {
            ISenseServiceReceiver t = target();
            if (t != null) t.onRemoved(faceIds, userId);
        }
        @Override
        public void onEnumerate(int[] faceIds, int userId) throws RemoteException {
            ISenseServiceReceiver t = target();
            if (t != null) t.onEnumerate(faceIds, userId);
        }
        @Override
        public void onLockoutChanged(long duration) throws RemoteException {
            ISenseServiceReceiver t = target();
            if (t != null) t.onLockoutChanged(duration);
        }
        @Override
        public void onStartPlatformEnroll(int userId, byte[] token, int[] disabledFeatures) {
            ISenseServiceReceiver t = target();
            if (t != null) {
                try {
                    t.onStartPlatformEnroll(mUserId, token, disabledFeatures);
                    return;
                } catch (RemoteException ignored) {
                }
            }
            triggerPlatformEnroll(mUserId, token, disabledFeatures);
        }
        @Override
        public void onCancelPlatformEnroll() {
            ISenseServiceReceiver t = target();
            if (t != null) {
                try {
                    t.onCancelPlatformEnroll();
                } catch (RemoteException ignored) {
                }
            }
        }
    }

    public static void main(String[] args) {
        Log.i(TAG, "Starting SenseFaceHalMain AIDL V4 service...");
        Looper.prepareMainLooper();

        HandlerThread connThread = new HandlerThread("SenseHalConn");
        connThread.start();
        sConnHandler = new Handler(connThread.getLooper());

        HandlerThread workThread = new HandlerThread("SenseHalWork");
        workThread.start();
        sWorkHandler = new Handler(workThread.getLooper());

        try {
            ActivityThread at = ActivityThread.systemMain();
            sSystemContext = at.getSystemContext();
            Log.i(TAG, "Obtained systemContext from ActivityThread.systemMain()");
            IBinder amBinder = ServiceManager.getService(Context.ACTIVITY_SERVICE);
            if (amBinder != null) {
                amBinder.linkToDeath(() -> {
                    Log.w(TAG, "system_server died, exiting sense_face_hal for clean restart");
                    System.exit(0);
                }, 0);
            }
        } catch (Throwable t) {
            Log.e(TAG, "Failed to obtain systemContext", t);
        }

        FaceHalService service = new FaceHalService();
        service.markVintfStability();
        try {
            ServiceManager.addService(SERVICE_NAME, service.asBinder());
            Log.i(TAG, "Registered " + SERVICE_NAME + " with ServiceManager");
        } catch (Throwable t) {
            Log.e(TAG, "Failed to register " + SERVICE_NAME, t);
            System.exit(1);
        }

        Looper.loop();
    }

    private static class FaceHalService extends IFace.Stub {
        @Override
        public int getInterfaceVersion() {
            return IFace.VERSION;
        }

        @Override
        public String getInterfaceHash() {
            return IFace.HASH;
        }

        @Override
        public SensorProps[] getSensorProps() {
            SensorProps props = new SensorProps();
            props.commonProps = new CommonProps();
            props.commonProps.sensorId = SENSOR_ID;
            props.commonProps.sensorStrength = SensorStrength.WEAK;
            props.commonProps.maxEnrollmentsPerUser = 1;
            props.commonProps.componentInfo = new ComponentInfo[0];
            props.sensorType = FaceSensorType.RGB;
            props.halControlsPreview = false;
            props.supportsDetectInteraction = false;
            return new SensorProps[]{props};
        }

        @Override
        public boolean onTransact(int code, Parcel data, Parcel reply, int flags)
                throws RemoteException {
            if (code == TRANSACTION_REGISTER_SENSE_SERVICE) {
                IBinder binder = data.readStrongBinder();
                int userId = data.readInt();
                Log.i(TAG, "SenseService registered itself for user " + userId);
                onServiceBinderAvailable(userId, binder);
                return true;
            }
            return super.onTransact(code, data, reply, flags);
        }

        @Override
        public ISession createSession(int sensorId, int userId, ISessionCallback cb) {
            boolean servesUser = !isHeadlessSystemUser(userId);
            Log.i(TAG, "createSession sensorId=" + sensorId + " userId=" + userId
                    + " servesUser=" + servesUser);
            SenseSession session = new SenseSession(sensorId, userId, servesUser, cb);
            session.markVintfStability();
            synchronized (sLock) {
                sSessions.put(userId, session);
            }
            if (servesUser) {
                IBinder binder = liveServiceBinder(userId);
                if (binder != null) {
                    session.attachService(binder);
                } else {
                    startSenseService(userId, false);
                }
            }
            return session;
        }
    }

    private static class SenseSession extends ISession.Stub {
        private final int mSensorId;
        private final int mUserId;
        /** False for the headless system user: no face data, no SenseService. */
        private final boolean mServesUser;
        private final ISessionCallback mCallback;
        private final Object mServiceLock = new Object();
        private ISenseService mSenseService; // guarded by mServiceLock
        private CountDownLatch mBindLatch = new CountDownLatch(1); // guarded by mServiceLock
        private volatile boolean mIsEnrolling = false;
        private volatile boolean mAuthOrEnrollActive = false;
        private volatile long mCurrentOperationId = 0L;
        private volatile CancellationSignal mPlatformEnrollCancel;

        SenseSession(int sensorId, int userId, boolean servesUser, ISessionCallback cb) {
            mSensorId = sensorId;
            mUserId = userId;
            mServesUser = servesUser;
            mCallback = cb;
        }

        private final ISenseServiceReceiver mReceiver = new ISenseServiceReceiver.Stub() {
            @Override
            public void onEnrollResult(int faceId, int userId, int remaining) {
                Log.i(TAG, "onEnrollResult faceId=" + faceId + " remaining=" + remaining);
                if (remaining == 0) {
                    mIsEnrolling = false;
                    mAuthOrEnrollActive = false;
                }
                try {
                    mCallback.onEnrollmentProgress(faceId, remaining);
                } catch (RemoteException e) {
                    Log.e(TAG, "onEnrollmentProgress failed", e);
                }
            }

            @Override
            public void onAuthenticated(int faceId, int userId, byte[] token) {
                Log.i(TAG, "onAuthenticated faceId=" + faceId + " userId=" + userId);
                mAuthOrEnrollActive = false;
                try {
                    if (faceId > 0) {
                        HardwareAuthToken hat = parseAuthToken(token, mCurrentOperationId, userId);
                        mCallback.onAuthenticationSucceeded(faceId, hat);
                    } else {
                        mCallback.onAuthenticationFailed();
                    }
                } catch (RemoteException e) {
                    Log.e(TAG, "onAuthenticated callback failed", e);
                }
            }

            @Override
            public void onAcquired(int userId, int acquiredInfo, int vendorCode) {
                try {
                    BaseFrame base = new BaseFrame();
                    if (acquiredInfo == 22) {
                        base.acquiredInfo = AcquiredInfo.VENDOR;
                        base.vendorCode = vendorCode;
                    } else {
                        base.acquiredInfo = (byte) Math.min(acquiredInfo + 1, AcquiredInfo.VENDOR);
                        base.vendorCode = vendorCode;
                    }
                    if (mIsEnrolling) {
                        EnrollmentFrame frame = new EnrollmentFrame();
                        frame.data = base;
                        mCallback.onEnrollmentFrame(frame);
                    } else {
                        AuthenticationFrame frame = new AuthenticationFrame();
                        frame.data = base;
                        mCallback.onAuthenticationFrame(frame);
                    }
                } catch (RemoteException e) {
                    Log.e(TAG, "onAcquired callback failed", e);
                }
            }

            @Override
            public void onError(int error, int vendorCode) {
                Log.i(TAG, "onError error=" + error + " vendorCode=" + vendorCode);
                mIsEnrolling = false;
                mAuthOrEnrollActive = false;
                try {
                    if (error == 7) {
                        mCallback.onLockoutTimed(30000L);
                    } else if (error == 9) {
                        mCallback.onLockoutPermanent();
                    } else {
                        byte aidlErr;
                        switch (error) {
                            case 1: aidlErr = Error.HW_UNAVAILABLE; break;
                            case 2: aidlErr = Error.UNABLE_TO_PROCESS; break;
                            case 3: aidlErr = Error.TIMEOUT; break;
                            case 4: aidlErr = Error.NO_SPACE; break;
                            case 5: aidlErr = Error.CANCELED; break;
                            case 6: aidlErr = Error.UNABLE_TO_REMOVE; break;
                            case 8: aidlErr = Error.VENDOR; break;
                            default: aidlErr = Error.UNABLE_TO_PROCESS; break;
                        }
                        mCallback.onError(aidlErr, vendorCode);
                    }
                } catch (RemoteException e) {
                    Log.e(TAG, "onError callback failed", e);
                }
            }

            @Override
            public void onRemoved(int[] faceIds, int userId) {
                Log.i(TAG, "onRemoved count=" + (faceIds != null ? faceIds.length : 0));
                try {
                    mCallback.onEnrollmentsRemoved(faceIds != null ? faceIds : new int[0]);
                } catch (RemoteException e) {
                    Log.e(TAG, "onEnrollmentsRemoved failed", e);
                }
            }

            @Override
            public void onEnumerate(int[] faceIds, int userId) {
                ArrayList<Integer> valid = new ArrayList<>();
                if (faceIds != null) {
                    for (int id : faceIds) {
                        if (id > 0) valid.add(id);
                    }
                }
                int[] out = new int[valid.size()];
                for (int i = 0; i < valid.size(); i++) out[i] = valid.get(i);
                Log.i(TAG, "onEnumerate valid count=" + out.length);
                try {
                    mCallback.onEnrollmentsEnumerated(out);
                } catch (RemoteException e) {
                    Log.e(TAG, "onEnrollmentsEnumerated failed", e);
                }
            }

            @Override
            public void onLockoutChanged(long duration) {
                try {
                    if (duration == 0) {
                        mCallback.onLockoutCleared();
                    } else if (duration == Long.MAX_VALUE) {
                        mCallback.onLockoutPermanent();
                    } else {
                        mCallback.onLockoutTimed(duration);
                    }
                } catch (RemoteException e) {
                    Log.e(TAG, "onLockoutChanged failed", e);
                }
            }

            @Override
            public void onStartPlatformEnroll(int userId, byte[] token, int[] disabledFeatures) {
                Log.i(TAG, "onStartPlatformEnroll userId=" + userId);
                sWorkHandler.post(() -> {
                    try {
                        FaceManager fm = sSystemContext.getSystemService(FaceManager.class);
                        if (fm != null) {
                            mPlatformEnrollCancel = new CancellationSignal();
                            fm.enroll(userId, token, mPlatformEnrollCancel,
                                    new FaceManager.EnrollmentCallback() {
                                        @Override
                                        public void onEnrollmentProgress(int remaining) {
                                            Log.i(TAG, "Platform FaceManager.enroll progress=" + remaining);
                                        }
                                        @Override
                                        public void onEnrollmentHelp(int helpMsgId, CharSequence helpString) {
                                        }
                                        @Override
                                        public void onEnrollmentError(int errMsgId, CharSequence errString) {
                                            Log.w(TAG, "Platform FaceManager.enroll err=" + errMsgId + " " + errString);
                                        }
                                    }, disabledFeatures);
                        }
                    } catch (Throwable t) {
                        Log.e(TAG, "Platform FaceManager.enroll failed, invoking service.enroll directly", t);
                        ISenseService svc = getServiceSync(true);
                        if (svc != null) {
                            try {
                                svc.generateChallenge(600);
                                svc.enroll(token, 60, disabledFeatures != null ? disabledFeatures : new int[0]);
                            } catch (RemoteException re) {
                                Log.e(TAG, "Direct svc.enroll failed", re);
                            }
                        }
                    }
                });
            }

            @Override
            public void onCancelPlatformEnroll() {
                Log.i(TAG, "onCancelPlatformEnroll");
                CancellationSignal cs = mPlatformEnrollCancel;
                if (cs != null) {
                    mPlatformEnrollCancel = null;
                    cs.cancel();
                }
            }
        };

        /** Connects this session to its user's SenseService and installs the session receiver. */
        void attachService(IBinder binder) {
            ISenseService svc = ISenseService.Stub.asInterface(binder);
            try {
                svc.setCallback(mReceiver);
            } catch (RemoteException e) {
                Log.e(TAG, "setCallback failed for user " + mUserId, e);
                return;
            }
            synchronized (mServiceLock) {
                mSenseService = svc;
                mBindLatch.countDown();
            }
            Log.i(TAG, "Session for user " + mUserId + " connected to SenseService");
        }

        private ISenseService getServiceSync() {
            return getServiceSync(false);
        }

        /**
         * Returns this user's SenseService, starting it and waiting briefly if needed. With
         * {@code forCamera} the service is (re)started as a foreground service first.
         */
        private ISenseService getServiceSync(boolean forCamera) {
            if (!mServesUser) return null;
            startSenseService(mUserId, forCamera);
            CountDownLatch latch;
            synchronized (mServiceLock) {
                if (mSenseService != null && mSenseService.asBinder().isBinderAlive()) {
                    return mSenseService;
                }
                mSenseService = null;
                if (mBindLatch.getCount() == 0) mBindLatch = new CountDownLatch(1);
                latch = mBindLatch;
            }
            IBinder binder = liveServiceBinder(mUserId);
            if (binder != null) {
                attachService(binder);
            } else {
                // The service registers its binder with the HAL once it is running.
                try {
                    latch.await(BIND_TIMEOUT_MS, TimeUnit.MILLISECONDS);
                } catch (InterruptedException ignored) {
                }
            }
            synchronized (mServiceLock) {
                if (mSenseService == null) {
                    Log.w(TAG, "SenseService for user " + mUserId + " is not available");
                }
                return mSenseService;
            }
        }

        private static byte[] toByteArray(HardwareAuthToken hat) {
            if (hat == null) return new byte[69];
            ByteBuffer buf = ByteBuffer.allocate(69).order(ByteOrder.LITTLE_ENDIAN);
            buf.put((byte) 0);
            buf.putLong(hat.challenge);
            buf.putLong(hat.userId);
            buf.putLong(hat.authenticatorId);
            buf.putInt(Integer.reverseBytes(hat.authenticatorType));
            long ts = hat.timestamp != null ? hat.timestamp.milliSeconds : 0L;
            buf.putLong(Long.reverseBytes(ts));
            if (hat.mac != null && hat.mac.length == 32) {
                buf.put(hat.mac);
            } else {
                buf.put(new byte[32]);
            }
            return buf.array();
        }

        private static HardwareAuthToken parseAuthToken(byte[] token, long operationId, int fallbackUserId) {
            HardwareAuthToken hat = new HardwareAuthToken();
            hat.timestamp = new Timestamp();
            hat.mac = new byte[32];
            if (token != null && token.length >= 69) {
                try {
                    ByteBuffer buf = ByteBuffer.wrap(token).order(ByteOrder.LITTLE_ENDIAN);
                    buf.get(); // version
                    hat.challenge = buf.getLong();
                    hat.userId = buf.getLong();
                    hat.authenticatorId = buf.getLong();
                    hat.authenticatorType = Integer.reverseBytes(buf.getInt());
                    hat.timestamp.milliSeconds = Long.reverseBytes(buf.getLong());
                    buf.get(hat.mac);
                } catch (Throwable ignored) {
                }
            }
            hat.challenge = operationId;
            if (hat.userId == 0L) {
                hat.userId = fallbackUserId;
            }
            if (hat.authenticatorType == 0) {
                hat.authenticatorType = 2; // HW_AUTH_FACE
            }
            hat.timestamp.milliSeconds = android.os.SystemClock.elapsedRealtime();
            return hat;
        }

        private ICancellationSignal createCancelSignal() {
            ICancellationSignal.Stub cancel = new ICancellationSignal.Stub() {
                @Override
                public int getInterfaceVersion() {
                    return ICancellationSignal.VERSION;
                }

                @Override
                public String getInterfaceHash() {
                    return ICancellationSignal.HASH;
                }

                @Override
                public void cancel() {
                    sWorkHandler.post(() -> {
                        mIsEnrolling = false;
                        ISenseService svc = getServiceSync();
                        boolean sentToService = false;
                        if (svc != null) {
                            try {
                                svc.cancel();
                                sentToService = true;
                            } catch (RemoteException e) {
                                Log.e(TAG, "cancel failed on first attempt, waking and retrying", e);
                                try {
                                    svc = getServiceSync();
                                    if (svc != null) {
                                        svc.cancel();
                                        sentToService = true;
                                    }
                                } catch (Throwable ignored) {
                                }
                            }
                        }
                        if (!sentToService) {
                            mAuthOrEnrollActive = false;
                            try {
                                mCallback.onError(Error.CANCELED, 0);
                            } catch (RemoteException ignored) {
                            }
                        } else {
                            sWorkHandler.postDelayed(() -> {
                                if (mAuthOrEnrollActive) {
                                    Log.i(TAG, "cancel fallback delivering Error.CANCELED");
                                    mAuthOrEnrollActive = false;
                                    try {
                                        mCallback.onError(Error.CANCELED, 0);
                                    } catch (RemoteException ignored) {
                                    }
                                }
                            }, 150);
                        }
                    });
                }
            };
            cancel.markVintfStability();
            return cancel;
        }

        @Override
        public int getInterfaceVersion() {
            return ISession.VERSION;
        }

        @Override
        public String getInterfaceHash() {
            return ISession.HASH;
        }

        @Override
        public void generateChallenge() {
            sWorkHandler.post(() -> {
                long challenge = 0L;
                ISenseService svc = getServiceSync();
                if (svc != null) {
                    try {
                        challenge = svc.generateChallenge(600);
                    } catch (RemoteException e) {
                        Log.e(TAG, "generateChallenge failed", e);
                    }
                }
                while (challenge == 0L) {
                    challenge = sRandom.nextLong();
                }
                try {
                    mCallback.onChallengeGenerated(challenge);
                } catch (RemoteException e) {
                    Log.e(TAG, "onChallengeGenerated failed", e);
                }
            });
        }

        @Override
        public void revokeChallenge(long challenge) {
            sWorkHandler.post(() -> {
                ISenseService svc = getServiceSync();
                if (svc != null) {
                    try {
                        svc.revokeChallenge();
                    } catch (RemoteException e) {
                        Log.e(TAG, "revokeChallenge failed", e);
                    }
                }
                try {
                    mCallback.onChallengeRevoked(challenge);
                } catch (RemoteException e) {
                    Log.e(TAG, "onChallengeRevoked failed", e);
                }
            });
        }

        @Override
        public EnrollmentStageConfig[] getEnrollmentConfig(byte enrollmentType) {
            return new EnrollmentStageConfig[0];
        }

        @Override
        public ICancellationSignal enroll(HardwareAuthToken hat, byte type, byte[] features,
                NativeHandle previewSurface) {
            sWorkHandler.post(() -> {
                mIsEnrolling = true;
                mAuthOrEnrollActive = true;
                ISenseService svc = getServiceSync(true);
                if (svc != null) {
                    int[] disabled = new int[features != null ? features.length : 0];
                    if (features != null) {
                        for (int i = 0; i < features.length; i++) disabled[i] = features[i];
                    }
                    byte[] hatBytes = toByteArray(hat);
                    try {
                        svc.generateChallenge(600);
                        svc.enroll(hatBytes, 60, disabled);
                    } catch (RemoteException e) {
                        Log.e(TAG, "enroll failed on first attempt, waking and retrying", e);
                        try {
                            svc = getServiceSync(true);
                            if (svc != null) {
                                svc.generateChallenge(600);
                                svc.enroll(hatBytes, 60, disabled);
                            }
                        } catch (Throwable t) {
                            Log.e(TAG, "enroll retry failed", t);
                        }
                    }
                } else {
                    mIsEnrolling = false;
                    mAuthOrEnrollActive = false;
                    try {
                        mCallback.onError(Error.HW_UNAVAILABLE, 0);
                    } catch (RemoteException ignored) {
                    }
                }
            });
            return createCancelSignal();
        }

        @Override
        public ICancellationSignal authenticate(long operationId) {
            sWorkHandler.post(() -> {
                mIsEnrolling = false;
                mAuthOrEnrollActive = true;
                mCurrentOperationId = operationId;
                ISenseService svc = getServiceSync(true);
                if (svc != null) {
                    try {
                        svc.authenticate(operationId);
                    } catch (RemoteException e) {
                        Log.e(TAG, "authenticate failed on first attempt, waking and retrying", e);
                        try {
                            svc = getServiceSync(true);
                            if (svc != null) {
                                svc.authenticate(operationId);
                                return;
                            }
                        } catch (Throwable t) {
                            Log.e(TAG, "authenticate retry failed", t);
                        }
                        mAuthOrEnrollActive = false;
                        try {
                            mCallback.onError(Error.HW_UNAVAILABLE, 0);
                        } catch (RemoteException ignored) {
                        }
                    }
                } else {
                    mAuthOrEnrollActive = false;
                    try {
                        mCallback.onError(Error.HW_UNAVAILABLE, 0);
                    } catch (RemoteException ignored) {
                    }
                }
            });
            return createCancelSignal();
        }

        @Override
        public ICancellationSignal detectInteraction() {
            return createCancelSignal();
        }

        @Override
        public void enumerateEnrollments() {
            sWorkHandler.post(() -> {
                ISenseService svc = getServiceSync();
                if (svc != null) {
                    try {
                        svc.enumerate();
                        return;
                    } catch (RemoteException e) {
                        Log.e(TAG, "enumerate failed", e);
                    }
                }
                try {
                    if (mServesUser) {
                        // Never report an empty list when the enrollments could not be read: the
                        // framework would delete its own record of the user's face.
                        mCallback.onError(Error.HW_UNAVAILABLE, 0);
                    } else {
                        mCallback.onEnrollmentsEnumerated(new int[0]);
                    }
                } catch (RemoteException ignored) {
                }
            });
        }

        @Override
        public void removeEnrollments(int[] enrollmentIds) {
            sWorkHandler.post(() -> {
                ISenseService svc = getServiceSync();
                if (svc != null) {
                    try {
                        int id = (enrollmentIds != null && enrollmentIds.length > 0) ? enrollmentIds[0] : 0;
                        svc.remove(id);
                        return;
                    } catch (RemoteException e) {
                        Log.e(TAG, "remove failed", e);
                    }
                }
                try {
                    if (mServesUser) {
                        mCallback.onError(Error.UNABLE_TO_REMOVE, 0);
                    } else {
                        mCallback.onEnrollmentsRemoved(
                                enrollmentIds != null ? enrollmentIds : new int[0]);
                    }
                } catch (RemoteException ignored) {
                }
            });
        }

        @Override
        public void getFeatures() {
            sWorkHandler.post(() -> {
                try {
                    mCallback.onFeaturesRetrieved(new byte[0]);
                } catch (RemoteException e) {
                    Log.e(TAG, "onFeaturesRetrieved failed", e);
                }
            });
        }

        @Override
        public void setFeature(HardwareAuthToken hat, byte feature, boolean enabled) {
            sWorkHandler.post(() -> {
                ISenseService svc = getServiceSync();
                if (svc != null) {
                    try {
                        svc.setFeature(feature, enabled, toByteArray(hat), 1);
                    } catch (RemoteException e) {
                        Log.e(TAG, "setFeature failed", e);
                    }
                }
                try {
                    mCallback.onFeatureSet(feature);
                } catch (RemoteException e) {
                    Log.e(TAG, "onFeatureSet failed", e);
                }
            });
        }

        @Override
        public void getAuthenticatorId() {
            sWorkHandler.post(() -> {
                long authId = 0L;
                ISenseService svc = getServiceSync();
                if (svc != null) {
                    try {
                        authId = svc.getAuthenticatorId();
                    } catch (RemoteException e) {
                        Log.e(TAG, "getAuthenticatorId failed", e);
                    }
                }
                try {
                    mCallback.onAuthenticatorIdRetrieved(authId);
                } catch (RemoteException e) {
                    Log.e(TAG, "onAuthenticatorIdRetrieved failed", e);
                }
            });
        }

        @Override
        public void invalidateAuthenticatorId() {
            sWorkHandler.post(() -> {
                long authId = System.currentTimeMillis();
                try {
                    mCallback.onAuthenticatorIdInvalidated(authId);
                } catch (RemoteException e) {
                    Log.e(TAG, "onAuthenticatorIdInvalidated failed", e);
                }
            });
        }

        @Override
        public void resetLockout(HardwareAuthToken hat) {
            sWorkHandler.post(() -> {
                ISenseService svc = getServiceSync();
                if (svc != null) {
                    try {
                        svc.resetLockout(toByteArray(hat));
                    } catch (RemoteException e) {
                        Log.e(TAG, "resetLockout failed", e);
                    }
                }
                try {
                    mCallback.onLockoutCleared();
                } catch (RemoteException e) {
                    Log.e(TAG, "onLockoutCleared failed", e);
                }
            });
        }

        @Override
        public void close() {
            synchronized (sLock) {
                if (sSessions.get(mUserId) == this) sSessions.remove(mUserId);
            }
            sWorkHandler.post(() -> {
                try {
                    mCallback.onSessionClosed();
                } catch (RemoteException e) {
                    Log.e(TAG, "onSessionClosed failed", e);
                }
            });
        }

        @Override
        public ICancellationSignal authenticateWithContext(long operationId, OperationContext context) {
            return authenticate(operationId);
        }

        @Override
        public ICancellationSignal enrollWithContext(HardwareAuthToken hat, byte type, byte[] features,
                NativeHandle previewSurface, OperationContext context) {
            return enroll(hat, type, features, previewSurface);
        }

        @Override
        public ICancellationSignal detectInteractionWithContext(OperationContext context) {
            return detectInteraction();
        }

        @Override
        public void onContextChanged(OperationContext context) {
        }

        @Override
        public ICancellationSignal enrollWithOptions(FaceEnrollOptions options) {
            if (options == null) {
                return enroll(null, (byte) 0, new byte[0], null);
            }
            return enroll(options.hardwareAuthToken, options.enrollmentType, options.features,
                    options.nativeHandlePreview);
        }
    }
}
