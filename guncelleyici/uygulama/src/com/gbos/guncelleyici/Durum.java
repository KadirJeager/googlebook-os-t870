package com.gbos.guncelleyici;

import org.json.JSONException;
import org.json.JSONObject;

import java.util.HashMap;
import java.util.Map;

/**
 * The updater's state as gbos-guncelleyici-is durum prints it: key=value lines (durum, mesaj,
 * ilerleme, zaman, calisan, kit_farkli, hazir, onceki, son_kurulum, deneme) and, while the tablet
 * prepares a release, the motor's own state as one JSON line (motor=...).
 */
final class Durum {
    final String durum;        // guncel, hazirlaniyor, hazir, kuruluyor, deneme, guncellendi,
                               // geri-donuluyor, geri-donuldu, duraklatildi, hata, bos
    final String mesaj;
    final int ilerleme;        // -1 when unknown
    final long zaman;          // seconds, when the state was written
    final String calisan;      // ro.build.version.incremental
    final boolean kitFarkli;   // the tablet has a preparation kit the running image was not made with
    final String hazir;        // build of the ready set, or null
    final String onceki;       // build kept for a rollback, or null
    final String sonKurulum;   // recovery's last result line, or null
    final JSONObject motor;    // null when the tablet has not prepared anything

    private Durum(Map<String, String> m) {
        durum = m.getOrDefault("durum", "bos");
        mesaj = m.getOrDefault("mesaj", "");
        ilerleme = sayi(m.get("ilerleme"), -1);
        zaman = sayi(m.get("zaman"), 0);
        calisan = m.get("calisan");
        kitFarkli = "1".equals(m.get("kit_farkli"));
        hazir = bosIseNull(m.get("hazir"));
        onceki = bosIseNull(m.get("onceki"));
        sonKurulum = bosIseNull(m.get("son_kurulum"));
        JSONObject j = null;
        String motorSatiri = m.get("motor");
        if (motorSatiri != null) {
            try {
                j = new JSONObject(motorSatiri);
            } catch (JSONException e) {
                j = null;
            }
        }
        motor = j;
    }

    static Durum ayristir(String metin) {
        Map<String, String> m = new HashMap<>();
        for (String satir : metin.split("\n")) {
            int i = satir.indexOf('=');
            if (i > 0) m.put(satir.substring(0, i), satir.substring(i + 1));
        }
        return new Durum(m);
    }

    /** The running release is to be prepared again for a new kit (and nothing else is under way). */
    boolean kitIcinHazirlanmali() {
        return kitFarkli && hazir == null && !suruyor();
    }

    boolean suruyor() {
        return durum.equals("hazirlaniyor") || durum.equals("kuruluyor")
                || durum.equals("geri-donuluyor") || durum.equals("deneme");
    }

    /** Progress of the motor's step while preparing, -1 when unknown. */
    int motorIlerleme() {
        if (motor == null || motor.isNull("ilerleme")) return -1;
        return motor.optInt("ilerleme", -1);
    }

    String motorMesaj() {
        return motor == null ? "" : motor.optString("mesaj", "");
    }

    private static int sayi(String s, int varsayilan) {
        try {
            return s == null || s.isEmpty() ? varsayilan : Integer.parseInt(s.trim());
        } catch (NumberFormatException e) {
            return varsayilan;
        }
    }

    private static long sayi(String s, long varsayilan) {
        try {
            return s == null || s.isEmpty() ? varsayilan : Long.parseLong(s.trim());
        } catch (NumberFormatException e) {
            return varsayilan;
        }
    }

    private static String bosIseNull(String s) {
        return s == null || s.isEmpty() ? null : s;
    }
}
