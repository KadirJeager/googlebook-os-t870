package com.gbos.guncelleyici;

import android.app.job.JobInfo;
import android.app.job.JobParameters;
import android.app.job.JobScheduler;
import android.app.job.JobService;
import android.content.ComponentName;
import android.content.Context;
import android.util.Log;

import java.io.IOException;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;

/**
 * The updater's background work, as JobScheduler jobs:
 *   DENETIM  once a day: is there a newer mica release, or a new preparation kit? (any network)
 *   HAZIRLA  a newer release, or the running one again for a new kit (from the computer, or the
 *            bridge image's first boot): let the tablet prepare it, on an unmetered network while
 *            charging
 *   IZLE     every 15 min while something runs: notices "ready", "updated", "rolled back", errors
 *   KUR      a ready update: install while charging and idle (overnight), like Pixel updates
 */
public final class Planlayici extends JobService {
    static final String ETIKET = "gbos-guncelleyici";
    static final int DENETIM = 1, HAZIRLA = 2, IZLE = 3, KUR = 4;

    private final ExecutorService is = Executors.newSingleThreadExecutor();

    static void kur(Context c) {
        JobScheduler js = c.getSystemService(JobScheduler.class);
        if (js.getPendingJob(DENETIM) == null) {
            js.schedule(new JobInfo.Builder(DENETIM, bilesen(c))
                    .setPeriodic(TimeUnit.DAYS.toMillis(1), TimeUnit.HOURS.toMillis(6))
                    .setRequiredNetworkType(JobInfo.NETWORK_TYPE_ANY)
                    .setPersisted(true)
                    .build());
        }
    }

    static void hazirlamaPlanla(Context c) {
        c.getSystemService(JobScheduler.class).schedule(new JobInfo.Builder(HAZIRLA, bilesen(c))
                .setRequiredNetworkType(JobInfo.NETWORK_TYPE_UNMETERED)
                .setRequiresCharging(true)
                .setRequiresBatteryNotLow(true)
                .setPersisted(true)
                .build());
    }

    static void izlemePlanla(Context c) {
        JobScheduler js = c.getSystemService(JobScheduler.class);
        if (js.getPendingJob(IZLE) != null) return;
        js.schedule(new JobInfo.Builder(IZLE, bilesen(c))
                .setPeriodic(TimeUnit.MINUTES.toMillis(15))
                .setPersisted(true)
                .build());
    }

    static void kurulumPlanla(Context c) {
        c.getSystemService(JobScheduler.class).schedule(new JobInfo.Builder(KUR, bilesen(c))
                .setRequiresCharging(true)
                .setRequiresDeviceIdle(true)
                .setPersisted(true)
                .build());
    }

    static void iptal(Context c, int id) {
        c.getSystemService(JobScheduler.class).cancel(id);
    }

    private static ComponentName bilesen(Context c) {
        return new ComponentName(c, Planlayici.class);
    }

    @Override
    public boolean onStartJob(JobParameters p) {
        is.execute(() -> {
            boolean tekrar = false;
            try {
                switch (p.getJobId()) {
                    case DENETIM -> denetim();
                    case HAZIRLA -> hazirla();
                    case IZLE -> izle();
                    case KUR -> tekrar = !kurulum();
                    default -> { }
                }
            } catch (IOException e) {
                Log.w(ETIKET, "iş " + p.getJobId() + ": " + e.getMessage());
                tekrar = p.getJobId() != IZLE;
            }
            jobFinished(p, tekrar);
        });
        return true;
    }

    @Override
    public boolean onStopJob(JobParameters p) {
        return true;
    }

    private void denetim() throws IOException {
        Durum d = Servis.durum();
        if (d.kitIcinHazirlanmali()) hazirlamaPlanla(this);
        Surum s = Surum.denetle(this);
        if (!s.yeni()) return;
        if (s.build.equals(d.hazir)) {
            kurulumPlanla(this);
            return;
        }
        if (d.suruyor()) return;
        Bildirim.yeniSurum(this, s);
        hazirlamaPlanla(this);
    }

    private void hazirla() throws IOException {
        Durum d = Servis.durum();
        if (d.suruyor() || d.hazir != null) return;
        Servis.baslat("hazirla");
        izlemePlanla(this);
    }

    /** Notifies what changed; stops itself when nothing runs any more. */
    private void izle() throws IOException {
        Durum d = Servis.durum();
        Bildirim.durumaGore(this, d);
        if (d.durum.equals("hazir")) kurulumPlanla(this);
        // a trial that just ended (the bridge image, or a kit set): the kit may still differ
        if (d.kitIcinHazirlanmali()) hazirlamaPlanla(this);
        if (!d.suruyor()) iptal(this, IZLE);
    }

    /** true when the install was started (the tablet then reboots). */
    private boolean kurulum() throws IOException {
        Durum d = Servis.durum();
        if (d.hazir == null) return true;
        if (!Pil.kurulumaYeter(this)) return false;
        Servis.baslat("kur");
        return true;
    }
}
