package com.android.virtualization.terminal;

import android.content.ContentProvider;
import android.content.ContentValues;
import android.database.Cursor;
import android.net.Uri;
import android.os.Binder;
import android.os.Bundle;
import android.util.Log;

/**
 * content://com.android.virtualization.terminal.provider: the status calls of Android's
 * terminal (getVmStatus, isAutoPortForwardEnabled, setAutoPortForwardEnabled), answered by
 * com.gbos.terminal from the Linux environment's real state. Change notifications for
 * .../vm_status come from com.gbos.terminal, which sees the changes.
 */
public final class StatusBridgeProvider extends ContentProvider {
    static final String PERMISSION = "com.android.virtualization.terminal.permission.MANAGE_TERMINAL";
    private static final String TERMINAL_STATUS = "com.gbos.terminal.status";
    private static final String TAG = "TerminalBridge";

    @Override
    public boolean onCreate() {
        return true;
    }

    @Override
    public Bundle call(String method, String arg, Bundle extras) {
        getContext().enforceCallingOrSelfPermission(PERMISSION, "Linux terminal status");
        switch (method) {
            case "getVmStatus":
            case "isAutoPortForwardEnabled":
            case "setAutoPortForwardEnabled":
                break;
            default:
                return super.call(method, arg, extras);
        }
        long id = Binder.clearCallingIdentity();
        try {
            return getContext().getContentResolver().call(TERMINAL_STATUS, method, arg, extras);
        } catch (RuntimeException e) {
            // No answer is better than a made-up one: the caller sees a null bundle, as when
            // Android's terminal cannot answer.
            Log.w(TAG, method + ": " + e);
            return null;
        } finally {
            Binder.restoreCallingIdentity(id);
        }
    }

    @Override
    public Cursor query(Uri uri, String[] projection, String selection, String[] selectionArgs, String sortOrder) {
        return null;
    }

    @Override
    public String getType(Uri uri) {
        return null;
    }

    @Override
    public Uri insert(Uri uri, ContentValues values) {
        return null;
    }

    @Override
    public int delete(Uri uri, String selection, String[] selectionArgs) {
        return 0;
    }

    @Override
    public int update(Uri uri, ContentValues values, String selection, String[] selectionArgs) {
        return 0;
    }
}
