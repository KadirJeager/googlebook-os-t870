package com.gbos.terminal;

import android.app.Activity;
import android.os.UserManager;
import android.text.Editable;
import android.text.TextWatcher;
import android.text.format.Formatter;
import android.text.method.ScrollingMovementMethod;
import android.view.KeyEvent;
import android.view.View;
import android.view.inputmethod.EditorInfo;
import android.widget.Button;
import android.widget.EditText;
import android.widget.ProgressBar;
import android.widget.TextView;

import org.json.JSONObject;

import java.io.FileInputStream;
import java.io.IOException;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.text.Normalizer;
import java.util.ArrayDeque;
import java.util.Locale;
import java.util.regex.Pattern;

/** The first-run screen: choose a user name, then follow the installation. */
final class SetupScreen {
    enum Mode { INSTALL, USER }

    interface Actions {
        void onInstall(String userName);
        void onCancel();
        void onRetry();
    }

    static final Pattern USER_NAME = Pattern.compile("^[a-z_][a-z0-9_-]{0,31}$");
    private static final int LOG_LINES = 400;

    private final Activity activity;
    private final View root;
    private final TextView title, body, phase, detail, error, detailsToggle, log, usernameError;
    private final EditText username;
    private final View form, progressBox;
    private final ProgressBar progress;
    private final Button primary, secondary;
    private final Actions actions;
    private final ArrayDeque<String> logLines = new ArrayDeque<>();
    private Mode mode = Mode.INSTALL;
    private boolean running;

    SetupScreen(Activity activity, View root, Actions actions) {
        this.activity = activity;
        this.root = root;
        this.actions = actions;
        title = root.findViewById(R.id.setup_title);
        body = root.findViewById(R.id.setup_body);
        form = root.findViewById(R.id.form);
        username = root.findViewById(R.id.username);
        usernameError = root.findViewById(R.id.username_error);
        progressBox = root.findViewById(R.id.progress_box);
        phase = root.findViewById(R.id.phase);
        progress = root.findViewById(R.id.progress);
        detail = root.findViewById(R.id.detail);
        error = root.findViewById(R.id.error);
        detailsToggle = root.findViewById(R.id.details_toggle);
        log = root.findViewById(R.id.log);
        primary = root.findViewById(R.id.primary_button);
        secondary = root.findViewById(R.id.secondary_button);
        log.setMovementMethod(new ScrollingMovementMethod());
        // A readable column in wide desktop windows: at most 600dp, centered.
        View content = root.findViewById(R.id.setup_content);
        int maxWidth = (int) (600 * activity.getResources().getDisplayMetrics().density);
        root.addOnLayoutChangeListener((v, l, t, r, b, ol, ot, or, ob) -> {
            int available = ((View) content.getParent()).getWidth()
                    - ((View) content.getParent()).getPaddingLeft() - ((View) content.getParent()).getPaddingRight();
            int width = Math.min(available, maxWidth);
            android.view.ViewGroup.LayoutParams lp = content.getLayoutParams();
            if (available > 0 && lp.width != width) {
                lp.width = width;
                content.setLayoutParams(lp);
            }
        });
        detailsToggle.setOnClickListener(v -> log.setVisibility(
                log.getVisibility() == View.VISIBLE ? View.GONE : View.VISIBLE));
        primary.setOnClickListener(v -> onPrimary());
        secondary.setOnClickListener(v -> actions.onCancel());
        username.setOnEditorActionListener((v, id, e) -> {
            if (id == EditorInfo.IME_ACTION_DONE
                    || (e != null && e.getKeyCode() == KeyEvent.KEYCODE_ENTER && e.getAction() == KeyEvent.ACTION_DOWN)) {
                onPrimary();
                return true;
            }
            return false;
        });
        username.addTextChangedListener(new TextWatcher() {
            @Override public void beforeTextChanged(CharSequence s, int a, int b, int c) {}
            @Override public void onTextChanged(CharSequence s, int a, int b, int c) {}
            @Override public void afterTextChanged(Editable s) {
                usernameError.setVisibility(View.GONE);
            }
        });
    }

    View view() {
        return root;
    }

    boolean isRunning() {
        return running;
    }

    void showForm(Mode mode) {
        this.mode = mode;
        running = false;
        title.setText(mode == Mode.INSTALL ? R.string.setup_title : R.string.setup_user_title);
        body.setText(mode == Mode.INSTALL
                ? activity.getString(R.string.setup_body, debianRelease())
                : activity.getString(R.string.setup_user_body));
        form.setVisibility(View.VISIBLE);
        username.setEnabled(true);
        if (username.getText().length() == 0) username.setText(suggestedUserName());
        username.setSelection(username.getText().length());
        progressBox.setVisibility(View.GONE);
        error.setVisibility(View.GONE);
        detailsToggle.setVisibility(logLines.isEmpty() ? View.GONE : View.VISIBLE);
        secondary.setVisibility(View.GONE);
        primary.setVisibility(View.VISIBLE);
        primary.setEnabled(true);
        primary.setText(mode == Mode.INSTALL ? R.string.setup_install : R.string.setup_create_user);
        username.requestFocus();
    }

    /** The installation is running (also when attaching to one started earlier). */
    void showRunning() {
        running = true;
        title.setText(R.string.setup_title);
        body.setText(activity.getString(R.string.setup_body, debianRelease()));
        form.setVisibility(View.VISIBLE);
        username.setEnabled(false);
        progressBox.setVisibility(View.VISIBLE);
        error.setVisibility(View.GONE);
        detailsToggle.setVisibility(View.VISIBLE);
        primary.setVisibility(View.GONE);
        secondary.setVisibility(View.VISIBLE);
        secondary.setText(R.string.setup_cancel);
        secondary.setEnabled(true);
        secondary.setOnClickListener(v -> {
            secondary.setEnabled(false);
            actions.onCancel();
        });
    }

    void onProgress(JSONObject ev) {
        if (!running) showRunning();
        String ph = ev.optString("phase");
        int text;
        switch (ph) {
            case "resolve": text = R.string.phase_resolve; break;
            case "download": text = R.string.phase_download; break;
            case "verify": text = R.string.phase_verify; break;
            case "extract": text = R.string.phase_extract; break;
            case "configure": text = R.string.phase_configure; break;
            case "user": text = R.string.phase_user; break;
            default: text = R.string.phase_done;
        }
        phase.setText(text);
        progress.setProgress((int) Math.round(ev.optDouble("percent", 0) * 10));
        if (ev.has("bytes") && ev.has("total")) {
            detail.setText(activity.getString(R.string.progress_bytes,
                    Formatter.formatShortFileSize(activity, ev.optLong("bytes")),
                    Formatter.formatShortFileSize(activity, ev.optLong("total"))));
        } else {
            detail.setText(ev.optString("detail", ""));
        }
    }

    void onLog(String line) {
        logLines.addLast(line);
        while (logLines.size() > LOG_LINES) logLines.removeFirst();
        log.setText(String.join("\n", logLines));
    }

    void showError(String code, String message) {
        running = false;
        progressBox.setVisibility(View.GONE);
        form.setVisibility(View.VISIBLE);
        username.setEnabled(true);
        String text;
        switch (code == null ? "" : code) {
            case "network": text = activity.getString(R.string.error_network); break;
            case "disk": text = activity.getString(R.string.error_disk); break;
            case "cancelled": text = activity.getString(R.string.setup_cancelled); break;
            case "service": text = activity.getString(R.string.error_service); break;
            default: text = activity.getString(R.string.error_generic, message == null ? code : message);
        }
        title.setText(R.string.setup_failed);
        error.setText(text);
        error.setVisibility(View.VISIBLE);
        detailsToggle.setVisibility(logLines.isEmpty() ? View.GONE : View.VISIBLE);
        secondary.setVisibility(View.GONE);
        primary.setVisibility(View.VISIBLE);
        primary.setEnabled(true);
        primary.setText(R.string.setup_retry);
        primary.setOnClickListener(v -> {
            primary.setOnClickListener(x -> onPrimary());
            if ("service".equals(code)) actions.onRetry();
            else onPrimary();
        });
    }

    void clearLog() {
        logLines.clear();
        log.setText("");
    }

    private void onPrimary() {
        String name = username.getText().toString().trim();
        if (!USER_NAME.matcher(name).matches()) {
            usernameError.setVisibility(View.VISIBLE);
            username.requestFocus();
            return;
        }
        primary.setEnabled(false);
        actions.onInstall(name);
    }

    /** The Android user's name as a Linux user name: "Jülide Kaya" -> "julide". */
    private String suggestedUserName() {
        String name = null;
        try {
            name = activity.getSystemService(UserManager.class).getUserName();
        } catch (SecurityException ignored) {
        }
        return toUserName(name);
    }

    static String toUserName(String name) {
        if (name == null) return "user";
        String first = name.trim().split("\\s+")[0].replace('ı', 'i').replace('İ', 'i');
        String ascii = Normalizer.normalize(first, Normalizer.Form.NFD).replaceAll("\\p{M}", "");
        StringBuilder out = new StringBuilder();
        for (char c : ascii.toLowerCase(Locale.ROOT).toCharArray()) {
            if ((c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') || c == '_' || c == '-') out.append(c);
        }
        while (out.length() > 0 && !(out.charAt(0) >= 'a' && out.charAt(0) <= 'z')) out.deleteCharAt(0);
        if (out.length() > 32) out.setLength(32);
        return out.length() == 0 ? "user" : out.toString();
    }

    /** The Debian release named in the image's linux.conf ("trixie"). */
    private static String debianRelease() {
        try (InputStream in = new FileInputStream("/system/etc/gbos-linux/linux.conf")) {
            for (String line : new String(in.readAllBytes(), StandardCharsets.UTF_8).split("\n")) {
                line = line.trim();
                if (line.startsWith("tag=")) return "“" + line.substring(4).trim() + "”";
            }
        } catch (IOException ignored) {
        }
        return "";
    }
}
