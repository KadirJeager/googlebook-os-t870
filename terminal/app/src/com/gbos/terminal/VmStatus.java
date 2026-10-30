package com.gbos.terminal;

import android.content.Context;
import android.net.Uri;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.util.Log;

import org.json.JSONObject;

import java.io.IOException;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/**
 * The Linux environment's state in the terms of Android's own terminal
 * (com.android.virtualization.terminal TerminalStatusProvider.getVmStatus): is_installed,
 * is_running, is_installing, is_foreground. Apps written for that terminal (Antigravity) read it
 * through the compatibility package's provider (terminal/kopru) and watch
 * content://com.android.virtualization.terminal.provider/vm_status for changes; this app sends
 * those change notifications, since only it sees the state change.
 */
final class VmStatus {
    static final Uri AOSP_STATUS_URI = Uri.parse("content://com.android.virtualization.terminal.provider/vm_status");
    private static final String TAG = "Terminal";
    private static final ExecutorService IO = Executors.newSingleThreadExecutor();
    private static final Handler MAIN = new Handler(Looper.getMainLooper());
    /** Last state announced (in this process); null before the first. */
    private static Bundle last;

    final boolean installed, running, installing, foreground;

    private VmStatus(boolean installed, boolean running, boolean installing, boolean foreground) {
        this.installed = installed;
        this.running = running;
        this.installing = installing;
        this.foreground = foreground;
    }

    /** Asks gbos-linuxd (blocking: a binder or worker thread, never the main thread). */
    static VmStatus query(boolean foreground) throws IOException {
        JSONObject s = DaemonClient.request(DaemonClient.op("status"));
        if (!s.optBoolean("ok", false)) throw new IOException(s.optString("error", "status failed"));
        return new VmStatus(s.optBoolean("installed"), s.optBoolean("running"),
                "installing".equals(s.optString("state")), foreground);
    }

    Bundle toBundle() {
        Bundle b = new Bundle();
        b.putBoolean("is_installed", installed);
        b.putBoolean("is_running", running);
        b.putBoolean("is_installing", installing);
        b.putBoolean("is_foreground", foreground);
        return b;
    }

    /** Whether a Terminal window is visible, from any thread. */
    static boolean foreground(Context context) {
        TerminalApp app = (TerminalApp) context.getApplicationContext();
        if (Looper.myLooper() == Looper.getMainLooper()) return app.inForeground();
        final boolean[] out = new boolean[1];
        final java.util.concurrent.CountDownLatch done = new java.util.concurrent.CountDownLatch(1);
        MAIN.post(() -> {
            out[0] = app.inForeground();
            done.countDown();
        });
        try {
            done.await(2, java.util.concurrent.TimeUnit.SECONDS);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
        }
        return out[0];
    }

    /**
     * Reads the state again and, when it differs from what was last announced, notifies the
     * observers of the vm_status URI. Called wherever this app changes or learns of a change
     * (sessions, installation, shutdown, windows shown or hidden).
     */
    static void publish(Context context) {
        Context app = context.getApplicationContext();
        boolean fg = ((TerminalApp) app).inForeground();
        IO.execute(() -> {
            VmStatus s;
            try {
                s = query(fg);
            } catch (IOException e) {
                Log.w(TAG, "status for vm_status: " + e.getMessage());
                return;
            }
            Bundle b = s.toBundle();
            synchronized (VmStatus.class) {
                if (last != null && same(last, b)) return;
                last = b;
            }
            try {
                app.getContentResolver().notifyChange(AOSP_STATUS_URI, null);
            } catch (RuntimeException e) {
                // The compatibility package is not installed (or this app lacks MANAGE_TERMINAL).
                Log.w(TAG, "vm_status notification: " + e);
            }
        });
    }

    private static boolean same(Bundle a, Bundle b) {
        for (String k : new String[] {"is_installed", "is_running", "is_installing", "is_foreground"}) {
            if (a.getBoolean(k) != b.getBoolean(k)) return false;
        }
        return true;
    }
}
