package com.android.virtualization.terminal;

import android.app.Activity;
import android.content.ActivityNotFoundException;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.os.Bundle;
import android.os.Process;
import android.util.Log;

/**
 * Receives the requests apps send to Android's terminal (VIEW android-terminal://start...,
 * SHARE_DIRECTORY) and hands them to com.gbos.terminal's AgentRequestActivity, which checks them
 * and asks the user, together with the caller's identity as Android reports it here. The result
 * goes back to the caller unchanged. Like Android's terminal, a caller that did not start this
 * for a result (getCallingPackage() null) is refused.
 */
public final class RequestBridgeActivity extends Activity {
    static final String TERMINAL = "com.gbos.terminal";
    private static final String TAG = "TerminalBridge";
    private static final int REQUEST = 1;

    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);
        if (state != null) return;  // recreated while the Terminal's answer is pending
        String caller = getCallingPackage();
        if (caller == null) {
            Log.w(TAG, "request rejected: the caller must start it for a result");
            finishWith(RESULT_CANCELED);
            return;
        }
        int uid = getLaunchedFromUid();
        if (uid == Process.INVALID_UID) {
            try {
                uid = getPackageManager().getPackageUid(caller, 0);
            } catch (PackageManager.NameNotFoundException e) {
                Log.w(TAG, "request rejected: unknown caller " + caller);
                finishWith(RESULT_CANCELED);
                return;
            }
        }
        Intent in = getIntent();
        Intent out = new Intent(in.getAction(), in.getData())
                .setClassName(TERMINAL, TERMINAL + ".AgentRequestActivity")
                .putExtra(TERMINAL + ".extra.CALLER_PACKAGE", caller)
                .putExtra(TERMINAL + ".extra.CALLER_UID", uid);
        String background = "com.android.virtualization.terminal.EXTRA_GO_BACKGROUND";
        if (in.hasExtra(background)) out.putExtra(background, in.getBooleanExtra(background, false));
        if (in.getClipData() != null) out.setClipData(in.getClipData());
        // Pass on only the access the caller granted here.
        out.addFlags(in.getFlags() & (Intent.FLAG_GRANT_READ_URI_PERMISSION | Intent.FLAG_GRANT_WRITE_URI_PERMISSION));
        try {
            startActivityForResult(out, REQUEST);
        } catch (ActivityNotFoundException | SecurityException e) {
            Log.w(TAG, "the Googlebook Terminal cannot take the request: " + e);
            finishWith(RESULT_CANCELED);
        }
    }

    @Override
    protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        if (requestCode == REQUEST) finishWith(resultCode);
    }

    private void finishWith(int result) {
        setResult(result);
        finish();
    }
}
