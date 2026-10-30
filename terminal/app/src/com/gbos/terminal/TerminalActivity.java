package com.gbos.terminal;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.ClipData;
import android.content.ClipboardManager;
import android.content.ComponentName;
import android.content.Context;
import android.content.Intent;
import android.content.ServiceConnection;
import android.content.SharedPreferences;
import android.content.res.Configuration;
import android.graphics.Insets;
import android.graphics.Typeface;
import android.net.Uri;
import android.os.Bundle;
import android.os.Environment;
import android.os.Handler;
import android.os.IBinder;
import android.os.Looper;
import android.os.LocaleList;
import android.provider.Settings;
import android.util.TypedValue;
import android.view.ContextMenu;
import android.view.Gravity;
import android.view.InputDevice;
import android.view.KeyEvent;
import android.view.Menu;
import android.view.MenuItem;
import android.view.MotionEvent;
import android.view.View;
import android.view.ViewGroup;
import android.view.WindowInsets;
import android.view.inputmethod.InputMethodManager;
import android.widget.HorizontalScrollView;
import android.widget.ImageButton;
import android.widget.LinearLayout;
import android.widget.PopupMenu;
import android.widget.ProgressBar;
import android.widget.TextView;
import android.widget.Toast;

import com.termux.terminal.TerminalColors;
import com.termux.terminal.TerminalSession;
import com.termux.view.MouseSelection;
import com.termux.view.TerminalView;
import com.termux.view.TerminalViewClient;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

import java.io.IOException;
import java.util.List;
import java.util.Properties;
import java.util.UUID;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/**
 * A Terminal window: tabs of Linux shells. On first use it shows the setup screen instead.
 * Several windows can be open (desktop "New window"); each owns its tabs.
 */
public final class TerminalActivity extends Activity implements SessionService.Listener {
    /** Shows the sessions of approved commands that ran without a window (their notification). */
    static final String ACTION_SHOW_BACKGROUND = "com.gbos.terminal.SHOW_BACKGROUND";
    /** One-time token of a command the user approved for another app (DroidRequests). */
    static final String EXTRA_DROID_TOKEN = "com.gbos.terminal.DROID_TOKEN";
    private static final String STATE_WINDOW = "window";
    private static final String STATE_CURRENT = "current";
    private static final int EXIT_NOT_INSTALLED = 69;
    private static final int EXIT_TOOL_ERROR = 125;

    private static final int MENU_COPY = 1, MENU_PASTE = 2, MENU_NEW_TAB = 3, MENU_NEW_WINDOW = 4,
            MENU_RESET = 5, MENU_SETTINGS = 6, MENU_CLOSE_TAB = 7;

    // GNOME's default (Tango) palette: readable on both the light and the dark background.
    private static final String[] ANSI = {
            "#2e3436", "#cc0000", "#4e9a06", "#c4a000", "#3465a4", "#75507b", "#06989a", "#d3d7cf",
            "#555753", "#ef2929", "#8ae234", "#fce94f", "#729fcf", "#ad7fa8", "#34e2e2", "#eeeeec"};

    // On a light background Tango's bright colors (bold green prompt, yellow, white) wash out:
    // the light theme draws them with the normal tones, white as dark grey.
    private static final String[] ANSI_LIGHT_OVERRIDES = {
            "7", "#5e5c64", "9", "#cc0000", "10", "#4e9a06", "11", "#c4a000", "12", "#3465a4",
            "13", "#75507b", "14", "#06989a", "15", "#2e3436"};

    private enum Screen { LOADING, SETUP, TERMINAL }

    private final Handler main = new Handler(Looper.getMainLooper());
    private final ExecutorService io = Executors.newCachedThreadPool();
    private Prefs prefs;
    private String windowId;
    private int restoreIndex = -1;
    private SessionService service;
    private TerminalSession current;
    private Screen screen = Screen.LOADING;

    private View root, tabBar;
    private LinearLayout tabs;
    private HorizontalScrollView tabScroll, extraKeysScroll;
    private TerminalView terminal;
    private View bellFlash;
    private ProgressBar loading;
    private SetupScreen setup;
    private ExtraKeys extraKeys;
    private float scale = 1f;
    private boolean imeVisible;

    private final ServiceConnection connection = new ServiceConnection() {
        @Override
        public void onServiceConnected(ComponentName name, IBinder binder) {
            service = ((SessionService.LocalBinder) binder).service();
            service.setListener(windowId, TerminalActivity.this);
            takeRequests(getIntent());
            // Sessions outlive the window: after a theme change their palettes follow it here.
            applyColors();
            refreshState();
        }

        @Override
        public void onServiceDisconnected(ComponentName name) {
            service = null;
        }
    };

    private final SharedPreferences.OnSharedPreferenceChangeListener prefsListener =
            (p, key) -> { if ("font_sp".equals(key)) applyFontSize(); };

    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);
        prefs = new Prefs(this);
        windowId = state != null ? state.getString(STATE_WINDOW) : null;
        if (windowId == null) windowId = UUID.randomUUID().toString();
        if (state != null) restoreIndex = state.getInt(STATE_CURRENT, -1);
        getWindow().setDecorFitsSystemWindows(false);
        setContentView(R.layout.activity_terminal);

        root = findViewById(R.id.root);
        tabBar = findViewById(R.id.tab_bar);
        tabs = findViewById(R.id.tabs);
        tabScroll = findViewById(R.id.tab_scroll);
        terminal = findViewById(R.id.terminal);
        bellFlash = findViewById(R.id.bell_flash);
        loading = findViewById(R.id.loading);
        extraKeysScroll = findViewById(R.id.extra_keys_scroll);
        extraKeys = new ExtraKeys(findViewById(R.id.extra_keys));
        extraKeys.attach(terminal);
        setup = new SetupScreen(this, findViewById(R.id.setup), setupActions);

        root.setOnApplyWindowInsetsListener((v, insets) -> {
            Insets bars = insets.getInsets(WindowInsets.Type.systemBars() | WindowInsets.Type.displayCutout());
            Insets ime = insets.getInsets(WindowInsets.Type.ime());
            v.setPadding(bars.left, bars.top, bars.right, Math.max(bars.bottom, ime.bottom));
            imeVisible = insets.isVisible(WindowInsets.Type.ime());
            updateExtraKeys();
            return WindowInsets.CONSUMED;
        });

        terminal.setTerminalViewClient(viewClient);
        applyFontSize();  // creates the renderer; the typeface goes into it
        terminal.setTypeface(Typeface.MONOSPACE);
        terminal.setOnTouchListener(new MouseSelection(terminal));
        terminal.setTerminalCursorBlinkerRate(600);
        registerForContextMenu(terminal);
        applyColors();

        findViewById(R.id.add_tab).setOnClickListener(v -> newTab());
        ImageButton menu = findViewById(R.id.menu_button);
        menu.setOnClickListener(this::showMenu);
        prefs.registerListener(prefsListener);

        Intent svc = new Intent(this, SessionService.class);
        startService(svc);
        bindService(svc, connection, Context.BIND_AUTO_CREATE);
    }

    @Override
    protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        setIntent(intent);
        if (service != null && takeRequests(intent)) refreshState();
    }

    /**
     * Sessions this window takes on from its intent: an approved command of another app (by
     * token, never by command text: the activity is exported) or the background window's
     * sessions. Each intent is acted on once.
     */
    private boolean takeRequests(Intent intent) {
        if (intent == null) return false;
        boolean changed = false;
        if (ACTION_SHOW_BACKGROUND.equals(intent.getAction())) {
            service.adoptWindow(SessionService.BACKGROUND_WINDOW, windowId);
            intent.setAction(null);
            changed = true;
        }
        DroidRequests.Approved approved = DroidRequests.take(intent.getStringExtra(EXTRA_DROID_TOKEN));
        intent.removeExtra(EXTRA_DROID_TOKEN);
        if (approved != null) {
            current = service.createDroidSession(windowId, approved);
            changed = true;
        }
        return changed;
    }

    @Override
    protected void onSaveInstanceState(Bundle out) {
        super.onSaveInstanceState(out);
        out.putString(STATE_WINDOW, windowId);
        if (service != null && current != null) out.putInt(STATE_CURRENT, sessions().indexOf(current));
    }

    @Override
    protected void onResume() {
        super.onResume();
        if (screen == Screen.TERMINAL && current != null) {
            terminal.onScreenUpdated();
            terminal.setTerminalCursorBlinkerState(true, true);
        }
    }

    @Override
    protected void onPause() {
        super.onPause();
        bellFlash.animate().cancel();
        bellFlash.setVisibility(View.GONE);
        terminal.setTerminalCursorBlinkerState(false, true);
    }

    @Override
    protected void onDestroy() {
        prefs.unregisterListener(prefsListener);
        if (service != null) {
            // Closing the window closes its tabs; anything else (a restart) keeps them running.
            if (isFinishing()) service.closeWindow(windowId);
            else service.setListener(windowId, null);
        }
        unbindService(connection);
        io.shutdownNow();
        super.onDestroy();
    }

    @Override
    public void onConfigurationChanged(Configuration config) {
        super.onConfigurationChanged(config);
        applyFontSize();
        updateExtraKeys();
    }

    // ---- state

    private List<TerminalSession> sessions() {
        return service.sessions(windowId);
    }

    private void show(Screen s) {
        screen = s;
        loading.setVisibility(s == Screen.LOADING ? View.VISIBLE : View.GONE);
        setup.view().setVisibility(s == Screen.SETUP ? View.VISIBLE : View.GONE);
        terminal.setVisibility(s == Screen.TERMINAL ? View.VISIBLE : View.GONE);
        tabBar.setVisibility(s == Screen.TERMINAL ? View.VISIBLE : View.INVISIBLE);
        updateExtraKeys();
    }

    /** Asks the Linux service where things stand and shows the matching screen. */
    private void refreshState() {
        if (service != null && !sessions().isEmpty()) {
            showTerminal();
            return;
        }
        show(Screen.LOADING);
        io.execute(() -> {
            JSONObject status;
            try {
                status = DaemonClient.request(DaemonClient.op("status"));
            } catch (IOException e) {
                main.post(() -> {
                    show(Screen.SETUP);
                    setup.showForm(SetupScreen.Mode.INSTALL);
                    setup.showError("service", e.getMessage());
                });
                return;
            }
            main.post(() -> route(status));
        });
    }

    private void route(JSONObject status) {
        if (isDestroyed()) return;
        VmStatus.publish(this);  // installation finished, removed, ... (observers of vm_status)
        String state = status.optString("state");
        if ("installing".equals(state)) {
            show(Screen.SETUP);
            setup.showRunning();
            if (status.optJSONObject("progress") != null) setup.onProgress(status.optJSONObject("progress"));
            follow(null);  // attach to the running installation
        } else if (!status.optBoolean("installed")) {
            show(Screen.SETUP);
            setup.showForm(SetupScreen.Mode.INSTALL);
            if ("failed".equals(state)) {
                setup.showError(status.optString("error_code"), status.optString("error"));
            }
        } else if (status.isNull("user") || status.optJSONObject("user") == null) {
            show(Screen.SETUP);
            setup.showForm(SetupScreen.Mode.USER);
        } else {
            showTerminal();
        }
    }

    private final SetupScreen.Actions setupActions = new SetupScreen.Actions() {
        @Override
        public void onInstall(String userName) {
            setup.clearLog();
            setup.showRunning();
            follow(userName);
        }

        @Override
        public void onCancel() {
            io.execute(() -> {
                try {
                    DaemonClient.request(DaemonClient.op("cancel"));
                } catch (IOException ignored) {
                }
            });
        }

        @Override
        public void onRetry() {
            refreshState();
        }
    };

    /** Starts (userName != null) or attaches to the installation and follows its events. */
    private void follow(String userName) {
        io.execute(() -> {
            try (DaemonClient c = DaemonClient.connect()) {
                JSONObject req = DaemonClient.op("install");
                if (userName != null) {
                    req.put("user", userName);
                    JSONArray locales = new JSONArray();
                    LocaleList list = LocaleList.getDefault();
                    for (int i = 0; i < list.size(); i++) locales.put(SessionService.posixLocale(list.get(i)));
                    req.put("locales", locales);
                }
                c.send(req);
                JSONObject ev;
                while ((ev = c.read()) != null) {
                    JSONObject e = ev;
                    String kind = e.optString("event");
                    if ("progress".equals(kind)) {
                        main.post(() -> setup.onProgress(e));
                    } else if ("log".equals(kind)) {
                        main.post(() -> setup.onLog(e.optString("line")));
                    } else if ("done".equals(kind) || e.has("ok")) {
                        main.post(() -> installFinished(e));
                        return;
                    }
                }
                main.post(() -> setup.showError("service", null));
            } catch (IOException | JSONException e) {
                main.post(() -> setup.showError("service", e.getMessage()));
            }
        });
    }

    private void installFinished(JSONObject done) {
        if (isDestroyed()) return;
        if (!done.optBoolean("ok")) {
            setup.showError(done.optString("code"), done.optString("error"));
            return;
        }
        SharedPreferences p = getSharedPreferences("terminal", MODE_PRIVATE);
        if (!Environment.isExternalStorageManager() && !p.getBoolean("files_asked", false)) {
            p.edit().putBoolean("files_asked", true).apply();
            new AlertDialog.Builder(this)
                    .setTitle(R.string.files_access_title)
                    .setMessage(R.string.files_access_body)
                    .setPositiveButton(R.string.files_access_allow, (d, w) -> startActivity(
                            new Intent(Settings.ACTION_MANAGE_APP_ALL_FILES_ACCESS_PERMISSION,
                                    Uri.parse("package:" + getPackageName()))))
                    .setNegativeButton(R.string.cancel, null)
                    .setOnDismissListener(d -> refreshState())
                    .show();
            return;
        }
        refreshState();
    }

    // ---- tabs

    private void showTerminal() {
        show(Screen.TERMINAL);
        List<TerminalSession> list = sessions();
        if (list.isEmpty()) {
            newTab();
            return;
        }
        TerminalSession target = current;
        if (target == null || !list.contains(target)) {
            target = restoreIndex >= 0 && restoreIndex < list.size() ? list.get(restoreIndex) : list.get(list.size() - 1);
        }
        switchTo(target);
    }

    private void newTab() {
        if (service == null) return;
        if (screen != Screen.TERMINAL) show(Screen.TERMINAL);
        switchTo(service.createSession(windowId));
    }

    private void switchTo(TerminalSession s) {
        current = s;
        terminal.attachSession(s);
        terminal.onScreenUpdated();
        terminal.requestFocus();
        rebuildTabs();
        updateTitle();
    }

    private void closeTab(TerminalSession s) {
        List<TerminalSession> list = sessions();
        int index = list.indexOf(s);
        service.removeSession(s);
        if (list.isEmpty()) {
            current = null;
            finishAndRemoveTask();
            return;
        }
        if (s == current) switchTo(list.get(Math.min(Math.max(index, 0), list.size() - 1)));
        else rebuildTabs();
    }

    private void cycleTab(int delta) {
        List<TerminalSession> list = sessions();
        if (list.size() < 2 || current == null) return;
        int i = (list.indexOf(current) + delta + list.size()) % list.size();
        switchTo(list.get(i));
    }

    private String titleOf(TerminalSession s) {
        String t = s.getTitle();
        return t == null || t.trim().isEmpty() ? getString(R.string.tab_default_title) : t.trim();
    }

    private void updateTitle() {
        if (current != null) setTitle(titleOf(current));
    }

    private void rebuildTabs() {
        tabs.removeAllViews();
        if (service == null) return;
        float dp = getResources().getDisplayMetrics().density;
        View selected = null;
        for (TerminalSession s : sessions()) {
            LinearLayout tab = new LinearLayout(this);
            tab.setOrientation(LinearLayout.HORIZONTAL);
            tab.setGravity(Gravity.CENTER_VERTICAL);
            tab.setBackgroundResource(R.drawable.tab_background);
            tab.setPaddingRelative((int) (12 * dp), 0, (int) (4 * dp), 0);
            tab.setMinimumWidth((int) (120 * dp));
            tab.setSelected(s == current);
            TextView title = new TextView(this);
            title.setTextAppearance(R.style.TabText);
            title.setTextColor(getColor(s == current ? R.color.on_secondary_container : R.color.on_surface_variant));
            title.setText(titleOf(s));
            title.setMaxWidth((int) (220 * dp));
            tab.addView(title, new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1));
            ImageButton close = new ImageButton(this);
            close.setImageResource(R.drawable.ic_close);
            close.setBackgroundResource(R.drawable.round_button);
            close.setContentDescription(getString(R.string.close_tab));
            close.setTooltipText(getString(R.string.close_tab));
            close.setOnClickListener(v -> closeTab(s));
            tab.addView(close, new LinearLayout.LayoutParams((int) (28 * dp), (int) (28 * dp)));
            tab.setOnClickListener(v -> switchTo(s));
            tab.setOnGenericMotionListener((v, e) -> false);
            tab.setOnTouchListener((v, e) -> {
                // Middle click closes a tab, as in every desktop tab strip.
                if (e.isFromSource(InputDevice.SOURCE_MOUSE) && e.getActionMasked() == MotionEvent.ACTION_DOWN
                        && (e.getButtonState() & MotionEvent.BUTTON_TERTIARY) != 0) {
                    closeTab(s);
                    return true;
                }
                return false;
            });
            LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT, (int) (34 * dp));
            lp.setMarginEnd((int) (4 * dp));
            tabs.addView(tab, lp);
            if (s == current) selected = tab;
        }
        if (selected != null) {
            View target = selected;
            tabScroll.post(() -> tabScroll.requestChildRectangleOnScreen(tabs,
                    new android.graphics.Rect(target.getLeft(), 0, target.getRight(), target.getHeight()), false));
        }
    }

    private void showMenu(View anchor) {
        PopupMenu popup = new PopupMenu(this, anchor);
        Menu m = popup.getMenu();
        m.add(0, MENU_NEW_TAB, 0, R.string.new_tab);
        m.add(0, MENU_NEW_WINDOW, 1, R.string.new_window);
        if (current != null) m.add(0, MENU_RESET, 2, R.string.reset_terminal);
        m.add(0, MENU_SETTINGS, 3, R.string.settings);
        popup.setOnMenuItemClickListener(this::onMenuItem);
        popup.show();
    }

    private boolean onMenuItem(MenuItem item) {
        switch (item.getItemId()) {
            case MENU_COPY: copySelection(); return true;
            case MENU_PASTE: paste(); return true;
            case MENU_NEW_TAB: newTab(); return true;
            case MENU_NEW_WINDOW: newWindow(); return true;
            case MENU_RESET:
                if (current != null) {
                    current.reset();
                    terminal.invalidate();
                }
                return true;
            case MENU_SETTINGS: startActivity(new Intent(this, SettingsActivity.class)); return true;
            case MENU_CLOSE_TAB: if (current != null) closeTab(current); return true;
            default: return false;
        }
    }

    private void newWindow() {
        startActivity(new Intent(this, TerminalActivity.class)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_MULTIPLE_TASK));
    }

    @Override
    public void onCreateContextMenu(ContextMenu menu, View v, ContextMenu.ContextMenuInfo info) {
        String selected = terminal.getStoredSelectedText();
        menu.add(0, MENU_COPY, 0, R.string.copy).setEnabled(selected != null && !selected.isEmpty());
        menu.add(0, MENU_PASTE, 1, R.string.paste);
        menu.add(0, MENU_NEW_TAB, 2, R.string.new_tab);
        menu.add(0, MENU_RESET, 3, R.string.reset_terminal);
        menu.add(0, MENU_SETTINGS, 4, R.string.settings);
    }

    @Override
    public boolean onContextItemSelected(MenuItem item) {
        if (item.getItemId() == MENU_COPY) {
            String text = terminal.getStoredSelectedText();
            terminal.unsetStoredSelectedText();
            copy(text);
            return true;
        }
        return onMenuItem(item);
    }

    @Override
    public void onContextMenuClosed(Menu menu) {
        terminal.onContextMenuClosed(menu);
        super.onContextMenuClosed(menu);
    }

    @Override
    public boolean dispatchKeyEvent(KeyEvent e) {
        // TerminalView ends a selection on any key, the Ctrl/Shift of Ctrl+Shift+C included, so the
        // copy chord is handled here, before the view sees it.
        if (screen == Screen.TERMINAL && terminal.isSelectingText()) {
            if (KeyEvent.isModifierKey(e.getKeyCode())) return true;
            if (e.getKeyCode() == KeyEvent.KEYCODE_C && e.isCtrlPressed() && e.isShiftPressed()) {
                if (e.getAction() == KeyEvent.ACTION_DOWN) copySelection();
                return true;
            }
        }
        return super.dispatchKeyEvent(e);
    }

    // ---- clipboard, font, colors

    private void copy(String text) {
        if (text == null || text.isEmpty()) return;
        getSystemService(ClipboardManager.class).setPrimaryClip(ClipData.newPlainText(getString(R.string.app_name), text));
    }

    private void copySelection() {
        if (terminal.isSelectingText()) {
            copy(terminal.getSelectedText());
            terminal.stopTextSelectionMode();
        }
    }

    private void paste() {
        if (current == null || current.getEmulator() == null) return;
        ClipData clip = getSystemService(ClipboardManager.class).getPrimaryClip();
        CharSequence text = clip == null || clip.getItemCount() == 0 ? null : clip.getItemAt(0).coerceToText(this);
        if (text == null || text.length() == 0) {
            Toast.makeText(this, R.string.paste_empty, Toast.LENGTH_SHORT).show();
            return;
        }
        current.getEmulator().paste(text.toString());
    }

    private void applyFontSize() {
        float sp = prefs.fontSizeSp();
        int px = Math.round(TypedValue.applyDimension(TypedValue.COMPLEX_UNIT_SP, sp, getResources().getDisplayMetrics()));
        terminal.setTextSize(px);
    }

    private void changeFontSize(float deltaSp) {
        prefs.setFontSizeSp(deltaSp == 0 ? Prefs.DEFAULT_FONT_SP : prefs.fontSizeSp() + deltaSp);
        applyFontSize();
    }

    /** Terminal colors from the Material You palette of the current (light/dark) theme. */
    private void applyColors() {
        Properties p = new Properties();
        p.put("foreground", hex(getColor(R.color.terminal_foreground)));
        p.put("background", hex(getColor(R.color.terminal_background)));
        p.put("cursor", hex(getColor(R.color.terminal_cursor)));
        for (int i = 0; i < ANSI.length; i++) p.put("color" + i, ANSI[i]);
        boolean night = (getResources().getConfiguration().uiMode & Configuration.UI_MODE_NIGHT_MASK)
                == Configuration.UI_MODE_NIGHT_YES;
        if (!night) {
            for (int i = 0; i < ANSI_LIGHT_OVERRIDES.length; i += 2)
                p.put("color" + ANSI_LIGHT_OVERRIDES[i], ANSI_LIGHT_OVERRIDES[i + 1]);
        }
        TerminalColors.COLOR_SCHEME.updateWith(p);
        if (service != null) {
            for (TerminalSession s : sessions()) if (s.getEmulator() != null) s.getEmulator().mColors.reset();
        }
        terminal.invalidate();
    }

    private static String hex(int color) {
        return String.format("#%06x", color & 0xffffff);
    }

    private void updateExtraKeys() {
        Configuration c = getResources().getConfiguration();
        boolean hardware = c.keyboard != Configuration.KEYBOARD_NOKEYS
                && c.hardKeyboardHidden != Configuration.HARDKEYBOARDHIDDEN_YES;
        extraKeysScroll.setVisibility(screen == Screen.TERMINAL && imeVisible && !hardware ? View.VISIBLE : View.GONE);
    }

    // ---- SessionService.Listener

    @Override
    public void onTextChanged(TerminalSession session) {
        if (session == current) terminal.onScreenUpdated();
    }

    @Override
    public void onTitleChanged(TerminalSession session) {
        rebuildTabs();
        if (session == current) updateTitle();
    }

    @Override
    public void onSessionFinished(TerminalSession session) {
        int code = session.getExitStatus();
        if (code == 0) {
            closeTab(session);
            return;
        }
        if (code == EXIT_NOT_INSTALLED) {
            service.removeSession(session);
            current = null;
            refreshState();
            return;
        }
        // The terminal library has printed "[Process completed (code N) - press Enter]";
        // Enter closes the tab, or retries it when gbos-linux itself failed (onKeyDown).
        if (session == current) terminal.onScreenUpdated();
    }

    @Override
    public void onBell(TerminalSession session) {
        if (session != current || !hasWindowFocus()) return;
        bellFlash.animate().cancel();
        bellFlash.setVisibility(View.VISIBLE);
        bellFlash.setAlpha(0.12f);
        bellFlash.animate().alpha(0f).setDuration(180)
                .withEndAction(() -> bellFlash.setVisibility(View.GONE)).start();
    }

    @Override
    public void onColorsChanged(TerminalSession session) {
        if (session == current) terminal.invalidate();
    }

    @Override
    public void onCursorBlink(boolean visible) {
        terminal.setTerminalCursorBlinkerState(visible, true);
    }

    // ---- TerminalViewClient

    private final TerminalViewClient viewClient = new TerminalViewClient() {
        @Override
        public float onScale(float s) {
            if (s < 0.9f || s > 1.1f) {
                changeFontSize(s > 1f ? 1 : -1);
                return 1f;
            }
            return s;
        }

        @Override
        public void onSingleTapUp(MotionEvent e) {
            Configuration c = getResources().getConfiguration();
            if (c.hardKeyboardHidden != Configuration.HARDKEYBOARDHIDDEN_NO || c.keyboard == Configuration.KEYBOARD_NOKEYS) {
                getSystemService(InputMethodManager.class).showSoftInput(terminal, 0);
            }
        }

        @Override
        public boolean shouldBackButtonBeMappedToEscape() {
            return false;
        }

        @Override
        public boolean shouldEnforceCharBasedInput() {
            return false;
        }

        @Override
        public boolean shouldUseCtrlSpaceWorkaround() {
            return false;
        }

        @Override
        public boolean isTerminalViewSelected() {
            return screen == Screen.TERMINAL;
        }

        @Override
        public void copyModeChanged(boolean copyMode) {
        }

        @Override
        public boolean onKeyDown(int keyCode, KeyEvent e, TerminalSession session) {
            if (session != null && !session.isRunning()) {
                if (keyCode == KeyEvent.KEYCODE_ENTER || keyCode == KeyEvent.KEYCODE_NUMPAD_ENTER) {
                    if (session.getExitStatus() == EXIT_TOOL_ERROR) {
                        // Retry in the same place: a fresh session replaces the failed one.
                        int index = sessions().indexOf(session);
                        service.removeSession(session);
                        TerminalSession fresh = service.createSession(windowId);
                        List<TerminalSession> list = sessions();
                        list.remove(fresh);
                        list.add(Math.min(index, list.size()), fresh);
                        switchTo(fresh);
                    } else {
                        closeTab(session);
                    }
                }
                return true;
            }
            boolean ctrl = e.isCtrlPressed(), shift = e.isShiftPressed(), alt = e.isAltPressed();
            if (ctrl && shift && !alt) {
                switch (keyCode) {
                    case KeyEvent.KEYCODE_C: copySelection(); return true;
                    case KeyEvent.KEYCODE_V: paste(); return true;
                    case KeyEvent.KEYCODE_T: newTab(); return true;
                    case KeyEvent.KEYCODE_W: if (current != null) closeTab(current); return true;
                    case KeyEvent.KEYCODE_N: newWindow(); return true;
                    case KeyEvent.KEYCODE_TAB: cycleTab(-1); return true;
                    case KeyEvent.KEYCODE_PLUS:
                    case KeyEvent.KEYCODE_EQUALS: changeFontSize(1); return true;
                    default: break;
                }
            }
            if (ctrl && !alt) {
                switch (keyCode) {
                    case KeyEvent.KEYCODE_TAB: if (!shift) { cycleTab(1); return true; } break;
                    case KeyEvent.KEYCODE_PAGE_DOWN: cycleTab(1); return true;
                    case KeyEvent.KEYCODE_PAGE_UP: cycleTab(-1); return true;
                    case KeyEvent.KEYCODE_EQUALS:
                    case KeyEvent.KEYCODE_PLUS:
                    case KeyEvent.KEYCODE_NUMPAD_ADD: changeFontSize(1); return true;
                    case KeyEvent.KEYCODE_MINUS:
                    case KeyEvent.KEYCODE_NUMPAD_SUBTRACT: if (!shift) { changeFontSize(-1); return true; } break;
                    case KeyEvent.KEYCODE_0:
                    case KeyEvent.KEYCODE_NUMPAD_0: if (!shift) { changeFontSize(0); return true; } break;
                    default: break;
                }
            }
            return false;
        }

        @Override
        public boolean onKeyUp(int keyCode, KeyEvent e) {
            return false;
        }

        @Override
        public boolean onLongPress(MotionEvent event) {
            return false;
        }

        @Override
        public boolean readControlKey() {
            return extraKeys.readCtrl();
        }

        @Override
        public boolean readAltKey() {
            return extraKeys.readAlt();
        }

        @Override
        public boolean readShiftKey() {
            return false;
        }

        @Override
        public boolean readFnKey() {
            return false;
        }

        @Override
        public boolean onCodePoint(int codePoint, boolean ctrlDown, TerminalSession session) {
            return false;
        }

        @Override
        public void onEmulatorSet() {
            terminal.setTerminalCursorBlinkerState(true, true);
        }

        @Override
        public void logError(String tag, String message) {
            android.util.Log.e("Terminal", tag + ": " + message);
        }

        @Override
        public void logWarn(String tag, String message) {
            android.util.Log.w("Terminal", tag + ": " + message);
        }

        @Override
        public void logInfo(String tag, String message) {
        }

        @Override
        public void logDebug(String tag, String message) {
        }

        @Override
        public void logVerbose(String tag, String message) {
        }

        @Override
        public void logStackTraceWithMessage(String tag, String message, Exception e) {
            android.util.Log.e("Terminal", tag + ": " + message, e);
        }

        @Override
        public void logStackTrace(String tag, Exception e) {
            android.util.Log.e("Terminal", tag, e);
        }
    };
}
