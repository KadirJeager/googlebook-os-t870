package com.gbos.terminal;

import android.app.UiModeManager;
import android.content.Context;
import android.content.SharedPreferences;

/** User settings of the Terminal. The theme is applied through Android's per-app night mode. */
final class Prefs {
    static final float MIN_FONT_SP = 8, MAX_FONT_SP = 32, DEFAULT_FONT_SP = 14;

    private final SharedPreferences prefs;
    private final Context context;

    Prefs(Context context) {
        this.context = context.getApplicationContext();
        this.prefs = this.context.getSharedPreferences("terminal", Context.MODE_PRIVATE);
    }

    float fontSizeSp() {
        return clampFont(prefs.getFloat("font_sp", DEFAULT_FONT_SP));
    }

    void setFontSizeSp(float sp) {
        prefs.edit().putFloat("font_sp", clampFont(sp)).apply();
    }

    static float clampFont(float sp) {
        return Math.max(MIN_FONT_SP, Math.min(MAX_FONT_SP, sp));
    }

    /** UiModeManager.MODE_NIGHT_AUTO (follow the system), MODE_NIGHT_NO or MODE_NIGHT_YES. */
    int nightMode() {
        return prefs.getInt("night_mode", UiModeManager.MODE_NIGHT_AUTO);
    }

    /** Stored here (Android keeps the per-app mode but offers no getter) and applied app-wide. */
    void setNightMode(int mode) {
        prefs.edit().putInt("night_mode", mode).apply();
        context.getSystemService(UiModeManager.class).setApplicationNightMode(mode);
    }

    void registerListener(SharedPreferences.OnSharedPreferenceChangeListener l) {
        prefs.registerOnSharedPreferenceChangeListener(l);
    }

    void unregisterListener(SharedPreferences.OnSharedPreferenceChangeListener l) {
        prefs.unregisterOnSharedPreferenceChangeListener(l);
    }
}
