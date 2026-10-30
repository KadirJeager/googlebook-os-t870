package com.gbos.terminal;

import android.app.Notification;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.ClipData;
import android.content.ClipboardManager;
import android.content.Intent;
import android.content.pm.ServiceInfo;
import android.os.Binder;
import android.os.IBinder;
import android.os.LocaleList;
import android.util.Log;

import com.termux.terminal.TerminalSession;
import com.termux.terminal.TerminalSessionClient;

import java.io.IOException;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;

/**
 * Owns the terminal sessions of every Terminal window, so they outlive activity restarts and
 * keep running while the window is in the background (foreground service while any is open).
 * Each session runs /system/bin/gbos-linux, which attaches to a login shell in the Linux
 * environment through gbos-linuxd.
 */
public final class SessionService extends Service {
    static final String ACTION_EXIT = "com.gbos.terminal.EXIT";
    static final String ACTION_END_LINUX = "com.gbos.terminal.END_LINUX";
    /** Runs an approved command of another app in the background window (extra: token). */
    static final String ACTION_RUN_DROID = "com.gbos.terminal.RUN_DROID";
    static final String EXTRA_TOKEN = "token";
    /**
     * Sessions of approved commands that run without a window (background=true requests).
     * A Terminal window opened from their notification takes them over.
     */
    static final String BACKGROUND_WINDOW = "background";
    private static final int FAILED_NOTIFICATION_BASE = 100;
    /** How often programs left running without a terminal are counted again. */
    private static final long BACKGROUND_CHECK_MS = 20_000;
    private static final String TAG = "Terminal";
    private static final int NOTIFICATION_ID = 1;
    private static final int TRANSCRIPT_ROWS = 10000;
    static final String CLIENT = "/system/bin/gbos-linux";

    /** Window (activity task) callbacks for its sessions; all on the main thread. */
    interface Listener {
        void onTextChanged(TerminalSession session);
        void onTitleChanged(TerminalSession session);
        void onSessionFinished(TerminalSession session);
        void onBell(TerminalSession session);
        void onColorsChanged(TerminalSession session);
        void onCursorBlink(boolean visible);
    }

    final class LocalBinder extends Binder {
        SessionService service() {
            return SessionService.this;
        }
    }

    private final LocalBinder binder = new LocalBinder();
    private final Map<String, List<TerminalSession>> windows = new LinkedHashMap<>();
    private final Map<TerminalSession, String> windowOf = new HashMap<>();
    private final Map<String, Listener> listeners = new HashMap<>();
    /** Which app asked for a session of an approved command (for its failure notification). */
    private final Map<TerminalSession, String> requestedBy = new HashMap<>();
    private int nextFailedNotification = FAILED_NOTIFICATION_BASE;
    private boolean foreground;
    /** Linux programs of this user still running with no terminal open (0: none or unknown). */
    private int backgroundPrograms;
    private final android.os.Handler main = new android.os.Handler(android.os.Looper.getMainLooper());
    private final java.util.concurrent.ExecutorService io = java.util.concurrent.Executors.newSingleThreadExecutor();
    private final Runnable backgroundCheck = this::checkBackgroundPrograms;

    @Override
    public IBinder onBind(Intent intent) {
        return binder;
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        if (intent != null && ACTION_END_LINUX.equals(intent.getAction())) {
            io.execute(() -> {
                try {
                    DaemonClient.request(DaemonClient.op("end_user"));
                } catch (IOException e) {
                    Log.w(TAG, "end_user: " + e);
                }
                main.post(this::checkBackgroundPrograms);
            });
            return START_NOT_STICKY;
        }
        if (intent != null && ACTION_RUN_DROID.equals(intent.getAction())) {
            DroidRequests.Approved approved = DroidRequests.take(intent.getStringExtra(EXTRA_TOKEN));
            if (approved != null) createDroidSession(BACKGROUND_WINDOW, approved);
            return START_NOT_STICKY;
        }
        if (intent != null && ACTION_EXIT.equals(intent.getAction())) {
            for (String w : new ArrayList<>(windows.keySet())) {
                Listener l = listeners.get(w);
                closeWindow(w);
                if (l instanceof TerminalActivity) ((TerminalActivity) l).finishAndRemoveTask();
            }
        }
        updateForeground();
        return START_NOT_STICKY;
    }

    List<TerminalSession> sessions(String window) {
        List<TerminalSession> list = windows.get(window);
        if (list == null) {
            list = new ArrayList<>();
            windows.put(window, list);
        }
        return list;
    }

    void setListener(String window, Listener listener) {
        if (listener == null) {
            listeners.remove(window);
            if (listeners.isEmpty() && sessionCount() == 0) checkBackgroundPrograms();
        } else {
            listeners.put(window, listener);
        }
    }

    TerminalSession createSession(String window) {
        List<String> env = new ArrayList<>();
        env.add("TERM=xterm-256color");
        env.add("COLORTERM=truecolor");
        env.add("TERM_PROGRAM=googlebook-terminal");
        env.add("LANG=" + posixLocale(LocaleList.getDefault().get(0)));
        TerminalSession session = new TerminalSession(CLIENT, getFilesDir().getAbsolutePath(),
                new String[] {"gbos-linux", "shell"}, env.toArray(new String[0]), TRANSCRIPT_ROWS,
                client);
        sessions(window).add(session);
        windowOf.put(session, window);
        updateForeground();
        return session;
    }

    /**
     * A session that runs an approved command of another app as the droid account (the user of
     * Android's own Linux terminal): `gbos-linux droid -- /bin/bash -l -c COMMAND`, a login
     * shell as in that terminal's own tab.
     */
    TerminalSession createDroidSession(String window, DroidRequests.Approved approved) {
        List<String> env = new ArrayList<>();
        env.add("TERM=xterm-256color");
        env.add("COLORTERM=truecolor");
        env.add("TERM_PROGRAM=googlebook-terminal");
        env.add("LANG=" + posixLocale(LocaleList.getDefault().get(0)));
        TerminalSession session = new TerminalSession(CLIENT, getFilesDir().getAbsolutePath(),
                new String[] {"gbos-linux", "droid", "--", "/bin/bash", "-l", "-c", approved.command},
                env.toArray(new String[0]), TRANSCRIPT_ROWS, client);
        // Without a view the terminal library has no size yet; start it as a standard 80x24
        // terminal (a window that takes it over resizes it).
        if (listeners.get(window) == null) session.updateSize(80, 24, 10, 20);
        sessions(window).add(session);
        windowOf.put(session, window);
        requestedBy.put(session, approved.callerLabel);
        updateForeground();
        VmStatus.publish(this);
        return session;
    }

    /** Moves the sessions of `from` (the background window) into `to`, a window just opened. */
    void adoptWindow(String from, String to) {
        List<TerminalSession> moving = windows.remove(from);
        if (moving == null || moving.isEmpty()) return;
        List<TerminalSession> target = sessions(to);
        for (TerminalSession s : moving) {
            target.add(s);
            windowOf.put(s, to);
        }
        NotificationManager nm = getSystemService(NotificationManager.class);
        for (int id = FAILED_NOTIFICATION_BASE; id < nextFailedNotification; id++) nm.cancel(id);
        updateForeground();
    }

    void removeSession(TerminalSession session) {
        session.finishIfRunning();
        String window = windowOf.remove(session);
        requestedBy.remove(session);
        if (window != null) {
            List<TerminalSession> list = windows.get(window);
            if (list != null) list.remove(session);
        }
        updateForeground();
    }

    void closeWindow(String window) {
        List<TerminalSession> list = windows.remove(window);
        listeners.remove(window);
        if (list != null) {
            for (TerminalSession s : list) {
                s.finishIfRunning();
                windowOf.remove(s);
                requestedBy.remove(s);
            }
        }
        updateForeground();
    }

    int sessionCount() {
        return windowOf.size();
    }

    /** Android "tr-TR" -> "tr_TR.UTF-8"; the service generates it in Linux when needed. */
    static String posixLocale(Locale locale) {
        String lang = locale.getLanguage();
        if (lang.isEmpty()) return "C.UTF-8";
        String country = locale.getCountry();
        return lang + (country.isEmpty() ? "" : "_" + country) + ".UTF-8";
    }

    private void updateForeground() {
        int count = sessionCount();
        if (count == 0) {
            // Programs started in a terminal may outlive it (servers, nohup jobs). They run with
            // this app's uid, and Android cuts the network of an app in the background: stay in
            // the foreground while any is running, as Android's own Linux terminal does with
            // its VM.
            checkBackgroundPrograms();
            return;
        }
        main.removeCallbacks(backgroundCheck);
        backgroundPrograms = 0;
        showForeground(buildNotification(count));
    }

    private void showForeground(Notification n) {
        if (!foreground) {
            // Started, not only bound: closing the last window (unbind) must not destroy the
            // service while it keeps Linux programs reachable.
            startService(new Intent(this, SessionService.class));
            startForeground(NOTIFICATION_ID, n, ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE);
            foreground = true;
        } else {
            getSystemService(NotificationManager.class).notify(NOTIFICATION_ID, n);
        }
    }

    private void checkBackgroundPrograms() {
        main.removeCallbacks(backgroundCheck);
        if (sessionCount() > 0) return;
        io.execute(() -> {
            int programs = 0;
            try {
                programs = DaemonClient.request(DaemonClient.op("status")).optInt("processes", 0);
            } catch (IOException e) {
                Log.w(TAG, "status: " + e);
            }
            int n = programs;
            Log.i(TAG, "Linux programs running without a terminal: " + n);
            main.post(() -> {
                if (sessionCount() > 0) return;
                backgroundPrograms = n;
                if (n > 0) {
                    try {
                        showForeground(buildBackgroundNotification(n));
                    } catch (IllegalStateException e) {
                        // Not in the foreground already and the app is in the background:
                        // Android does not let it start one now (it does once a window opens).
                        Log.w(TAG, "cannot keep Linux programs in the foreground: " + e);
                        if (listeners.isEmpty()) stopSelf();
                        return;
                    }
                    main.postDelayed(backgroundCheck, BACKGROUND_CHECK_MS);
                } else {
                    if (foreground) {
                        stopForeground(STOP_FOREGROUND_REMOVE);
                        foreground = false;
                    }
                    // An open window (setup screen, a window between tabs) keeps it bound;
                    // the service ends when nothing needs it.
                    if (listeners.isEmpty()) stopSelf();
                }
            });
        });
    }

    /**
     * An approved command that ran without a window ended: success leaves nothing behind, a
     * failure keeps its output and says so (Terminal shows it when the notification is tapped).
     */
    private void backgroundFinished(TerminalSession session) {
        int code = session.getExitStatus();
        String caller = requestedBy.get(session);
        if (code == 0) {
            removeSession(session);
            return;
        }
        Intent open = new Intent(this, TerminalActivity.class)
                .setAction(TerminalActivity.ACTION_SHOW_BACKGROUND)
                .setFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
        PendingIntent openPi = PendingIntent.getActivity(this, 3, open,
                PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT);
        Notification n = new Notification.Builder(this, TerminalApp.CHANNEL_REQUESTS)
                .setSmallIcon(R.drawable.ic_notification)
                .setContentTitle(getString(R.string.notification_request_failed_title,
                        caller == null ? getString(R.string.app_name) : caller))
                .setContentText(getString(R.string.notification_request_failed_text, code))
                .setContentIntent(openPi)
                .setAutoCancel(true)
                .setCategory(Notification.CATEGORY_ERROR)
                .build();
        getSystemService(NotificationManager.class).notify(nextFailedNotification++, n);
    }

    private Notification buildBackgroundNotification(int programs) {
        Intent open = new Intent(this, TerminalActivity.class)
                .setFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_RESET_TASK_IF_NEEDED);
        PendingIntent openPi = PendingIntent.getActivity(this, 0, open, PendingIntent.FLAG_IMMUTABLE);
        PendingIntent endPi = PendingIntent.getService(this, 2,
                new Intent(this, SessionService.class).setAction(ACTION_END_LINUX), PendingIntent.FLAG_IMMUTABLE);
        return new Notification.Builder(this, TerminalApp.CHANNEL_SESSIONS)
                .setSmallIcon(R.drawable.ic_notification)
                .setContentTitle(getString(R.string.notification_background_title))
                .setContentText(getResources().getQuantityString(R.plurals.notification_background, programs, programs))
                .setContentIntent(openPi)
                .setOngoing(true)
                .setShowWhen(false)
                .setCategory(Notification.CATEGORY_SERVICE)
                .addAction(new Notification.Action.Builder(null, getString(R.string.notification_end_linux), endPi).build())
                .build();
    }

    @Override
    public void onDestroy() {
        main.removeCallbacks(backgroundCheck);
        io.shutdownNow();
        super.onDestroy();
    }

    private Notification buildNotification(int count) {
        Intent open = new Intent(this, TerminalActivity.class)
                .setFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_RESET_TASK_IF_NEEDED);
        PendingIntent openPi = PendingIntent.getActivity(this, 0, open, PendingIntent.FLAG_IMMUTABLE);
        PendingIntent exitPi = PendingIntent.getService(this, 1,
                new Intent(this, SessionService.class).setAction(ACTION_EXIT), PendingIntent.FLAG_IMMUTABLE);
        return new Notification.Builder(this, TerminalApp.CHANNEL_SESSIONS)
                .setSmallIcon(R.drawable.ic_notification)
                .setContentTitle(getString(R.string.notification_title))
                .setContentText(getResources().getQuantityString(R.plurals.notification_sessions, count, count))
                .setContentIntent(openPi)
                .setOngoing(true)
                .setShowWhen(false)
                .setCategory(Notification.CATEGORY_SERVICE)
                .addAction(new Notification.Action.Builder(null, getString(R.string.notification_exit), exitPi).build())
                .build();
    }

    private Listener listenerFor(TerminalSession s) {
        String w = windowOf.get(s);
        return w == null ? null : listeners.get(w);
    }

    private final TerminalSessionClient client = new TerminalSessionClient() {
        @Override
        public void onTextChanged(TerminalSession changedSession) {
            Listener l = listenerFor(changedSession);
            if (l != null) l.onTextChanged(changedSession);
        }

        @Override
        public void onTitleChanged(TerminalSession changedSession) {
            Listener l = listenerFor(changedSession);
            if (l != null) l.onTitleChanged(changedSession);
        }

        @Override
        public void onSessionFinished(TerminalSession finishedSession) {
            Listener l = listenerFor(finishedSession);
            if (l != null) l.onSessionFinished(finishedSession);
            else if (BACKGROUND_WINDOW.equals(windowOf.get(finishedSession))) backgroundFinished(finishedSession);
            VmStatus.publish(SessionService.this);
        }

        @Override
        public void onCopyTextToClipboard(TerminalSession session, String text) {
            if (text == null || text.isEmpty()) return;
            ClipboardManager cm = getSystemService(ClipboardManager.class);
            cm.setPrimaryClip(ClipData.newPlainText(getString(R.string.app_name), text));
        }

        @Override
        public void onPasteTextFromClipboard(TerminalSession session) {
            if (session == null) return;
            ClipboardManager cm = getSystemService(ClipboardManager.class);
            ClipData clip = cm.getPrimaryClip();
            if (clip == null || clip.getItemCount() == 0) return;
            CharSequence text = clip.getItemAt(0).coerceToText(SessionService.this);
            if (text != null && text.length() > 0 && session.getEmulator() != null) {
                session.getEmulator().paste(text.toString());
            }
        }

        @Override
        public void onBell(TerminalSession session) {
            Listener l = listenerFor(session);
            if (l != null) l.onBell(session);
        }

        @Override
        public void onColorsChanged(TerminalSession session) {
            Listener l = listenerFor(session);
            if (l != null) l.onColorsChanged(session);
        }

        @Override
        public void onTerminalCursorStateChange(boolean state) {
            for (Listener l : listeners.values()) l.onCursorBlink(state);
        }

        @Override
        public void setTerminalShellPid(TerminalSession session, int pid) {
        }

        @Override
        public Integer getTerminalCursorStyle() {
            return null;
        }

        @Override
        public void logError(String tag, String message) {
            Log.e(TAG, tag + ": " + message);
        }

        @Override
        public void logWarn(String tag, String message) {
            Log.w(TAG, tag + ": " + message);
        }

        @Override
        public void logInfo(String tag, String message) {
            Log.i(TAG, tag + ": " + message);
        }

        @Override
        public void logDebug(String tag, String message) {
        }

        @Override
        public void logVerbose(String tag, String message) {
        }

        @Override
        public void logStackTraceWithMessage(String tag, String message, Exception e) {
            Log.e(TAG, tag + ": " + message, e);
        }

        @Override
        public void logStackTrace(String tag, Exception e) {
            Log.e(TAG, tag, e);
        }
    };
}
