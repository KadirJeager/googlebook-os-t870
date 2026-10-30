package com.gbos.tabletmodu;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.util.Log;

import java.io.IOException;

/**
 * Each user's start (before unlock) and after an update: show the tile only if the kernel has the
 * switch, and write the user's choice back, since the switch is 0 after every boot.
 */
public final class AcilisAlicisi extends BroadcastReceiver {
    private static final String TAG = "GbosTabletModu";

    @Override
    public void onReceive(Context context, Intent intent) {
        boolean var = TabletModu.varMi();
        TabletModu.kutucuguGoster(context, var);
        if (var && TabletModu.tercih(context)) {
            try {
                TabletModu.ayarla(true);
            } catch (IOException e) {
                Log.e(TAG, "tablet_mode geri yazılamadı", e);
            }
        }
    }
}
