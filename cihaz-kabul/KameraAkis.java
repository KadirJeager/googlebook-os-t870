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
import android.media.Image;
import android.media.ImageReader;
import android.media.MediaCodec;
import android.media.MediaCodecInfo;
import android.media.MediaFormat;
import android.os.Handler;
import android.os.HandlerThread;
import android.os.Looper;
import android.view.Surface;

import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.Executor;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;

/**
 * Opens one camera with the stream set the Googlebook camera app uses for video (preview,
 * hardware H.264 encoder input surface, optional JPEG still) and reports, per stream, how many
 * frames arrive in a few seconds of a TEMPLATE_RECORD repeating request.
 *
 *   app_process /system/bin KameraAkis <cameraId> <videoW>x<videoH> <jpeg:0|1> [seconds]
 *
 * A video size of 0x0 leaves the encoder out (preview, and the still when asked for, only).
 * The third argument is 0 (no still), 1 (JPEG at the video size) or <jpeg|yuv><W>x<H>.
 *
 * One line: "kamera=0 video=1920x1080 jpeg=1 onizleme=N video=N foto=N" and exit 0 when the
 * preview and the encoder both got frames (and the still capture, when asked for), 1 otherwise.
 */
public class KameraAkis {
    static Context systemContext() throws Exception {
        Class<?> at = Class.forName("android.app.ActivityThread");
        Object thread = at.getMethod("systemMain").invoke(null);
        return (Context) at.getMethod("getSystemContext").invoke(thread);
    }

    public static void main(String[] args) throws Exception {
        Looper.prepareMainLooper();
        String id = args[0];
        String[] wh = args[1].split("x");
        int vw = Integer.parseInt(wh[0]), vh = Integer.parseInt(wh[1]);
        boolean jpeg = !args[2].equals("0");
        int stillFormat = args[2].startsWith("yuv") ? ImageFormat.YUV_420_888 : ImageFormat.JPEG;
        String stillSize = args[2].replaceFirst("^(jpeg|yuv)", "");
        int seconds = args.length > 3 ? Integer.parseInt(args[3]) : 4;

        HandlerThread ht = new HandlerThread("kamera");
        ht.start();
        Handler h = new Handler(ht.getLooper());
        Executor ex = h::post;
        CameraManager cm = systemContext().getSystemService(CameraManager.class);
        CameraCharacteristics cc = cm.getCameraCharacteristics(id);
        int facing = cc.get(CameraCharacteristics.LENS_FACING);

        // Preview: a PRIVATE-format reader with GPU usage, the buffer kind a TextureView or
        // SurfaceView consumer gets (no GL context is needed to count and release frames).
        AtomicInteger preview = new AtomicInteger(), video = new AtomicInteger(), still = new AtomicInteger();
        ImageReader previewReader = ImageReader.newInstance(1920, 1080, ImageFormat.PRIVATE, 4,
                HardwareBuffer.USAGE_GPU_SAMPLED_IMAGE);
        previewReader.setOnImageAvailableListener(r -> {
            Image im = r.acquireNextImage();
            if (im != null) { preview.incrementAndGet(); im.close(); }
        }, h);
        Surface previewSurface = previewReader.getSurface();

        boolean withVideo = vw > 0;
        // Video: the same hardware encoder and input surface path CameraX's VideoCapture uses.
        MediaCodec enc = null;
        Surface videoSurface = null;
        if (withVideo) {
        MediaFormat f = MediaFormat.createVideoFormat(MediaFormat.MIMETYPE_VIDEO_AVC, vw, vh);
        f.setInteger(MediaFormat.KEY_COLOR_FORMAT, MediaCodecInfo.CodecCapabilities.COLOR_FormatSurface);
        f.setInteger(MediaFormat.KEY_BIT_RATE, vw * vh * 4);
        f.setInteger(MediaFormat.KEY_FRAME_RATE, 30);
        f.setInteger(MediaFormat.KEY_I_FRAME_INTERVAL, 1);
        enc = MediaCodec.createEncoderByType(MediaFormat.MIMETYPE_VIDEO_AVC);
        enc.setCallback(new MediaCodec.Callback() {
            public void onInputBufferAvailable(MediaCodec c, int i) {}
            public void onOutputBufferAvailable(MediaCodec c, int i, MediaCodec.BufferInfo bi) {
                if ((bi.flags & MediaCodec.BUFFER_FLAG_CODEC_CONFIG) == 0) video.incrementAndGet();
                c.releaseOutputBuffer(i, false);
            }
            public void onError(MediaCodec c, MediaCodec.CodecException e) {
                System.out.println("kodlayici-hatasi " + e.getDiagnosticInfo());
            }
            public void onOutputFormatChanged(MediaCodec c, MediaFormat mf) {}
        }, h);
        enc.configure(f, null, null, MediaCodec.CONFIGURE_FLAG_ENCODE);
        videoSurface = enc.createInputSurface();
        enc.start();
        }

        ImageReader jpegReader = null;
        List<OutputConfiguration> outs = new ArrayList<>();
        outs.add(new OutputConfiguration(previewSurface));
        if (withVideo) outs.add(new OutputConfiguration(videoSurface));
        if (jpeg) {
            int sw = withVideo ? vw : 1920, sh = withVideo ? vh : 1080;
            if (stillSize.contains("x")) {
                sw = Integer.parseInt(stillSize.split("x")[0]);
                sh = Integer.parseInt(stillSize.split("x")[1]);
            }
            jpegReader = ImageReader.newInstance(sw, sh, stillFormat, 4);
            jpegReader.setOnImageAvailableListener(r -> {
                Image im = r.acquireNextImage();
                if (im != null) { still.incrementAndGet(); im.close(); }
            }, h);
            outs.add(new OutputConfiguration(jpegReader.getSurface()));
        }

        CountDownLatch opened = new CountDownLatch(1);
        CameraDevice[] dev = new CameraDevice[1];
        String[] err = new String[1];
        cm.openCamera(id, ex, new CameraDevice.StateCallback() {
            public void onOpened(CameraDevice d) { dev[0] = d; opened.countDown(); }
            public void onDisconnected(CameraDevice d) { err[0] = "baglanti-koptu"; opened.countDown(); }
            public void onError(CameraDevice d, int e) { err[0] = "acma-hatasi-" + e; opened.countDown(); }
        });
        if (!opened.await(10, TimeUnit.SECONDS) || dev[0] == null) {
            System.out.println("kamera=" + id + " " + (err[0] == null ? "acilmadi" : err[0]));
            System.exit(2);
        }

        CountDownLatch configured = new CountDownLatch(1);
        CameraCaptureSession[] session = new CameraCaptureSession[1];
        dev[0].createCaptureSession(new SessionConfiguration(SessionConfiguration.SESSION_REGULAR, outs, ex,
                new CameraCaptureSession.StateCallback() {
                    public void onConfigured(CameraCaptureSession s) { session[0] = s; configured.countDown(); }
                    public void onConfigureFailed(CameraCaptureSession s) { configured.countDown(); }
                }));
        if (!configured.await(10, TimeUnit.SECONDS) || session[0] == null) {
            System.out.println("kamera=" + id + " oturum-kurulamadi");
            dev[0].close();
            System.exit(3);
        }

        CaptureRequest.Builder rb = dev[0].createCaptureRequest(CameraDevice.TEMPLATE_RECORD);
        rb.addTarget(previewSurface);
        if (withVideo) rb.addTarget(videoSurface);
        session[0].setRepeatingRequest(rb.build(), null, h);
        Thread.sleep(seconds * 1000L / 2);
        if (jpeg) {
            CaptureRequest.Builder sb = dev[0].createCaptureRequest(CameraDevice.TEMPLATE_VIDEO_SNAPSHOT);
            sb.addTarget(previewSurface);
            if (withVideo) sb.addTarget(videoSurface);
            sb.addTarget(jpegReader.getSurface());
            session[0].capture(sb.build(), null, h);
        }
        Thread.sleep(seconds * 1000L / 2);

        int p = preview.get(), v = video.get(), s = still.get();
        System.out.println("kamera=" + id + (facing == CameraCharacteristics.LENS_FACING_BACK ? "(arka)" : "(on)")
                + " video=" + vw + "x" + vh + " foto-akisi=" + args[2]
                + " onizleme=" + p + " video=" + v + (jpeg ? " foto=" + s : ""));
        try { session[0].abortCaptures(); } catch (Exception ignored) {}
        dev[0].close();
        if (withVideo) { enc.stop(); enc.release(); }
        System.exit(p > 0 && (!withVideo || v > 0) && (!jpeg || s > 0) ? 0 : 1);
    }
}
