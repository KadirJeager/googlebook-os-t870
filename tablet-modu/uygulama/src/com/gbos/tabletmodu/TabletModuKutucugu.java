package com.gbos.tabletmodu;

import android.service.quicksettings.Tile;
import android.service.quicksettings.TileService;
import android.util.Log;

import java.io.IOException;

/** Quick settings "Tablet modu": on, the cover keyboard and touchpad are off and mica is a slate. */
public final class TabletModuKutucugu extends TileService {
    private static final String TAG = "GbosTabletModu";

    @Override
    public void onStartListening() {
        guncelle();
    }

    @Override
    public void onClick() {
        try {
            boolean acik = !TabletModu.acikMi();
            TabletModu.ayarla(acik);
            TabletModu.tercihiKaydet(this, acik);
        } catch (IOException e) {
            Log.e(TAG, "tablet_mode yazılamadı", e);
        }
        guncelle();
    }

    private void guncelle() {
        Tile tile = getQsTile();
        if (tile == null) {
            return;
        }
        int durum;
        int altBaslik;
        if (!TabletModu.varMi()) {
            durum = Tile.STATE_UNAVAILABLE;
            altBaslik = R.string.kutucuk_yok;
        } else {
            boolean acik;
            try {
                acik = TabletModu.acikMi();
            } catch (IOException e) {
                Log.e(TAG, "tablet_mode okunamadı", e);
                acik = false;
            }
            durum = acik ? Tile.STATE_ACTIVE : Tile.STATE_INACTIVE;
            altBaslik = acik ? R.string.kutucuk_acik : R.string.kutucuk_kapali;
        }
        tile.setState(durum);
        tile.setSubtitle(getString(altBaslik));
        tile.updateTile();
    }
}
