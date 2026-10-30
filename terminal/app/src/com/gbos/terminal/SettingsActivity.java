package com.gbos.terminal;

import android.app.Activity;
import android.app.AlertDialog;
import android.app.UiModeManager;
import android.content.Intent;
import android.graphics.Insets;
import android.net.Uri;
import android.os.Bundle;
import android.os.Environment;
import android.os.Handler;
import android.os.Looper;
import android.provider.Settings;
import android.util.TypedValue;
import android.view.View;
import android.view.WindowInsets;
import android.widget.RadioGroup;
import android.widget.SeekBar;
import android.widget.TextView;
import android.widget.Toast;

import org.json.JSONObject;

import java.io.IOException;
import java.util.Locale;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/** Text size, theme and the Linux environment itself (files access, shut down, remove). */
public final class SettingsActivity extends Activity {
    private final Handler main = new Handler(Looper.getMainLooper());
    private final ExecutorService io = Executors.newSingleThreadExecutor();
    private Prefs prefs;
    private TextView fontValue, fontPreview, filesState, version;

    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);
        prefs = new Prefs(this);
        getWindow().setDecorFitsSystemWindows(false);
        setContentView(R.layout.settings);
        View root = findViewById(R.id.settings_root);
        root.setOnApplyWindowInsetsListener((v, insets) -> {
            Insets bars = insets.getInsets(WindowInsets.Type.systemBars() | WindowInsets.Type.displayCutout());
            v.setPadding(bars.left, bars.top, bars.right, bars.bottom);
            return WindowInsets.CONSUMED;
        });

        fontValue = findViewById(R.id.font_value);
        fontPreview = findViewById(R.id.font_preview);
        SeekBar font = findViewById(R.id.font_size);
        font.setMax((int) (Prefs.MAX_FONT_SP - Prefs.MIN_FONT_SP));
        font.setProgress((int) (prefs.fontSizeSp() - Prefs.MIN_FONT_SP));
        showFont(prefs.fontSizeSp());
        font.setOnSeekBarChangeListener(new SeekBar.OnSeekBarChangeListener() {
            @Override
            public void onProgressChanged(SeekBar s, int progress, boolean fromUser) {
                float sp = Prefs.MIN_FONT_SP + progress;
                showFont(sp);
                if (fromUser) prefs.setFontSizeSp(sp);
            }

            @Override public void onStartTrackingTouch(SeekBar s) {}
            @Override public void onStopTrackingTouch(SeekBar s) {}
        });

        RadioGroup theme = findViewById(R.id.theme);
        int mode = prefs.nightMode();
        theme.check(mode == UiModeManager.MODE_NIGHT_NO ? R.id.theme_light
                : mode == UiModeManager.MODE_NIGHT_YES ? R.id.theme_dark : R.id.theme_system);
        theme.setOnCheckedChangeListener((g, id) -> prefs.setNightMode(
                id == R.id.theme_light ? UiModeManager.MODE_NIGHT_NO
                        : id == R.id.theme_dark ? UiModeManager.MODE_NIGHT_YES : UiModeManager.MODE_NIGHT_AUTO));

        version = findViewById(R.id.version);
        filesState = findViewById(R.id.files_state);
        findViewById(R.id.files).setOnClickListener(v -> startActivity(
                new Intent(Settings.ACTION_MANAGE_APP_ALL_FILES_ACCESS_PERMISSION, Uri.parse("package:" + getPackageName()))));
        findViewById(R.id.shutdown).setOnClickListener(v -> linuxOp("stop", R.string.settings_shut_down));
        findViewById(R.id.remove).setOnClickListener(v -> new AlertDialog.Builder(this)
                .setMessage(R.string.settings_remove_confirm)
                .setPositiveButton(R.string.settings_remove_action, (d, w) -> linuxOp("remove", R.string.settings_removed))
                .setNegativeButton(R.string.cancel, null)
                .show());
    }

    @Override
    protected void onResume() {
        super.onResume();
        filesState.setText(Environment.isExternalStorageManager() ? R.string.settings_files_on : R.string.settings_files_off);
        io.execute(() -> {
            try {
                JSONObject s = DaemonClient.request(DaemonClient.op("status"));
                String v = s.optString("debian_version");
                main.post(() -> version.setText(v.isEmpty() ? "" : getString(R.string.settings_version, v)));
            } catch (IOException e) {
                main.post(() -> version.setText(R.string.error_service));
            }
        });
    }

    @Override
    protected void onDestroy() {
        io.shutdownNow();
        super.onDestroy();
    }

    private void showFont(float sp) {
        fontValue.setText(String.format(Locale.getDefault(), "%.0f sp", sp));
        fontPreview.setTextSize(TypedValue.COMPLEX_UNIT_SP, sp);
    }

    private void linuxOp(String op, int doneText) {
        io.execute(() -> {
            String error = null;
            try {
                JSONObject r = DaemonClient.request(DaemonClient.op(op));
                if (!r.optBoolean("ok")) error = r.optString("error");
            } catch (IOException e) {
                error = e.getMessage();
            }
            String err = error;
            VmStatus.publish(this);
            main.post(() -> {
                Toast.makeText(this, err == null ? getString(doneText) : getString(R.string.error_generic, err),
                        Toast.LENGTH_LONG).show();
                if (err == null && "remove".equals(op)) {
                    // Every window shows the setup screen again from scratch.
                    startService(new Intent(this, SessionService.class).setAction(SessionService.ACTION_EXIT));
                    finish();
                }
            });
        });
    }
}
