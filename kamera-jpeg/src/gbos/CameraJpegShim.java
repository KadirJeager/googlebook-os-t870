package gbos;

import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.graphics.ImageFormat;
import android.graphics.Rect;
import android.graphics.YuvImage;
import android.hardware.camera2.CaptureRequest;
import android.hardware.camera2.ICameraDeviceUser;
import android.hardware.camera2.impl.CameraMetadataNative;
import android.hardware.camera2.impl.ICameraDeviceUserWrapper;
import android.hardware.camera2.params.OutputConfiguration;
import android.hardware.camera2.params.SessionConfiguration;
import android.hardware.camera2.utils.OutputAndInputStreamIds;
import android.hardware.camera2.utils.SessionConfigurationAndStreamIds;
import android.hardware.camera2.utils.SubmitInfo;
import android.hardware.camera2.utils.SurfaceUtils;
import android.location.Location;
import android.media.Image;
import android.media.ImageReader;
import android.media.ImageWriter;
import android.os.Build;
import android.os.Handler;
import android.os.HandlerThread;
import android.os.RemoteException;
import android.util.Log;
import android.util.Size;
import android.util.SparseArray;
import android.view.Surface;

import java.io.ByteArrayOutputStream;
import java.lang.reflect.Field;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.text.SimpleDateFormat;
import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Collections;
import java.util.Date;
import java.util.List;
import java.util.Locale;
import java.util.WeakHashMap;

/**
 * JPEG stills next to a video encoder, produced by the system instead of the camera HAL.
 *
 * The LineageOS/Samsung CamX HAL of the SM-T870 locks up in its "Video With Liveshot" usecase:
 * when a session has a preview, a hardware video encoder stream and a JPEG (BLOB) stream, it
 * takes every video buffer and fills none, so recording freezes in every app that binds a still
 * capture next to a video capture (CameraX does, on the back camera). Preview + encoder + YUV of
 * the same size works at every size (T870-araclar/acilis-loglari/kamera-video-20261022/KOK-NEDEN.md).
 *
 * This class sits behind android.hardware.camera2.impl.ICameraDeviceUserWrapper (its five binder
 * calls are routed through CameraBridge by yamalar/kamera_jpeg.py) and does, per camera device:
 *  - createStream / endConfigure (the one-stream-per-call path): remembers which stream is the
 *    app's JPEG output and whether an encoder output exists; when both exist, endConfigure
 *    replaces the HAL JPEG stream by a YUV_420_888 stream of the same size that this class
 *    consumes, and requests aimed at the app's stream id are pointed at the YUV stream;
 *  - configureStreams (Android 17's batched path, the one CameraDeviceImpl uses on this image):
 *    the JPEG output in the session delta is swapped for the YUV output at the same position, so
 *    the stream id the app receives for its JPEG output already is the YUV stream's; the app's
 *    JPEG surface is fed through an ImageWriter;
 *  - submitRequestList: points requests that target the JPEG stream at the YUV stream and keeps
 *    the request's JPEG parameters (orientation, quality, thumbnail, GPS);
 *  - on every YUV frame: encodes the JPEG in software (YuvImage), prepends an EXIF block the way
 *    a HAL would (orientation, dimensions, date, make/model, thumbnail, GPS), appends the
 *    camera3_jpeg_blob trailer and queues the buffer to the app's surface with the frame's
 *    timestamp, so the app matches it with the capture result as before;
 *  - deleteStream / disconnect: maps the app's stream id and releases everything.
 * Sessions without an encoder keep the HAL JPEG path untouched. Nothing here depends on the app.
 */
public final class CameraJpegShim {
    private static final String TAG = "GbosCameraJpeg";
    private static final int OPERATING_MODE_NORMAL = 0;
    /** SurfaceUtils.getSurfaceFormat reports the HAL format: a JPEG consumer is HAL_PIXEL_FORMAT_BLOB. */
    private static final int HAL_PIXEL_FORMAT_BLOB = 0x21;
    private static final int YUV_BUFFERS = 3;
    private static final int JPEG_BUFFERS = 2;
    private static final int DEFAULT_QUALITY = 95;
    private static final int DEFAULT_THUMB_QUALITY = 85;
    /** camera3_jpeg_blob: u16 jpeg_blob_id (0x00FF), 2 bytes padding, u32 jpeg_size. */
    private static final int BLOB_TRAILER = 8;
    private static final short BLOB_ID = 0x00FF;

    private static final WeakHashMap<ICameraDeviceUserWrapper, Device> DEVICES = new WeakHashMap<>();
    private static Field sRemoteField;
    private static Field sStreamIdxField;

    private CameraJpegShim() { }

    /** JPEG settings of one still request, taken from the CaptureRequest. */
    static final class JpegParams {
        int orientation;
        int quality = DEFAULT_QUALITY;
        int thumbWidth;
        int thumbHeight;
        int thumbQuality = DEFAULT_THUMB_QUALITY;
        Location gps;
    }

    /** One opened camera device (one ICameraDeviceUserWrapper). */
    static final class Device {
        final ICameraDeviceUser remote;
        final SparseArray<OutputConfiguration> streams = new SparseArray<>();
        int jpegId = -1;
        OutputConfiguration jpegConfig;
        Surface appJpeg;
        Size jpegSize;
        int encoderId = -1;
        int yuvId = -1;
        ImageReader reader;
        ImageWriter writer;
        HandlerThread thread;
        final ArrayDeque<JpegParams> pending = new ArrayDeque<>();
        JpegParams last = new JpegParams();

        Device(ICameraDeviceUser remote) {
            this.remote = remote;
        }

        boolean substituted() {
            return yuvId >= 0;
        }

        /** Releases the software JPEG path (the server stream itself is the caller's business). */
        void release() {
            ImageReader r = reader;
            ImageWriter w = writer;
            HandlerThread t = thread;
            reader = null;
            writer = null;
            thread = null;
            yuvId = -1;
            pending.clear();
            if (r != null) {
                try { r.close(); } catch (Throwable e) { Log.w(TAG, "reader close", e); }
            }
            if (w != null) {
                try { w.close(); } catch (Throwable e) { Log.w(TAG, "writer close", e); }
            }
            if (t != null) {
                t.quitSafely();
            }
        }

        void forgetJpeg() {
            release();
            jpegId = -1;
            jpegConfig = null;
            appJpeg = null;
            jpegSize = null;
        }
    }

    // ---------------------------------------------------------------- wrapper entry points

    private static synchronized Device device(ICameraDeviceUserWrapper w) {
        Device d = DEVICES.get(w);
        if (d == null) {
            d = new Device(remoteOf(w));
            DEVICES.put(w, d);
        }
        return d;
    }

    private static ICameraDeviceUser remoteOf(ICameraDeviceUserWrapper w) {
        try {
            if (sRemoteField == null) {
                Field f = ICameraDeviceUserWrapper.class.getDeclaredField("mRemoteDevice");
                f.setAccessible(true);
                sRemoteField = f;
            }
            return (ICameraDeviceUser) sRemoteField.get(w);
        } catch (ReflectiveOperationException e) {
            throw new IllegalStateException("ICameraDeviceUserWrapper.mRemoteDevice", e);
        }
    }

    private static int[] streamIds(CaptureRequest r) {
        try {
            if (sStreamIdxField == null) {
                Field f = CaptureRequest.class.getDeclaredField("mStreamIdxArray");
                f.setAccessible(true);
                sStreamIdxField = f;
            }
            return (int[]) sStreamIdxField.get(r);
        } catch (ReflectiveOperationException e) {
            throw new IllegalStateException("CaptureRequest.mStreamIdxArray", e);
        }
    }

    private static void setStreamIds(CaptureRequest r, int[] ids) {
        try {
            sStreamIdxField.set(r, ids);
        } catch (ReflectiveOperationException e) {
            throw new IllegalStateException("CaptureRequest.mStreamIdxArray", e);
        }
    }

    public static int createStream(ICameraDeviceUserWrapper w, OutputConfiguration c) throws RemoteException {
        Device d = device(w);
        int id = d.remote.createStream(c);
        synchronized (d) {
            d.streams.put(id, c);
            int role = classify(c, d, "stream " + id);
            if (role == 1 && d.jpegId < 0) {
                d.jpegId = id;
                d.jpegConfig = c;
                d.appJpeg = c.getSurfaces().get(0);
                d.jpegSize = SurfaceUtils.getSurfaceSize(d.appJpeg);
            } else if (role == 2) {
                d.encoderId = id;
            }
        }
        return id;
    }

    public static void deleteStream(ICameraDeviceUserWrapper w, int streamId) throws RemoteException {
        Device d = device(w);
        int serverId = streamId;
        synchronized (d) {
            if (streamId == d.jpegId) {
                if (d.substituted()) {
                    serverId = d.yuvId;  // same as streamId on the batched path
                }
                d.forgetJpeg();
            } else if (streamId == d.encoderId) {
                d.encoderId = -1;
            }
            d.streams.remove(streamId);
        }
        d.remote.deleteStream(serverId);
    }

    public static int[] endConfigure(ICameraDeviceUserWrapper w, int operatingMode, CameraMetadataNative sessionParams,
                                     long startTimeMs) throws RemoteException {
        Device d = device(w);
        synchronized (d) {
            if (operatingMode == OPERATING_MODE_NORMAL && d.jpegId >= 0 && d.encoderId >= 0 && !d.substituted()) {
                substitute(d);
            }
        }
        return d.remote.endConfigure(operatingMode, sessionParams, startTimeMs);
    }

    public static SubmitInfo submitRequestList(ICameraDeviceUserWrapper w, CaptureRequest[] requests, boolean repeating)
            throws RemoteException {
        Device d = device(w);
        synchronized (d) {
            if (d.substituted()) {
                for (CaptureRequest r : requests) {
                    int[] ids = streamIds(r);
                    if (ids == null) {
                        continue;
                    }
                    boolean hit = false;
                    for (int id : ids) {
                        hit |= id == d.jpegId;
                    }
                    if (!hit) {
                        continue;
                    }
                    if (d.yuvId != d.jpegId) {  // one-stream-per-call path: the app's id is the old JPEG stream
                        int[] copy = ids.clone();
                        for (int i = 0; i < copy.length; i++) {
                            if (copy[i] == d.jpegId) {
                                copy[i] = d.yuvId;
                            }
                        }
                        setStreamIds(r, copy);
                    }
                    JpegParams p = paramsOf(r);
                    d.last = p;
                    if (!repeating) {
                        d.pending.add(p);
                    }
                }
            }
        }
        return d.remote.submitRequestList(requests, repeating);
    }

    public static void disconnect(ICameraDeviceUserWrapper w) throws RemoteException {
        Device d;
        synchronized (CameraJpegShim.class) {
            d = DEVICES.remove(w);
        }
        if (d != null) {
            synchronized (d) {
                d.forgetJpeg();
            }
        }
        remoteOf(w).disconnect();
    }

    // ---------------------------------------------------------------- substitution

    /** Replaces the HAL JPEG stream by a YUV stream of the same size; the app's surface is fed by us. */
    private static void substitute(Device d) throws RemoteException {
        int w = d.jpegSize.getWidth(), h = d.jpegSize.getHeight();
        ImageReader reader = ImageReader.newInstance(w, h, ImageFormat.YUV_420_888, YUV_BUFFERS);
        OutputConfiguration yuv = yuvConfigFor(reader, d.jpegConfig);
        int yuvId;
        try {
            yuvId = d.remote.createStream(yuv);
        } catch (RemoteException | RuntimeException e) {
            reader.close();
            Log.e(TAG, "YUV stream for " + w + "x" + h + " refused; HAL JPEG stays", e);
            return;
        }
        try {
            d.remote.deleteStream(d.jpegId);
        } catch (RemoteException | RuntimeException e) {
            try { d.remote.deleteStream(yuvId); } catch (Throwable ignored) { }
            reader.close();
            throw e;
        }
        attach(d, reader, yuvId);
    }

    /** The YUV reader of this camera and its app surface by the writer; from here on frames flow. */
    private static void attach(Device d, ImageReader reader, int yuvId) {
        int w = d.jpegSize.getWidth(), h = d.jpegSize.getHeight();
        // Blob buffers are sized like a HAL would size them: enough for a JPEG of this many pixels.
        int maxBlob = Math.max(w * h, 1 << 20) + BLOB_TRAILER;
        ImageWriter writer = new ImageWriter.Builder(d.appJpeg)
                .setImageFormat(ImageFormat.JPEG)
                .setWidthAndHeight(maxBlob, 1)
                .setMaxImages(JPEG_BUFFERS)
                .build();
        HandlerThread thread = new HandlerThread("GbosCameraJpeg");
        thread.start();
        d.reader = reader;
        d.writer = writer;
        d.thread = thread;
        d.yuvId = yuvId;
        final Device dev = d;
        reader.setOnImageAvailableListener(new ImageReader.OnImageAvailableListener() {
            @Override
            public void onImageAvailable(ImageReader r) {
                onFrame(dev, r);
            }
        }, new Handler(thread.getLooper()));
        Log.i(TAG, "JPEG stream " + d.jpegId + " (" + w + "x" + h + ") now YUV stream " + yuvId
                + " with software JPEG (encoder stream " + d.encoderId + " present)");
    }

    private static OutputConfiguration yuvConfigFor(ImageReader reader, OutputConfiguration jpegConfig) {
        OutputConfiguration yuv = new OutputConfiguration(reader.getSurface());
        String physical = physicalCameraId(jpegConfig);  // getter is hidden, setter is public
        if (physical != null) {
            yuv.setPhysicalCameraId(physical);
        }
        return yuv;
    }

    /** Replaces the output list inside a SessionConfiguration (its list is unmodifiable, the
     *  field is private); everything else in it (type, session parameters, input, color space)
     *  stays as the app set it. */
    private static void setOutputs(SessionConfiguration sc, List<OutputConfiguration> outs) {
        try {
            Field f = SessionConfiguration.class.getDeclaredField("mOutputConfigurations");
            f.setAccessible(true);
            f.set(sc, Collections.unmodifiableList(new ArrayList<>(outs)));
        } catch (ReflectiveOperationException e) {
            throw new IllegalStateException("SessionConfiguration.mOutputConfigurations", e);
        }
    }

    /** Which role a surface plays: 1 = JPEG consumer, 2 = hardware video encoder, 0 = other. */
    private static int classify(OutputConfiguration c, Device d, String what) {
        List<Surface> surfaces = c.getSurfaces();
        Surface s = surfaces.isEmpty() ? null : surfaces.get(0);
        if (s == null) {
            return 0;  // deferred surface: finalized later, never a HAL JPEG of our concern
        }
        try {
            int format = SurfaceUtils.getSurfaceFormat(s);
            boolean jpeg = (format == HAL_PIXEL_FORMAT_BLOB || format == ImageFormat.JPEG) && surfaces.size() == 1;
            boolean encoder = SurfaceUtils.isSurfaceForHwVideoEncoder(s);
            Log.i(TAG, what + ": format " + Integer.toHexString(format) + (jpeg ? " (JPEG)" : "")
                    + (encoder ? " video encoder" : "") + " " + SurfaceUtils.getSurfaceSize(s));
            return jpeg ? 1 : encoder ? 2 : 0;
        } catch (Throwable t) {
            Log.w(TAG, what + ": cannot classify surface", t);
            return 0;
        }
    }

    public static OutputAndInputStreamIds configureStreams(ICameraDeviceUserWrapper w, SessionConfigurationAndStreamIds s)
            throws RemoteException {
        Device d = device(w);
        ImageReader reader = null;
        int jpegIndex = -1;
        boolean existingJpeg = false;  // a HAL JPEG stream of an earlier session now meets an encoder
        OutputConfiguration jpegCfg = null;
        List<Integer> encoderIdx = new ArrayList<>();
        synchronized (d) {
            if (s.deletedStreamIds != null) {
                for (int id : s.deletedStreamIds) {
                    if (id == d.jpegId) {
                        d.forgetJpeg();
                    } else if (id == d.encoderId) {
                        d.encoderId = -1;
                    }
                    d.streams.remove(id);
                }
            }
            SessionConfiguration delta = s.sessionConfigurationDelta;
            List<OutputConfiguration> outs = delta == null ? Collections.<OutputConfiguration>emptyList()
                    : delta.getOutputConfigurations();
            boolean encoder = d.encoderId >= 0;
            for (int i = 0; i < outs.size(); i++) {
                int role = classify(outs.get(i), d, "new output " + i);
                if (role == 1 && jpegIndex < 0 && d.jpegId < 0) {
                    jpegIndex = i;
                    jpegCfg = outs.get(i);
                } else if (role == 2) {
                    encoder = true;
                    encoderIdx.add(i);
                }
            }
            if (delta != null && delta.getSessionType() == SessionConfiguration.SESSION_REGULAR && encoder) {
                if (jpegIndex >= 0) {
                    Size size = SurfaceUtils.getSurfaceSize(jpegCfg.getSurfaces().get(0));
                    reader = ImageReader.newInstance(size.getWidth(), size.getHeight(), ImageFormat.YUV_420_888, YUV_BUFFERS);
                    List<OutputConfiguration> copy = new ArrayList<>(outs);
                    copy.set(jpegIndex, yuvConfigFor(reader, jpegCfg));
                    setOutputs(delta, copy);
                    d.jpegConfig = jpegCfg;
                    d.appJpeg = jpegCfg.getSurfaces().get(0);
                    d.jpegSize = size;
                } else if (d.jpegId >= 0 && !d.substituted()) {
                    reader = ImageReader.newInstance(d.jpegSize.getWidth(), d.jpegSize.getHeight(),
                            ImageFormat.YUV_420_888, YUV_BUFFERS);
                    List<OutputConfiguration> copy = new ArrayList<>(outs);
                    copy.add(yuvConfigFor(reader, d.jpegConfig));  // trailing id: CameraDeviceImpl ignores it
                    setOutputs(delta, copy);
                    int[] del = s.deletedStreamIds == null ? new int[0] : s.deletedStreamIds;
                    int[] more = new int[del.length + 1];
                    System.arraycopy(del, 0, more, 0, del.length);
                    more[del.length] = d.jpegId;
                    s.deletedStreamIds = more;
                    existingJpeg = true;
                }
            }
        }
        OutputAndInputStreamIds r;
        try {
            r = d.remote.configureStreams(s);
        } catch (RemoteException | RuntimeException e) {
            if (reader != null) {
                reader.close();
            }
            Log.e(TAG, "configureStreams failed" + (reader != null ? " with the YUV substitute" : ""), e);
            throw e;
        }
        synchronized (d) {
            int[] ids = r.outputStreamIds == null ? new int[0] : r.outputStreamIds;
            List<OutputConfiguration> outs = s.sessionConfigurationDelta == null
                    ? Collections.<OutputConfiguration>emptyList() : s.sessionConfigurationDelta.getOutputConfigurations();
            for (int i = 0; i < Math.min(ids.length, outs.size()); i++) {
                d.streams.put(ids[i], outs.get(i));
                if (encoderIdx.contains(i)) {
                    d.encoderId = ids[i];
                }
            }
            if (reader != null) {
                if (existingJpeg) {
                    attach(d, reader, ids[ids.length - 1]);  // app keeps its old id, requests are mapped
                } else {
                    d.jpegId = ids[jpegIndex];
                    attach(d, reader, ids[jpegIndex]);     // app id == YUV stream id, nothing to map
                }
            }
        }
        return r;
    }

    private static String physicalCameraId(OutputConfiguration c) {
        try {
            return (String) OutputConfiguration.class.getMethod("getPhysicalCameraId").invoke(c);
        } catch (ReflectiveOperationException | RuntimeException e) {
            return null;
        }
    }

    private static JpegParams paramsOf(CaptureRequest r) {
        JpegParams p = new JpegParams();
        Integer o = r.get(CaptureRequest.JPEG_ORIENTATION);
        if (o != null) {
            p.orientation = ((o % 360) + 360) % 360;
        }
        Byte q = r.get(CaptureRequest.JPEG_QUALITY);
        if (q != null && q > 0) {
            p.quality = q & 0xFF;
        }
        Size ts = r.get(CaptureRequest.JPEG_THUMBNAIL_SIZE);
        if (ts != null) {
            p.thumbWidth = ts.getWidth();
            p.thumbHeight = ts.getHeight();
        }
        Byte tq = r.get(CaptureRequest.JPEG_THUMBNAIL_QUALITY);
        if (tq != null && tq > 0) {
            p.thumbQuality = tq & 0xFF;
        }
        p.gps = r.get(CaptureRequest.JPEG_GPS_LOCATION);
        return p;
    }

    /* package: called from the listener's anonymous class; private would need nest-based access,
       which ART lacks and d8 is told not to desugar */
    static void onFrame(Device d, ImageReader reader) {
        Image img = reader.acquireNextImage();
        if (img == null) {
            return;
        }
        try {
            JpegParams p;
            ImageWriter writer;
            synchronized (d) {
                p = d.pending.poll();
                if (p == null) {
                    p = d.last;
                }
                writer = d.writer;
            }
            if (writer == null) {
                return;  // session already torn down
            }
            long t0 = System.nanoTime();
            byte[] jpeg = encode(img, p);
            deliver(writer, jpeg, img.getTimestamp(), img, p);
            Log.i(TAG, "still " + img.getWidth() + "x" + img.getHeight() + " -> " + jpeg.length + " bytes in "
                    + (System.nanoTime() - t0) / 1000000 + " ms (orientation " + p.orientation + ", q" + p.quality + ")");
        } catch (Throwable t) {
            Log.e(TAG, "software JPEG failed; the app gets no image for this request", t);
        } finally {
            img.close();
        }
    }

    private static void deliver(ImageWriter writer, byte[] jpeg, long timestamp, Image src, JpegParams p) {
        Image out = writer.dequeueInputImage();
        try {
            ByteBuffer b = out.getPlanes()[0].getBuffer();
            int cap = b.capacity();
            int q = p.quality;
            while (jpeg.length + BLOB_TRAILER > cap && q > 30) {
                q -= 10;  // a blob buffer is sized for the HAL's quality; squeeze a too large JPEG
                JpegParams lower = new JpegParams();
                lower.orientation = p.orientation;
                lower.quality = q;
                lower.thumbWidth = p.thumbWidth;
                lower.thumbHeight = p.thumbHeight;
                lower.thumbQuality = p.thumbQuality;
                lower.gps = p.gps;
                jpeg = encode(src, lower);
            }
            if (jpeg.length + BLOB_TRAILER > cap) {
                throw new IllegalStateException("JPEG " + jpeg.length + " bytes does not fit the blob buffer " + cap);
            }
            b.clear();
            b.put(jpeg);
            ByteBuffer trailer = ByteBuffer.allocate(BLOB_TRAILER).order(ByteOrder.nativeOrder());
            trailer.putShort(BLOB_ID).putShort((short) 0).putInt(jpeg.length);
            trailer.flip();
            b.position(cap - BLOB_TRAILER);
            b.put(trailer);
            out.setTimestamp(timestamp);
            writer.queueInputImage(out);
            out = null;
        } finally {
            if (out != null) {
                out.close();
            }
        }
    }

    // ---------------------------------------------------------------- encoding

    /** The complete JPEG (EXIF included) of a YUV_420_888 image, as the app expects from the HAL. */
    public static byte[] encode(Image img, JpegParams p) {
        int w = img.getWidth(), h = img.getHeight();
        byte[] nv21 = toNv21(img);
        ByteArrayOutputStream bos = new ByteArrayOutputStream(w * h / 4);
        new YuvImage(nv21, ImageFormat.NV21, w, h, null).compressToJpeg(new Rect(0, 0, w, h), p.quality, bos);
        byte[] body = bos.toByteArray();
        byte[] thumb = null;
        if (p.thumbWidth > 0 && p.thumbHeight > 0) {
            thumb = thumbnail(body, w, h, p);
        }
        byte[] app1 = Exif.segment(w, h, p.orientation, p.gps, thumb);
        return Exif.insert(body, app1);
    }

    /** Public variant for the on-device test harness (cihaz-kabul/KameraShimDeneme.java). */
    public static byte[] encodeForTest(Image img, int orientation, int quality, int thumbW, int thumbH, Location gps) {
        JpegParams p = new JpegParams();
        p.orientation = orientation;
        p.quality = quality;
        p.thumbWidth = thumbW;
        p.thumbHeight = thumbH;
        p.gps = gps;
        return encode(img, p);
    }

    private static byte[] thumbnail(byte[] body, int w, int h, JpegParams p) {
        try {
            BitmapFactory.Options o = new BitmapFactory.Options();
            int sample = 1;
            while (w / (sample * 2) >= p.thumbWidth && h / (sample * 2) >= p.thumbHeight) {
                sample *= 2;
            }
            o.inSampleSize = sample;
            Bitmap big = BitmapFactory.decodeByteArray(body, 0, body.length, o);
            if (big == null) {
                return null;
            }
            Bitmap small = Bitmap.createScaledBitmap(big, p.thumbWidth, p.thumbHeight, true);
            if (small != big) {
                big.recycle();
            }
            ByteArrayOutputStream bos = new ByteArrayOutputStream();
            small.compress(Bitmap.CompressFormat.JPEG, p.thumbQuality, bos);
            small.recycle();
            return bos.toByteArray();
        } catch (Throwable t) {
            Log.w(TAG, "thumbnail skipped", t);
            return null;
        }
    }

    /** Tightly packed NV21 (Y, then interleaved V/U) from the three planes, any stride layout. */
    static byte[] toNv21(Image img) {
        int w = img.getWidth(), h = img.getHeight();
        Image.Plane[] planes = img.getPlanes();
        byte[] out = new byte[w * h + 2 * ((w + 1) / 2) * ((h + 1) / 2)];
        ByteBuffer y = planes[0].getBuffer();
        int yStride = planes[0].getRowStride();
        for (int row = 0; row < h; row++) {
            y.position(row * yStride);
            y.get(out, row * w, w);
        }
        ByteBuffer u = planes[1].getBuffer(), v = planes[2].getBuffer();
        int cw = (w + 1) / 2, ch = (h + 1) / 2;
        int uStride = planes[1].getRowStride(), vStride = planes[2].getRowStride();
        int uPix = planes[1].getPixelStride(), vPix = planes[2].getPixelStride();
        int pos = w * h;
        if (uPix == 2 && vPix == 2 && uStride == vStride) {
            // Semi-planar: the V plane buffer is V0 U0 V1 U1 ... = NV21's chroma rows already.
            for (int row = 0; row < ch; row++) {
                int start = row * vStride;
                int n = Math.min(2 * cw, v.limit() - start);
                v.position(start);
                v.get(out, pos, n);
                if (n < 2 * cw) {  // the last row's final U byte is only in the U plane
                    out[pos + 2 * cw - 1] = u.get(start + 2 * (cw - 1));
                }
                pos += 2 * cw;
            }
        } else {
            for (int row = 0; row < ch; row++) {
                int ub = row * uStride, vb = row * vStride;
                for (int col = 0; col < cw; col++) {
                    out[pos++] = v.get(vb + col * vPix);
                    out[pos++] = u.get(ub + col * uPix);
                }
            }
        }
        return out;
    }

    // ---------------------------------------------------------------- EXIF

    /** A minimal but complete EXIF APP1 block: TIFF/IFD0, Exif IFD, optional GPS IFD and IFD1 thumbnail. */
    static final class Exif {
        private static final int ASCII = 2, SHORT = 3, LONG = 4, RATIONAL = 5, UNDEFINED = 7;

        static final class Entry {
            final int tag, type, count;
            final byte[] value;  // raw little-endian value bytes (count * type size)

            Entry(int tag, int type, int count, byte[] value) {
                this.tag = tag;
                this.type = type;
                this.count = count;
                this.value = value;
            }
        }

        static Entry ascii(int tag, String s) {
            byte[] b = (s + "\0").getBytes(java.nio.charset.StandardCharsets.US_ASCII);
            return new Entry(tag, ASCII, b.length, b);
        }

        static Entry shortE(int tag, int v) {
            return new Entry(tag, SHORT, 1, le(v, 2));
        }

        static Entry longE(int tag, long v) {
            return new Entry(tag, LONG, 1, le(v, 4));
        }

        static Entry rational(int tag, long... numDen) {
            ByteBuffer b = ByteBuffer.allocate(numDen.length * 4).order(ByteOrder.LITTLE_ENDIAN);
            for (long x : numDen) {
                b.putInt((int) x);
            }
            return new Entry(tag, RATIONAL, numDen.length / 2, b.array());
        }

        static Entry undefined(int tag, byte[] v) {
            return new Entry(tag, UNDEFINED, v.length, v);
        }

        static byte[] le(long v, int n) {
            byte[] b = new byte[n];
            for (int i = 0; i < n; i++) {
                b[i] = (byte) (v >> (8 * i));
            }
            return b;
        }

        /** Size of an IFD block: entry table + next pointer + overflow values. */
        static int ifdSize(List<Entry> entries) {
            int n = 2 + 12 * entries.size() + 4;
            for (Entry e : entries) {
                if (e.value.length > 4) {
                    n += e.value.length + (e.value.length & 1);
                }
            }
            return n;
        }

        static void writeIfd(ByteBuffer out, List<Entry> entries, int base, int next) {
            // entries must be sorted by tag
            List<Entry> sorted = new ArrayList<>(entries);
            sorted.sort(new java.util.Comparator<Entry>() {
                @Override
                public int compare(Entry a, Entry b) {
                    return Integer.compare(a.tag, b.tag);
                }
            });
            int overflow = base + 2 + 12 * sorted.size() + 4;
            out.putShort((short) sorted.size());
            ByteBuffer tail = ByteBuffer.allocate(ifdSize(entries)).order(ByteOrder.LITTLE_ENDIAN);
            for (Entry e : sorted) {
                out.putShort((short) e.tag).putShort((short) e.type).putInt(e.count);
                if (e.value.length <= 4) {
                    out.put(e.value);
                    for (int i = e.value.length; i < 4; i++) {
                        out.put((byte) 0);
                    }
                } else {
                    out.putInt(overflow);
                    tail.put(e.value);
                    if ((e.value.length & 1) == 1) {
                        tail.put((byte) 0);
                    }
                    overflow += e.value.length + (e.value.length & 1);
                }
            }
            out.putInt(next);
            tail.flip();
            out.put(tail);
        }

        static byte[] segment(int w, int h, int orientation, Location gps, byte[] thumb) {
            int exifOrientation;
            switch (orientation) {
                case 90: exifOrientation = 6; break;
                case 180: exifOrientation = 3; break;
                case 270: exifOrientation = 8; break;
                default: exifOrientation = 1;
            }
            String now = new SimpleDateFormat("yyyy:MM:dd HH:mm:ss", Locale.US).format(new Date());
            List<Entry> ifd0 = new ArrayList<>();
            ifd0.add(ascii(0x010F, Build.MANUFACTURER));
            ifd0.add(ascii(0x0110, Build.MODEL));
            ifd0.add(shortE(0x0112, exifOrientation));
            ifd0.add(rational(0x011A, 72, 1));
            ifd0.add(rational(0x011B, 72, 1));
            ifd0.add(shortE(0x0128, 2));
            ifd0.add(ascii(0x0131, "Googlebook OS " + Build.DISPLAY));
            ifd0.add(ascii(0x0132, now));
            ifd0.add(shortE(0x0213, 1));  // YCbCrPositioning: centered
            ifd0.add(longE(0x8769, 0));  // Exif IFD pointer, patched below
            List<Entry> exif = new ArrayList<>();
            exif.add(undefined(0x9000, new byte[] {'0', '2', '3', '2'}));
            exif.add(ascii(0x9003, now));
            exif.add(ascii(0x9004, now));
            exif.add(undefined(0x9101, new byte[] {1, 2, 3, 0}));
            exif.add(undefined(0xA000, new byte[] {'0', '1', '0', '0'}));
            exif.add(shortE(0xA001, 1));  // sRGB
            exif.add(longE(0xA002, w));
            exif.add(longE(0xA003, h));
            List<Entry> gpsIfd = null;
            if (gps != null) {
                gpsIfd = gpsEntries(gps);
                ifd0.add(longE(0x8825, 0));  // GPS IFD pointer, patched below
            }
            List<Entry> ifd1 = null;
            if (thumb != null) {
                ifd1 = new ArrayList<>();
                ifd1.add(shortE(0x0103, 6));
                ifd1.add(rational(0x011A, 72, 1));
                ifd1.add(rational(0x011B, 72, 1));
                ifd1.add(shortE(0x0128, 2));
                ifd1.add(longE(0x0201, 0));  // thumbnail offset, patched below
                ifd1.add(longE(0x0202, thumb.length));
            }
            // Layout inside the TIFF: header(8) | IFD0 | Exif IFD | [GPS IFD] | [IFD1] | [thumb]
            int ifd0Off = 8;
            int exifOff = ifd0Off + ifdSize(ifd0);
            int gpsOff = exifOff + ifdSize(exif);
            int ifd1Off = gpsOff + (gpsIfd != null ? ifdSize(gpsIfd) : 0);
            int thumbOff = ifd1Off + (ifd1 != null ? ifdSize(ifd1) : 0);
            int total = thumbOff + (thumb != null ? thumb.length : 0);
            replace(ifd0, 0x8769, longE(0x8769, exifOff));
            if (gpsIfd != null) {
                replace(ifd0, 0x8825, longE(0x8825, gpsOff));
            }
            if (ifd1 != null) {
                replace(ifd1, 0x0201, longE(0x0201, thumbOff));
            }
            ByteBuffer tiff = ByteBuffer.allocate(total).order(ByteOrder.LITTLE_ENDIAN);
            tiff.put((byte) 'I').put((byte) 'I').putShort((short) 42).putInt(8);
            writeIfd(tiff, ifd0, ifd0Off, ifd1 != null ? ifd1Off : 0);
            writeIfd(tiff, exif, exifOff, 0);
            if (gpsIfd != null) {
                writeIfd(tiff, gpsIfd, gpsOff, 0);
            }
            if (ifd1 != null) {
                writeIfd(tiff, ifd1, ifd1Off, 0);
                tiff.put(thumb);
            }
            byte[] body = tiff.array();
            int len = 2 + 6 + body.length;  // length field + "Exif\0\0" + TIFF
            if (len > 0xFFFF) {
                throw new IllegalStateException("EXIF block too large: " + len);
            }
            ByteBuffer app1 = ByteBuffer.allocate(2 + len).order(ByteOrder.BIG_ENDIAN);
            app1.put((byte) 0xFF).put((byte) 0xE1).putShort((short) len);
            app1.put(new byte[] {'E', 'x', 'i', 'f', 0, 0});
            app1.put(body);
            return app1.array();
        }

        static void replace(List<Entry> list, int tag, Entry e) {
            for (int i = 0; i < list.size(); i++) {
                if (list.get(i).tag == tag) {
                    list.set(i, e);
                    return;
                }
            }
            list.add(e);
        }

        static List<Entry> gpsEntries(Location l) {
            List<Entry> g = new ArrayList<>();
            g.add(new Entry(0x0000, 1, 4, new byte[] {2, 3, 0, 0}));
            g.add(ascii(0x0001, l.getLatitude() >= 0 ? "N" : "S"));
            g.add(dms(0x0002, Math.abs(l.getLatitude())));
            g.add(ascii(0x0003, l.getLongitude() >= 0 ? "E" : "W"));
            g.add(dms(0x0004, Math.abs(l.getLongitude())));
            if (l.hasAltitude()) {
                g.add(new Entry(0x0005, 1, 1, new byte[] {(byte) (l.getAltitude() < 0 ? 1 : 0)}));
                g.add(rational(0x0006, Math.round(Math.abs(l.getAltitude()) * 1000), 1000));
            }
            SimpleDateFormat utc = new SimpleDateFormat("yyyy:MM:dd", Locale.US);
            utc.setTimeZone(java.util.TimeZone.getTimeZone("UTC"));
            long t = l.getTime() / 1000;
            long sec = ((t % 86400) + 86400) % 86400;
            g.add(rational(0x0007, sec / 3600, 1, (sec % 3600) / 60, 1, sec % 60, 1));
            g.add(ascii(0x001D, utc.format(new Date(l.getTime()))));
            String provider = l.getProvider();
            if (provider != null) {
                byte[] p = ("ASCII\0\0\0" + provider).getBytes(java.nio.charset.StandardCharsets.US_ASCII);
                g.add(undefined(0x001B, p));
            }
            return g;
        }

        static Entry dms(int tag, double deg) {
            long d = (long) deg;
            double mf = (deg - d) * 60;
            long m = (long) mf;
            long s = Math.round((mf - m) * 60 * 1000);
            return rational(tag, d, 1, m, 1, s, 1000);
        }

        /** The APP1 block goes right after SOI, before any JFIF/other segment the encoder wrote. */
        static byte[] insert(byte[] jpeg, byte[] app1) {
            if (jpeg.length < 2 || (jpeg[0] & 0xFF) != 0xFF || (jpeg[1] & 0xFF) != 0xD8) {
                throw new IllegalStateException("not a JPEG");
            }
            byte[] out = new byte[jpeg.length + app1.length];
            out[0] = jpeg[0];
            out[1] = jpeg[1];
            System.arraycopy(app1, 0, out, 2, app1.length);
            System.arraycopy(jpeg, 2, out, 2 + app1.length, jpeg.length - 2);
            return out;
        }
    }
}
