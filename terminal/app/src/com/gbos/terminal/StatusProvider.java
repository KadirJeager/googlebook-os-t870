package com.gbos.terminal;

import android.content.ContentProvider;
import android.content.ContentValues;
import android.database.Cursor;
import android.net.Uri;
import android.os.Bundle;
import android.util.Log;

import java.io.IOException;

/**
 * The state calls of Android's terminal (TerminalStatusProvider), answered from gbos-linuxd's
 * real state. Only the compatibility package com.android.virtualization.terminal (terminal/kopru,
 * same signing key: permission com.gbos.terminal.permission.TERMINAL_BRIDGE) calls it; it holds
 * the provider authority apps such as Antigravity use.
 */
public final class StatusProvider extends ContentProvider {
    private static final String TAG = "Terminal";

    @Override
    public boolean onCreate() {
        return true;
    }

    @Override
    public Bundle call(String method, String arg, Bundle extras) {
        getContext().enforceCallingOrSelfPermission(AgentRequestActivity.BRIDGE_PERMISSION, "Terminal status");
        switch (method) {
            case "getVmStatus":
                try {
                    return VmStatus.query(VmStatus.foreground(getContext())).toBundle();
                } catch (IOException e) {
                    // The service is not reachable: nothing is installed or running as far as
                    // anyone here can tell; report that rather than a guess.
                    Log.w(TAG, "getVmStatus: " + e.getMessage());
                    Bundle b = new Bundle();
                    b.putBoolean("is_installed", false);
                    b.putBoolean("is_running", false);
                    b.putBoolean("is_installing", false);
                    b.putBoolean("is_foreground", VmStatus.foreground(getContext()));
                    return b;
                }
            case "isAutoPortForwardEnabled": {
                // Linux shares Android's network namespace here: every port a Linux program
                // listens on is reachable on the device as is, which is what "forward all
                // ports" gives in Android's terminal.
                Bundle b = new Bundle();
                b.putBoolean("is_auto_port_forward_enabled", true);
                return b;
            }
            case "setAutoPortForwardEnabled": {
                // Turning it on is the state already; turning it off cannot be honoured.
                boolean wanted = extras != null && extras.getBoolean("is_auto_port_forward_enabled", false);
                Bundle b = new Bundle();
                b.putBoolean("result", wanted);
                return b;
            }
            default:
                return super.call(method, arg, extras);
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
