package com.gbos.guncelleyici;

import android.content.Context;
import android.content.SharedPreferences;
import android.os.Build;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.URI;
import java.nio.charset.StandardCharsets;

import javax.net.ssl.HttpsURLConnection;

/**
 * The newest Googlebook (mica) release, from the same official listing the image pipeline's
 * mica-indir.py reads: Google's recovery image listing, entries with device "mica" and channel
 * "STABLE", the highest buildidentifier ("<incremental>_<build id>").
 */
final class Surum {
    static final String LISTE = "https://dl.google.com/dl/edgedl/device/recovery/production_recovery.json";
    private static final String TERCIHLER = "surum";
    private static final int ZAMAN_ASIMI_MS = 30_000;
    private static final int EN_FAZLA_BAYT = 4 << 20;

    final String build;     // 16560795_CL3B.260622.276.R2
    final String tarih;     // dateupdated, 2026-10-22
    final long denetim;     // when it was read (ms)

    private Surum(String build, String tarih, long denetim) {
        this.build = build;
        this.tarih = tarih;
        this.denetim = denetim;
    }

    static long artimli(String build) {
        int i = build.indexOf('_');
        try {
            return Long.parseLong(i > 0 ? build.substring(0, i) : build);
        } catch (NumberFormatException e) {
            return -1;
        }
    }

    /** The running build's incremental number (product build.prop). */
    static long calisan() {
        try {
            return Long.parseLong(Build.VERSION.INCREMENTAL);
        } catch (NumberFormatException e) {
            return -1;
        }
    }

    boolean yeni() {
        long a = artimli(build);
        return a > 0 && a > calisan();
    }

    /** Reads the listing (network; never on the main thread) and remembers the result. */
    static Surum denetle(Context c) throws IOException {
        HttpsURLConnection con = (HttpsURLConnection) URI.create(LISTE).toURL().openConnection();
        con.setConnectTimeout(ZAMAN_ASIMI_MS);
        con.setReadTimeout(ZAMAN_ASIMI_MS);
        con.setUseCaches(false);
        try {
            if (con.getResponseCode() != HttpURLConnection.HTTP_OK) {
                throw new IOException("HTTP " + con.getResponseCode());
            }
            String govde;
            try (InputStream in = con.getInputStream()) {
                ByteArrayOutputStream buf = new ByteArrayOutputStream();
                byte[] b = new byte[16384];
                int n;
                while ((n = in.read(b)) > 0) {
                    buf.write(b, 0, n);
                    if (buf.size() > EN_FAZLA_BAYT) throw new IOException("liste beklenenden büyük");
                }
                govde = buf.toString(StandardCharsets.UTF_8);
            }
            Surum s = ayikla(govde, System.currentTimeMillis());
            tercihler(c).edit().putString("build", s.build).putString("tarih", s.tarih)
                    .putLong("denetim", s.denetim).apply();
            return s;
        } finally {
            con.disconnect();
        }
    }

    static Surum ayikla(String json, long zaman) throws IOException {
        try {
            JSONArray dizi = new JSONArray(json);
            String enIyi = null, tarih = "";
            for (int i = 0; i < dizi.length(); i++) {
                JSONObject o = dizi.getJSONObject(i);
                if (!"mica".equals(o.optString("device")) || !"STABLE".equals(o.optString("channel"))) {
                    continue;
                }
                String b = o.optString("buildidentifier");
                if (artimli(b) <= 0) continue;
                if (enIyi == null || artimli(b) > artimli(enIyi)) {
                    enIyi = b;
                    tarih = o.optString("dateupdated");
                }
            }
            if (enIyi == null) throw new IOException("listede mica STABLE sürümü yok");
            return new Surum(enIyi, tarih, zaman);
        } catch (JSONException e) {
            throw new IOException("liste okunamadı: " + e.getMessage(), e);
        }
    }

    /** The last result read, or null before the first check. */
    static Surum son(Context c) {
        SharedPreferences p = tercihler(c);
        String b = p.getString("build", null);
        return b == null ? null : new Surum(b, p.getString("tarih", ""), p.getLong("denetim", 0));
    }

    private static SharedPreferences tercihler(Context c) {
        return c.getSharedPreferences(TERCIHLER, Context.MODE_PRIVATE);
    }
}
