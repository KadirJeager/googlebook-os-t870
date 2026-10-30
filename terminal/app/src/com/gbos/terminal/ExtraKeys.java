package com.gbos.terminal;

import android.content.Context;
import android.util.TypedValue;
import android.view.Gravity;
import android.view.HapticFeedbackConstants;
import android.view.KeyEvent;
import android.view.ViewGroup;
import android.widget.LinearLayout;
import android.widget.TextView;

import com.termux.terminal.KeyHandler;
import com.termux.view.TerminalView;

/**
 * Keys a touch keyboard lacks (Esc, Tab, Ctrl, Alt, arrows...), shown above it only when no
 * hardware keyboard is attached. Ctrl and Alt latch for the next key.
 */
final class ExtraKeys {
    private final LinearLayout row;
    private TerminalView terminal;
    private boolean ctrl, alt;
    private TextView ctrlKey, altKey;

    ExtraKeys(LinearLayout row) {
        this.row = row;
        Context c = row.getContext();
        add(c, "ESC", KeyEvent.KEYCODE_ESCAPE);
        add(c, "TAB", KeyEvent.KEYCODE_TAB);
        ctrlKey = addToggle(c, "CTRL", true);
        altKey = addToggle(c, "ALT", false);
        add(c, "←", KeyEvent.KEYCODE_DPAD_LEFT);
        add(c, "↓", KeyEvent.KEYCODE_DPAD_DOWN);
        add(c, "↑", KeyEvent.KEYCODE_DPAD_UP);
        add(c, "→", KeyEvent.KEYCODE_DPAD_RIGHT);
        add(c, "HOME", KeyEvent.KEYCODE_MOVE_HOME);
        add(c, "END", KeyEvent.KEYCODE_MOVE_END);
        add(c, "PGUP", KeyEvent.KEYCODE_PAGE_UP);
        add(c, "PGDN", KeyEvent.KEYCODE_PAGE_DOWN);
        for (String s : new String[] {"-", "/", "|", "~", "_", ":"}) addChar(c, s);
    }

    void attach(TerminalView terminal) {
        this.terminal = terminal;
    }

    /** One-shot modifiers, consumed by the next key typed. */
    boolean readCtrl() {
        boolean v = ctrl;
        if (v) setCtrl(false);
        return v;
    }

    boolean readAlt() {
        boolean v = alt;
        if (v) setAlt(false);
        return v;
    }

    private void setCtrl(boolean on) {
        ctrl = on;
        ctrlKey.setActivated(on);
    }

    private void setAlt(boolean on) {
        alt = on;
        altKey.setActivated(on);
    }

    private TextView key(Context c, String label) {
        TextView t = new TextView(c);
        t.setText(label);
        t.setGravity(Gravity.CENTER);
        t.setTextSize(TypedValue.COMPLEX_UNIT_SP, 13);
        t.setTextColor(c.getColor(R.color.on_surface));
        t.setBackgroundResource(R.drawable.key_background);
        int pad = (int) TypedValue.applyDimension(TypedValue.COMPLEX_UNIT_DIP, 12, c.getResources().getDisplayMetrics());
        t.setPadding(pad, 0, pad, 0);
        t.setMinWidth((int) TypedValue.applyDimension(TypedValue.COMPLEX_UNIT_DIP, 44, c.getResources().getDisplayMetrics()));
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT,
                ViewGroup.LayoutParams.MATCH_PARENT);
        lp.setMargins(2, 4, 2, 4);
        row.addView(t, lp);
        return t;
    }

    private void add(Context c, String label, int keyCode) {
        TextView t = key(c, label);
        t.setOnClickListener(v -> {
            v.performHapticFeedback(HapticFeedbackConstants.KEYBOARD_TAP);
            if (terminal == null) return;
            int mod = 0;
            if (readCtrl()) mod |= KeyHandler.KEYMOD_CTRL;
            if (readAlt()) mod |= KeyHandler.KEYMOD_ALT;
            terminal.handleKeyCode(keyCode, mod);
        });
    }

    private void addChar(Context c, String ch) {
        TextView t = key(c, ch);
        t.setOnClickListener(v -> {
            v.performHapticFeedback(HapticFeedbackConstants.KEYBOARD_TAP);
            if (terminal == null) return;
            terminal.inputCodePoint(TerminalView.KEY_EVENT_SOURCE_VIRTUAL_KEYBOARD, ch.codePointAt(0), readCtrl(), readAlt());
        });
    }

    private TextView addToggle(Context c, String label, boolean isCtrl) {
        TextView t = key(c, label);
        t.setOnClickListener(v -> {
            v.performHapticFeedback(HapticFeedbackConstants.KEYBOARD_TAP);
            if (isCtrl) setCtrl(!ctrl);
            else setAlt(!alt);
        });
        return t;
    }
}
