package com.termux.view;

import android.view.InputDevice;
import android.view.MotionEvent;
import android.view.View;

import com.termux.view.textselection.TextSelectionCursorController;
import com.termux.view.textselection.TextSelectionHandleView;

import java.lang.reflect.Field;

/**
 * Desktop text selection for {@link TerminalView}: press the primary mouse button (or trackpad)
 * and drag, as in any desktop terminal. The library itself selects with a long press and drags
 * to scroll; this keeps its selection machinery (handles, copy toolbar) and only drives it from
 * mouse drags. Lives in the library's package for its package-private selection controller;
 * the library sources stay untouched. Programs that track the mouse (vim, htop) still get the
 * events, as do touches.
 */
public final class MouseSelection implements View.OnTouchListener {
    private static final float SLOP_PX = 6;

    private final TerminalView view;
    private Field startHandleField;
    private float downX, downY;
    private boolean dragging;
    private boolean pressed;

    public MouseSelection(TerminalView view) {
        this.view = view;
        try {
            startHandleField = TextSelectionCursorController.class.getDeclaredField("mStartHandle");
            startHandleField.setAccessible(true);
        } catch (NoSuchFieldException e) {
            startHandleField = null;
        }
    }

    @Override
    public boolean onTouch(View v, MotionEvent e) {
        if (!e.isFromSource(InputDevice.SOURCE_MOUSE) || view.mEmulator == null) return false;
        if (view.mEmulator.isMouseTrackingActive()) return false;
        switch (e.getActionMasked()) {
            case MotionEvent.ACTION_DOWN:
                // Primary button; some touchpads and injected events report no button at all.
                if ((e.getButtonState() & (MotionEvent.BUTTON_SECONDARY | MotionEvent.BUTTON_TERTIARY)) != 0)
                    return false;
                pressed = true;
                dragging = false;
                downX = e.getX();
                downY = e.getY();
                if (view.isSelectingText()) view.stopTextSelectionMode();
                view.requestFocus();
                return true;
            case MotionEvent.ACTION_MOVE:
                if (!pressed) return false;
                if (!dragging) {
                    if (Math.abs(e.getX() - downX) < SLOP_PX && Math.abs(e.getY() - downY) < SLOP_PX) return true;
                    MotionEvent start = MotionEvent.obtain(e);
                    start.setLocation(downX, downY);
                    view.startTextSelectionMode(start);
                    start.recycle();
                    dragging = view.isSelectingText();
                    if (!dragging) return true;
                }
                extend(e.getX(), e.getY());
                return true;
            case MotionEvent.ACTION_UP:
            case MotionEvent.ACTION_CANCEL:
                if (!pressed) return false;
                pressed = false;
                if (!dragging) {
                    // A plain click: focus and keyboard, like a tap.
                    view.performClick();
                    return false;
                }
                dragging = false;
                return true;
            default:
                return false;
        }
    }

    /** Selection from the press point to (x, y), in either direction. */
    private void extend(float x, float y) {
        TextSelectionCursorController c = view.getTextSelectionCursorController();
        TextSelectionHandleView start = null;
        try {
            if (startHandleField != null) start = (TextSelectionHandleView) startHandleField.get(c);
        } catch (IllegalAccessException ignored) {
        }
        int px = (int) x, py = (int) y, ax = (int) downX, ay = (int) downY;
        boolean backwards = view.getCursorY(y) < view.getCursorY(downY)
                || (view.getCursorY(y) == view.getCursorY(downY) && view.getCursorX(x) < view.getCursorX(downX));
        if (start == null || !backwards) {
            // end handle: any handle other than the start one
            c.updatePosition(null, px, py);
            if (start != null) c.updatePosition(start, ax, ay);
            c.updatePosition(null, px, py);
        } else {
            c.updatePosition(start, px, py);
            c.updatePosition(null, ax, ay);
            c.updatePosition(start, px, py);
        }
        c.render();
    }
}
