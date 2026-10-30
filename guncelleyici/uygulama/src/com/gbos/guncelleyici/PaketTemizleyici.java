package com.gbos.guncelleyici;

import android.app.PendingIntent;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.ApplicationInfo;
import android.content.pm.PackageManager;
import android.os.Build;
import android.util.Log;

import java.io.BufferedReader;
import java.io.FileReader;
import java.io.IOException;
import java.util.ArrayList;
import java.util.List;

/**
 * Apps the image removed on purpose (apply_patches.REMOVED_APPS; the package names are listed in
 * /system/etc/gbos-guncelleyici/kaldirilan-paketler by yamalar/guncelleyici.py) can stay behind
 * as ordinary apps: when a system app has a Play update in /data and the system copy disappears,
 * Android keeps the update (Dell SupportAssist did, 23 Oct 2026). After each new build this
 * removes such leftovers in the user it runs for, once: an app the user installs again later on
 * the same build stays. System copies are never touched.
 */
final class PaketTemizleyici {
    static final String LISTE = "/system/etc/gbos-guncelleyici/kaldirilan-paketler";
    static final String SONUC = "com.gbos.guncelleyici.TEMIZLIK_SONUCU";
    private static final String TERCIHLER = "temizlik";

    private PaketTemizleyici() {}

    static void calistir(Context c) {
        String surum = Build.VERSION.INCREMENTAL + "_" + Build.ID;
        SharedPreferences p = c.getSharedPreferences(TERCIHLER, Context.MODE_PRIVATE);
        if (surum.equals(p.getString("surum", null))) return;
        List<String> paketler;
        try {
            paketler = oku();
        } catch (IOException e) {
            Log.w(Planlayici.ETIKET, LISTE + ": " + e.getMessage());
            return;
        }
        PackageManager pm = c.getPackageManager();
        for (String paket : paketler) {
            ApplicationInfo ai;
            try {
                ai = pm.getApplicationInfo(paket, 0);
            } catch (PackageManager.NameNotFoundException e) {
                continue;
            }
            if ((ai.flags & ApplicationInfo.FLAG_SYSTEM) != 0) continue;
            Log.i(Planlayici.ETIKET, "imajdan çıkarılmış uygulamanın kalıntısı kaldırılıyor: " + paket);
            PendingIntent pi = PendingIntent.getBroadcast(c, paket.hashCode(),
                    new Intent(c, BildirimEylemi.class).setAction(SONUC).putExtra("paket", paket),
                    PendingIntent.FLAG_MUTABLE | PendingIntent.FLAG_UPDATE_CURRENT);
            pm.getPackageInstaller().uninstall(paket, pi.getIntentSender());
        }
        p.edit().putString("surum", surum).apply();
    }

    static List<String> oku() throws IOException {
        List<String> out = new ArrayList<>();
        try (BufferedReader r = new BufferedReader(new FileReader(LISTE))) {
            String line;
            while ((line = r.readLine()) != null) {
                line = line.trim();
                if (!line.isEmpty() && !line.startsWith("#")) out.add(line);
            }
        }
        return out;
    }
}
