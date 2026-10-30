package com.gbos.guncelleyici;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.util.Log;

import java.io.IOException;

/**
 * After every boot (and after the app itself was replaced): keeps the daily check scheduled,
 * tells the user what the boot guard decided (updated, rolled back, failed), keeps watching while
 * something runs, prepares the running release again when the tablet has a new kit, and removes
 * the leftovers of apps the new build no longer ships.
 */
public final class AcilisAlicisi extends BroadcastReceiver {
    @Override
    public void onReceive(Context c, Intent intent) {
        String a = intent.getAction();
        if (!Intent.ACTION_BOOT_COMPLETED.equals(a) && !Intent.ACTION_MY_PACKAGE_REPLACED.equals(a)) return;
        Planlayici.kur(c);
        PendingResult sonuc = goAsync();
        new Thread(() -> {
            try {
                PaketTemizleyici.calistir(c);
                Durum d = Servis.durum();
                Bildirim.durumaGore(c, d);
                if (d.suruyor()) Planlayici.izlemePlanla(c);
                if (d.durum.equals("hazir")) Planlayici.kurulumPlanla(c);
                if (d.kitIcinHazirlanmali()) Planlayici.hazirlamaPlanla(c);
            } catch (IOException e) {
                Log.w(Planlayici.ETIKET, "açılış: " + e.getMessage());
            } finally {
                sonuc.finish();
            }
        }).start();
    }
}
