package com.gbos.guncelleyici;

import android.content.Context;
import android.os.BatteryManager;

/** The battery an install needs: 40 %, or 25 % while charging (recovery itself wants 20/15). */
final class Pil {
    static final int EN_AZ = 40;
    static final int EN_AZ_SARJDA = 25;

    private Pil() {}

    static boolean kurulumaYeter(Context c) {
        BatteryManager bm = c.getSystemService(BatteryManager.class);
        int duzey = bm.getIntProperty(BatteryManager.BATTERY_PROPERTY_CAPACITY);
        return duzey >= (bm.isCharging() ? EN_AZ_SARJDA : EN_AZ);
    }
}
