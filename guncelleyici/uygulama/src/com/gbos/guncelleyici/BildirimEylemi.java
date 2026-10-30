package com.gbos.guncelleyici;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.pm.PackageInstaller;
import android.util.Log;
import android.widget.Toast;

import java.io.IOException;

/** "Restart now" on the ready-update notification, and the results of PaketTemizleyici. */
public final class BildirimEylemi extends BroadcastReceiver {
    static final String KUR = "com.gbos.guncelleyici.KUR";

    @Override
    public void onReceive(Context c, Intent intent) {
        if (PaketTemizleyici.SONUC.equals(intent.getAction())) {
            int durum = intent.getIntExtra(PackageInstaller.EXTRA_STATUS, PackageInstaller.STATUS_FAILURE);
            Log.i(Planlayici.ETIKET, "kalıntı " + intent.getStringExtra("paket") + ": "
                    + (durum == PackageInstaller.STATUS_SUCCESS ? "kaldırıldı"
                    : "kaldırılamadı (" + durum + " " + intent.getStringExtra(PackageInstaller.EXTRA_STATUS_MESSAGE) + ")"));
            return;
        }
        if (!KUR.equals(intent.getAction())) return;
        if (!Pil.kurulumaYeter(c)) {
            Toast.makeText(c, c.getString(R.string.pil_yetersiz, Pil.EN_AZ, Pil.EN_AZ_SARJDA),
                    Toast.LENGTH_LONG).show();
            return;
        }
        PendingResult sonuc = goAsync();
        new Thread(() -> {
            try {
                Servis.baslat("kur");
                Bildirim.kaldir(c);
            } catch (IOException e) {
                Log.w(Planlayici.ETIKET, "kur: " + e.getMessage());
            } finally {
                sonuc.finish();
            }
        }).start();
    }
}
