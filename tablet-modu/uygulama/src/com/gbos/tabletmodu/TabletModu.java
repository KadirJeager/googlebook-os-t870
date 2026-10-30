package com.gbos.tabletmodu;

import android.content.ComponentName;
import android.content.Context;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;

import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.IOException;
import java.nio.charset.StandardCharsets;

/**
 * The kernel switch and the user's choice.
 *
 * The switch is the sec_keypad tablet_mode attribute (kernel patch 0002): 1 passes an open cover
 * on as folded back, 0 follows the keyboard MCU. It resets to 0 on every boot; the choice is kept
 * in this user's device-protected preferences and written back by AcilisAlicisi.
 */
final class TabletModu {
    static final String DUGUM = "/sys/devices/virtual/sec/sec_keypad/tablet_mode";
    private static final String TERCIHLER = "tablet_modu";
    private static final String ACIK = "acik";

    private TabletModu() {}

    /** The running kernel has the switch and init made it writable (gbos-tablet-modu.rc). */
    static boolean varMi() {
        File f = new File(DUGUM);
        return f.isFile() && f.canWrite();
    }

    static boolean acikMi() throws IOException {
        try (FileInputStream in = new FileInputStream(DUGUM)) {
            return in.read() == '1';
        }
    }

    static void ayarla(boolean acik) throws IOException {
        try (FileOutputStream out = new FileOutputStream(DUGUM)) {
            out.write((acik ? "1\n" : "0\n").getBytes(StandardCharsets.US_ASCII));
        }
    }

    static boolean tercih(Context context) {
        return tercihler(context).getBoolean(ACIK, false);
    }

    static void tercihiKaydet(Context context, boolean acik) {
        tercihler(context).edit().putBoolean(ACIK, acik).apply();
    }

    /** Shows the tile only where the switch exists. Per user, like the component state itself. */
    static void kutucuguGoster(Context context, boolean goster) {
        context.getPackageManager().setComponentEnabledSetting(
                new ComponentName(context, TabletModuKutucugu.class),
                goster ? PackageManager.COMPONENT_ENABLED_STATE_ENABLED
                        : PackageManager.COMPONENT_ENABLED_STATE_DISABLED,
                PackageManager.DONT_KILL_APP);
    }

    private static SharedPreferences tercihler(Context context) {
        return context.createDeviceProtectedStorageContext()
                .getSharedPreferences(TERCIHLER, Context.MODE_PRIVATE);
    }
}
