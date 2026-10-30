package com.gbos.guncelleyici;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;

/** The updater's notifications, one at a time (same id), each state change notified once. */
final class Bildirim {
    private static final String KANAL = "sistem_guncellemesi";
    private static final int KIMLIK = 1;
    private static final String TERCIHLER = "bildirim";

    private Bildirim() {}

    static void yeniSurum(Context c, Surum s) {
        if (!ilkKez(c, "yeni:" + s.build)) return;
        goster(c, c.getString(R.string.bildirim_yeni_baslik),
                c.getString(R.string.bildirim_yeni_metin, Surum.artimli(s.build), s.tarih), null);
    }

    /** Notifies a state the user should know about, once per state change. */
    static void durumaGore(Context c, Durum d) {
        if (!ilkKez(c, d.durum + ":" + d.zaman)) return;
        switch (d.durum) {
            case "hazir" -> goster(c, c.getString(R.string.bildirim_hazir_baslik),
                    c.getString(R.string.bildirim_hazir_metin), kurEylemi(c));
            case "guncellendi" -> goster(c, c.getString(R.string.bildirim_guncellendi_baslik),
                    c.getString(R.string.bildirim_guncellendi_metin), null);
            case "geri-donuldu" -> goster(c, c.getString(R.string.bildirim_geri_baslik),
                    c.getString(R.string.bildirim_geri_metin), null);
            case "hata" -> goster(c, c.getString(R.string.bildirim_hata_baslik), d.mesaj, null);
            default -> { }
        }
    }

    private static Notification.Action kurEylemi(Context c) {
        Intent i = new Intent(c, BildirimEylemi.class).setAction(BildirimEylemi.KUR);
        PendingIntent pi = PendingIntent.getBroadcast(c, 0, i,
                PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT);
        return new Notification.Action.Builder(null, c.getString(R.string.simdi_yeniden_baslat), pi)
                .build();
    }

    private static void goster(Context c, String baslik, String metin, Notification.Action eylem) {
        NotificationManager nm = c.getSystemService(NotificationManager.class);
        nm.createNotificationChannel(new NotificationChannel(KANAL,
                c.getString(R.string.kanal_adi), NotificationManager.IMPORTANCE_DEFAULT));
        PendingIntent ac = PendingIntent.getActivity(c, 0,
                new Intent(c, GuncellemeActivity.class).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK),
                PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT);
        Notification.Builder b = new Notification.Builder(c, KANAL)
                .setSmallIcon(R.drawable.ic_bildirim)
                .setContentTitle(baslik)
                .setContentText(metin)
                .setStyle(new Notification.BigTextStyle().bigText(metin))
                .setContentIntent(ac)
                .setAutoCancel(true);
        if (eylem != null) b.addAction(eylem);
        nm.notify(KIMLIK, b.build());
    }

    static void kaldir(Context c) {
        c.getSystemService(NotificationManager.class).cancel(KIMLIK);
    }

    private static boolean ilkKez(Context c, String anahtar) {
        SharedPreferences p = c.getSharedPreferences(TERCIHLER, Context.MODE_PRIVATE);
        if (anahtar.equals(p.getString("son", null))) return false;
        p.edit().putString("son", anahtar).apply();
        return true;
    }
}
