package com.gbos.guncelleyici;

import android.app.Activity;
import android.app.AlertDialog;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.text.format.DateUtils;
import android.view.LayoutInflater;
import android.view.View;
import android.widget.Button;
import android.widget.LinearLayout;
import android.widget.ProgressBar;
import android.widget.TextView;
import android.widget.Toast;

import java.io.IOException;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/** Settings › System › System update. Follows the service's state while it is visible. */
public final class GuncellemeActivity extends Activity {
    private static final long YOKLAMA_MS = 2000;

    private final ExecutorService is = Executors.newSingleThreadExecutor();
    private final Handler ana = new Handler(Looper.getMainLooper());
    private TextView baslik, metin, adim;
    private ProgressBar ilerleme;
    private Button birincil, ikincil;
    private LinearLayout ayrintilar;
    private Durum sonDurum;
    private String servisHatasi;
    private boolean denetleniyor, gorunur;

    private final Runnable yokla = new Runnable() {
        @Override
        public void run() {
            if (!gorunur) return;
            is.execute(() -> {
                Durum d = null;
                String hata = null;
                try {
                    d = Servis.durum();
                } catch (IOException e) {
                    hata = e.getMessage();
                }
                Durum fd = d;
                String fh = hata;
                ana.post(() -> {
                    sonDurum = fd;
                    servisHatasi = fh;
                    goster();
                });
            });
            ana.postDelayed(this, YOKLAMA_MS);
        }
    };

    @Override
    protected void onCreate(Bundle b) {
        super.onCreate(b);
        setContentView(R.layout.guncelleme);
        baslik = findViewById(R.id.baslik);
        metin = findViewById(R.id.metin);
        adim = findViewById(R.id.adim);
        ilerleme = findViewById(R.id.ilerleme);
        birincil = findViewById(R.id.birincil);
        ikincil = findViewById(R.id.ikincil);
        ayrintilar = findViewById(R.id.ayrintilar);
        if (getActionBar() != null) getActionBar().setDisplayHomeAsUpEnabled(true);
        Planlayici.kur(this);
    }

    @Override
    public boolean onNavigateUp() {
        finish();
        return true;
    }

    @Override
    protected void onResume() {
        super.onResume();
        gorunur = true;
        ana.post(yokla);
    }

    @Override
    protected void onPause() {
        gorunur = false;
        ana.removeCallbacks(yokla);
        super.onPause();
    }

    @Override
    protected void onDestroy() {
        is.shutdownNow();
        super.onDestroy();
    }

    private void goster() {
        Surum s = Surum.son(this);
        Durum d = sonDurum;
        ilerleme.setVisibility(View.GONE);
        adim.setVisibility(View.GONE);
        ikincil.setVisibility(View.GONE);
        birincil.setEnabled(!denetleniyor);
        if (d == null) {
            yaz(R.string.hata_baslik, getString(R.string.servis_yok) + "\n" + nullsuz(servisHatasi));
            dugme(birincil, R.string.yeniden_dene, v -> ana.post(yokla));
            ayrintilariYaz(null, s);
            return;
        }
        switch (d.durum) {
            case "hazirlaniyor" -> {
                yaz(R.string.hazirlaniyor_baslik, getString(R.string.hazirlaniyor_metin));
                ilerlemeGoster(d.motorIlerleme(), d.motorMesaj().isEmpty() ? d.mesaj : d.motorMesaj());
                dugme(birincil, R.string.durdur, v -> baslat("iptal"));
            }
            case "duraklatildi" -> {
                yaz(R.string.duraklatildi_baslik, getString(R.string.duraklatildi_metin));
                dugme(birincil, R.string.devam, v -> baslat("hazirla"));
            }
            case "kuruluyor", "geri-donuluyor" -> {
                yaz(d.durum.equals("kuruluyor") ? R.string.kuruluyor_baslik : R.string.geri_donuluyor_baslik, d.mesaj);
                ilerlemeGoster(d.ilerleme, null);
                birincil.setVisibility(View.GONE);
            }
            case "deneme" -> {
                yaz(R.string.deneme_baslik, getString(R.string.deneme_metin));
                ilerlemeGoster(-1, null);
                birincil.setVisibility(View.GONE);
            }
            case "geri-donuldu" -> {
                yaz(R.string.geri_baslik, getString(R.string.geri_metin));
                dugme(birincil, R.string.denetle, v -> denetle());
            }
            case "hata" -> {
                yaz(R.string.hata_baslik, d.mesaj);
                dugme(birincil, R.string.yeniden_dene, v -> {
                    if (d.hazir != null) kurOnayi(d.hazir);
                    else denetle();
                });
            }
            default -> {
                if (d.hazir != null) {
                    yaz(R.string.hazir_baslik, getString(R.string.hazir_metin, d.hazir));
                    dugme(birincil, R.string.simdi_yeniden_baslat, v -> kurOnayi(d.hazir));
                } else if (s != null && s.yeni()) {
                    yaz(R.string.yeni_baslik, getString(R.string.yeni_metin, Surum.artimli(s.build), s.tarih));
                    dugme(birincil, R.string.hazirla, v -> baslat("hazirla"));
                    dugme(ikincil, R.string.denetle, v -> denetle());
                } else if (d.durum.equals("guncellendi")) {
                    yaz(R.string.guncellendi_baslik, getString(R.string.guncellendi_metin));
                    dugme(birincil, R.string.denetle, v -> denetle());
                } else {
                    yaz(R.string.guncel_baslik, getString(R.string.guncel_metin));
                    dugme(birincil, R.string.denetle, v -> denetle());
                }
            }
        }
        if (denetleniyor) birincil.setText(R.string.denetleniyor);
        ayrintilariYaz(d, s);
    }

    private void yaz(int baslikId, String govde) {
        baslik.setText(baslikId);
        metin.setText(govde);
    }

    private void ilerlemeGoster(int yuzde, String aciklama) {
        ilerleme.setVisibility(View.VISIBLE);
        ilerleme.setIndeterminate(yuzde < 0);
        if (yuzde >= 0) ilerleme.setProgress(yuzde);
        if (aciklama != null && !aciklama.isEmpty()) {
            adim.setText(aciklama);
            adim.setVisibility(View.VISIBLE);
        }
    }

    private void dugme(Button b, int metinId, View.OnClickListener tik) {
        b.setVisibility(View.VISIBLE);
        b.setText(metinId);
        b.setOnClickListener(tik);
    }

    private void ayrintilariYaz(Durum d, Surum s) {
        ayrintilar.removeAllViews();
        satir(R.string.ayrinti_surum, Build.VERSION.INCREMENTAL + " (" + Build.ID + ")");
        satir(R.string.ayrinti_son_denetim, s == null ? getString(R.string.hic)
                : DateUtils.getRelativeTimeSpanString(s.denetim).toString());
        if (s != null) satir(R.string.ayrinti_sunucu, Surum.artimli(s.build) + " · " + s.tarih);
        if (d != null && d.onceki != null) satir(R.string.ayrinti_onceki, d.onceki);
    }

    private void satir(int ad, String deger) {
        View v = LayoutInflater.from(this).inflate(R.layout.ayrinti, ayrintilar, false);
        ((TextView) v.findViewById(R.id.ad)).setText(ad);
        ((TextView) v.findViewById(R.id.deger)).setText(deger);
        ayrintilar.addView(v);
    }

    private void denetle() {
        denetleniyor = true;
        goster();
        is.execute(() -> {
            String hata = null;
            try {
                Surum s = Surum.denetle(this);
                if (s.yeni()) Planlayici.hazirlamaPlanla(this);
            } catch (IOException e) {
                hata = e.getMessage();
            }
            String fh = hata;
            ana.post(() -> {
                denetleniyor = false;
                if (fh != null) Toast.makeText(this, getString(R.string.denetlenemedi, fh), Toast.LENGTH_LONG).show();
                goster();
            });
        });
    }

    private void baslat(String islem) {
        is.execute(() -> {
            String hata = null;
            try {
                Servis.baslat(islem);
                if (islem.equals("hazirla")) Planlayici.izlemePlanla(this);
            } catch (IOException e) {
                hata = e.getMessage();
            }
            String fh = hata;
            ana.post(() -> {
                if (fh != null) Toast.makeText(this, fh, Toast.LENGTH_LONG).show();
                ana.post(yokla);
            });
        });
    }

    private void kurOnayi(String surum) {
        if (!Pil.kurulumaYeter(this)) {
            Toast.makeText(this, getString(R.string.pil_yetersiz, Pil.EN_AZ, Pil.EN_AZ_SARJDA),
                    Toast.LENGTH_LONG).show();
            return;
        }
        new AlertDialog.Builder(this)
                .setTitle(R.string.onay_baslik)
                .setMessage(getString(R.string.onay_metin, surum))
                .setNegativeButton(R.string.iptal, null)
                .setPositiveButton(R.string.simdi_yeniden_baslat, (dlg, w) -> baslat("kur"))
                .show();
    }

    private static String nullsuz(String s) {
        return s == null ? "" : s;
    }
}
