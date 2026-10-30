package com.gbos.terminal;

import android.app.Activity;
import android.app.Application;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.os.Bundle;

public final class TerminalApp extends Application {
    static final String CHANNEL_SESSIONS = "sessions";
    static final String CHANNEL_REQUESTS = "requests";

    /** Activities of this app between onStart and onStop (Android's "foreground" for the app). */
    private int started;

    @Override
    public void onCreate() {
        super.onCreate();
        NotificationChannel channel = new NotificationChannel(CHANNEL_SESSIONS,
                getString(R.string.notification_channel), NotificationManager.IMPORTANCE_LOW);
        channel.setShowBadge(false);
        NotificationChannel requests = new NotificationChannel(CHANNEL_REQUESTS,
                getString(R.string.notification_channel_requests), NotificationManager.IMPORTANCE_DEFAULT);
        getSystemService(NotificationManager.class).createNotificationChannels(
                java.util.Arrays.asList(channel, requests));
        registerActivityLifecycleCallbacks(new ActivityLifecycleCallbacks() {
            @Override
            public void onActivityStarted(Activity activity) {
                if (started++ == 0) VmStatus.publish(TerminalApp.this);
            }

            @Override
            public void onActivityStopped(Activity activity) {
                if (--started == 0) VmStatus.publish(TerminalApp.this);
            }

            @Override
            public void onActivityCreated(Activity activity, Bundle state) {
            }

            @Override
            public void onActivityResumed(Activity activity) {
            }

            @Override
            public void onActivityPaused(Activity activity) {
            }

            @Override
            public void onActivitySaveInstanceState(Activity activity, Bundle state) {
            }

            @Override
            public void onActivityDestroyed(Activity activity) {
            }
        });
    }

    /** Whether a window of this app is visible (main thread). */
    boolean inForeground() {
        return started > 0;
    }
}
