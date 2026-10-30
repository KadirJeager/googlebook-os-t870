import android.content.Context;
import android.graphics.ImageFormat;
import android.hardware.HardwareBuffer;
import android.hardware.camera2.CameraCaptureSession;
import android.hardware.camera2.CameraCharacteristics;
import android.hardware.camera2.CameraDevice;
import android.hardware.camera2.CameraManager;
import android.hardware.camera2.CaptureRequest;
import android.hardware.camera2.params.OutputConfiguration;
import android.hardware.camera2.params.SessionConfiguration;
import android.hardware.camera2.params.StreamConfigurationMap;
import android.location.Location;
import android.media.Image;
import android.media.ImageReader;
import android.media.ImageWriter;
import android.os.Handler;
import android.os.HandlerThread;
import android.os.Looper;
import android.util.Size;

import java.io.FileOutputStream;
import java.nio.ByteBuffer;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicReference;

import gbos.CameraJpegShim;

/**
 * Exercises gbos.CameraJpegShim's encoder on a real still of the tablet's camera, without the
 * framework patch: preview (PRIVATE) + YUV_420_888 at the largest JPEG size, one TEMPLATE_STILL_CAPTURE,
 * the YUV image goes through CameraJpegShim.encodeForTest (software JPEG + EXIF with orientation 90,
 * a 320x240 thumbnail and a GPS location) and then through an ImageWriter into a JPEG ImageReader,
 * the way the patched framework feeds the app's surface. Writes /data/local/tmp/gbos-shim.jpg.
 *
 *   CLASSPATH=/data/local/tmp/gbos-shim-deneme.dex app_process /system/bin KameraShimDeneme <cameraId>
 */
public class KameraShimDeneme {
    public static void main(String[] args) throws Exception {
        Looper.prepareMainLooper();
        HandlerThread ht = new HandlerThread("kam");
        ht.start();
        Handler h = new Handler(ht.getLooper());
        String id = args.length > 0 ? args[0] : "0";
        Class<?> at = Class.forName("android.app.ActivityThread");
        Context ctx = (Context) at.getMethod("getSystemContext").invoke(at.getMethod("systemMain").invoke(null));
        CameraManager cm = (CameraManager) ctx.getSystemService(Context.CAMERA_SERVICE);
        CameraCharacteristics cc = cm.getCameraCharacteristics(id);
        StreamConfigurationMap map = cc.get(CameraCharacteristics.SCALER_STREAM_CONFIGURATION_MAP);
        Size still = Arrays.stream(map.getOutputSizes(ImageFormat.JPEG))
                .max((a, b) -> Integer.compare(a.getWidth() * a.getHeight(), b.getWidth() * b.getHeight())).get();
        int w = still.getWidth(), hh = still.getHeight();
        System.out.println("kamera=" + id + " yuv=" + w + "x" + hh);

        ImageReader preview = ImageReader.newInstance(1920, 1080, ImageFormat.PRIVATE, 4,
                HardwareBuffer.USAGE_GPU_SAMPLED_IMAGE);
        preview.setOnImageAvailableListener(r -> { Image i = r.acquireLatestImage(); if (i != null) i.close(); }, h);
        ImageReader yuv = ImageReader.newInstance(w, hh, ImageFormat.YUV_420_888, 3);
        AtomicReference<byte[]> jpeg = new AtomicReference<>();
        AtomicReference<String> info = new AtomicReference<>();
        CountDownLatch gotStill = new CountDownLatch(1);
        yuv.setOnImageAvailableListener(r -> {
            Image img = r.acquireNextImage();
            if (img == null) return;
            try {
                Location loc = new Location("gps");
                loc.setLatitude(39.9208);
                loc.setLongitude(32.8541);
                loc.setAltitude(890);
                loc.setTime(System.currentTimeMillis());
                long t0 = System.nanoTime();
                byte[] out = CameraJpegShim.encodeForTest(img, 90, 95, 320, 240, loc);
                long ms = (System.nanoTime() - t0) / 1000000;
                Image.Plane[] p = img.getPlanes();
                info.set("planes y:" + p[0].getRowStride() + "/" + p[0].getPixelStride() + " u:" + p[1].getRowStride()
                        + "/" + p[1].getPixelStride() + " v:" + p[2].getRowStride() + "/" + p[2].getPixelStride()
                        + " kodlama=" + ms + "ms jpeg=" + out.length + " ts=" + img.getTimestamp());
                jpeg.set(out);
                // the delivery path of the framework patch: ImageWriter into a JPEG consumer
                ImageReader consumer = ImageReader.newInstance(w, hh, ImageFormat.JPEG, 2);
                CountDownLatch got = new CountDownLatch(1);
                AtomicReference<String> seen = new AtomicReference<>();
                HandlerThread ct = new HandlerThread("tuketici");  // its own thread: this one blocks below
                ct.start();
                Handler chh = new Handler(ct.getLooper());
                consumer.setOnImageAvailableListener(cr -> {
                    Image ci = cr.acquireNextImage();
                    ByteBuffer b = ci.getPlanes()[0].getBuffer();
                    seen.set("tuketici: bytes=" + b.remaining() + " ts=" + ci.getTimestamp()
                            + String.format(" bas=%02x%02x son=%02x%02x", b.get(0), b.get(1), b.get(b.remaining() - 2), b.get(b.remaining() - 1)));
                    ci.close();
                    got.countDown();
                }, chh);
                int maxBlob = Math.max(w * hh, 1 << 20) + 8;
                ImageWriter writer = new ImageWriter.Builder(consumer.getSurface()).setImageFormat(ImageFormat.JPEG)
                        .setWidthAndHeight(maxBlob, 1).setMaxImages(2).build();
                Image wi = writer.dequeueInputImage();
                ByteBuffer b = wi.getPlanes()[0].getBuffer();
                b.clear();
                b.put(out);
                ByteBuffer trailer = ByteBuffer.allocate(8).order(java.nio.ByteOrder.nativeOrder());
                trailer.putShort((short) 0x00FF).putShort((short) 0).putInt(out.length);
                trailer.flip();
                b.position(b.capacity() - 8);
                b.put(trailer);
                wi.setTimestamp(img.getTimestamp());
                writer.queueInputImage(wi);
                got.await(5, TimeUnit.SECONDS);
                info.set(info.get() + " | " + seen.get());
                writer.close();
                consumer.close();
            } catch (Throwable t) {
                info.set("HATA: " + t);
            } finally {
                img.close();
                gotStill.countDown();
            }
        }, h);

        CountDownLatch opened = new CountDownLatch(1);
        AtomicReference<CameraDevice> dev = new AtomicReference<>();
        cm.openCamera(id, new CameraDevice.StateCallback() {
            public void onOpened(CameraDevice d) { dev.set(d); opened.countDown(); }
            public void onDisconnected(CameraDevice d) { opened.countDown(); }
            public void onError(CameraDevice d, int e) { System.out.println("acma hatasi " + e); opened.countDown(); }
        }, h);
        opened.await(5, TimeUnit.SECONDS);
        CameraDevice d = dev.get();
        if (d == null) { System.out.println("kamera acilamadi"); System.exit(2); }
        List<OutputConfiguration> outs = new ArrayList<>();
        outs.add(new OutputConfiguration(preview.getSurface()));
        outs.add(new OutputConfiguration(yuv.getSurface()));
        CountDownLatch configured = new CountDownLatch(1);
        AtomicReference<CameraCaptureSession> sess = new AtomicReference<>();
        d.createCaptureSession(new SessionConfiguration(SessionConfiguration.SESSION_REGULAR, outs, r -> h.post(r),
                new CameraCaptureSession.StateCallback() {
                    public void onConfigured(CameraCaptureSession s) { sess.set(s); configured.countDown(); }
                    public void onConfigureFailed(CameraCaptureSession s) { configured.countDown(); }
                }));
        configured.await(5, TimeUnit.SECONDS);
        CameraCaptureSession s = sess.get();
        if (s == null) { System.out.println("oturum kurulamadi"); System.exit(3); }
        CaptureRequest.Builder pb = d.createCaptureRequest(CameraDevice.TEMPLATE_PREVIEW);
        pb.addTarget(preview.getSurface());
        s.setRepeatingRequest(pb.build(), null, h);
        Thread.sleep(1500);
        CaptureRequest.Builder sb = d.createCaptureRequest(CameraDevice.TEMPLATE_STILL_CAPTURE);
        sb.addTarget(yuv.getSurface());
        s.capture(sb.build(), null, h);
        boolean ok = gotStill.await(15, TimeUnit.SECONDS);
        System.out.println(ok ? info.get() : "fotograf gelmedi");
        byte[] out = jpeg.get();
        if (out != null) {
            try (FileOutputStream f = new FileOutputStream("/data/local/tmp/gbos-shim.jpg")) { f.write(out); }
            System.out.println("yazildi /data/local/tmp/gbos-shim.jpg " + out.length);
        }
        s.close();
        d.close();
        System.exit(out != null && info.get() != null && !info.get().startsWith("HATA") ? 0 : 1);
    }
}
