package com.gbos.terminal;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.ClipData;
import android.content.Intent;
import android.content.pm.ApplicationInfo;
import android.content.pm.PackageManager;
import android.content.pm.ProviderInfo;
import android.database.Cursor;
import android.net.Uri;
import android.os.Bundle;
import android.os.Environment;
import android.os.Handler;
import android.os.Looper;
import android.os.Process;
import android.os.UserHandle;
import android.provider.DocumentsContract;
import android.provider.OpenableColumns;
import android.provider.Settings;
import android.text.format.Formatter;
import android.util.Base64;
import android.util.Log;
import android.view.View;
import android.widget.Button;
import android.widget.ProgressBar;
import android.widget.TextView;

import org.json.JSONException;
import org.json.JSONObject;

import java.io.IOException;
import java.io.InputStream;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/**
 * Requests other apps make of Android's own Linux terminal (com.android.virtualization.terminal,
 * TerminalIntentActivity), carried out in this environment after the user approves them:
 *
 *  - VIEW android-terminal://start?cmd=CMD&amp;dest_path=REL&amp;background=BOOL, ClipData item 0 a
 *    content URI ("Copy &amp; Run"): the file goes to /home/droid/REL, CMD runs as droid;
 *  - com.android.virtualization.terminal.action.SHARE_DIRECTORY with a folder (tree) URI: the
 *    folder of this user's shared storage is reachable in Linux under its Android path.
 *
 * The compatibility package com.android.virtualization.terminal (terminal/kopru) receives the
 * app's intent (apps address that package explicitly), checks it was started for a result and
 * forwards it here with the caller's package and uid; this activity accepts nothing else. It
 * applies Android's terminal's checks (AgentRules, SourceUriVerifier), shows the request as it
 * will run, and runs nothing the user did not approve. The caller gets RESULT_OK only once the
 * request is carried out (file copied, command started).
 */
public final class AgentRequestActivity extends Activity {
    static final String BRIDGE_PACKAGE = "com.android.virtualization.terminal";
    static final String BRIDGE_PERMISSION = "com.gbos.terminal.permission.TERMINAL_BRIDGE";
    static final String EXTRA_CALLER_PACKAGE = "com.gbos.terminal.extra.CALLER_PACKAGE";
    static final String EXTRA_CALLER_UID = "com.gbos.terminal.extra.CALLER_UID";
    static final String ACTION_SHARE_DIRECTORY = "com.android.virtualization.terminal.action.SHARE_DIRECTORY";
    static final String EXTRA_GO_BACKGROUND = "com.android.virtualization.terminal.EXTRA_GO_BACKGROUND";
    /** gbos-linuxd's limits for files copied into the droid home (config.h). */
    private static final int CHUNK = 96 << 10;
    private static final long FILE_MAX = 64L << 20;
    private static final String TAG = "Terminal";

    private final ExecutorService io = Executors.newSingleThreadExecutor();
    private final Handler main = new Handler(Looper.getMainLooper());
    private String callerPackage;
    private int callerUid;
    private CharSequence callerLabel;
    private AlertDialog dialog;
    /** Share requests waiting for the user to allow access to Android files. */
    private String pendingShareFolder;
    private boolean finished;
    /** Extra show listener for the next show() (buttons that do not close the dialog). */
    private android.content.DialogInterface.OnShowListener showListener;

    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);
        if (state != null) {
            // The process was recreated: the request's context (grants, the caller's wait) is
            // gone; the caller asks again.
            cancel();
            return;
        }
        if (!fromBridge()) {
            Log.w(TAG, "request rejected: not forwarded by " + BRIDGE_PACKAGE);
            cancel();
            return;
        }
        Intent intent = getIntent();
        callerPackage = intent.getStringExtra(EXTRA_CALLER_PACKAGE);
        callerUid = intent.getIntExtra(EXTRA_CALLER_UID, -1);
        if (callerPackage == null || callerUid < 0 || !callerIdentityConsistent()) {
            Log.w(TAG, "request rejected: caller " + callerPackage + " uid " + callerUid + " does not check out");
            cancel();
            return;
        }
        callerLabel = label(callerPackage);
        if (ACTION_SHARE_DIRECTORY.equals(intent.getAction())) {
            shareRequest(intent);
        } else if (Intent.ACTION_VIEW.equals(intent.getAction())) {
            runRequest(intent);
        } else {
            cancel();
        }
    }

    @Override
    protected void onResume() {
        super.onResume();
        // Back from the "All files access" screen.
        if (pendingShareFolder != null && dialog != null && dialog.isShowing()) {
            showShare(pendingShareFolder);
        }
    }

    @Override
    protected void onDestroy() {
        if (dialog != null) dialog.dismiss();
        io.shutdown();
        super.onDestroy();
    }

    // ---- who is asking

    private boolean fromBridge() {
        if (!BRIDGE_PACKAGE.equals(getCallingPackage())) return false;
        PackageManager pm = getPackageManager();
        try {
            ApplicationInfo bridge = pm.getApplicationInfo(BRIDGE_PACKAGE, 0);
            if ((bridge.flags & ApplicationInfo.FLAG_SYSTEM) == 0) return false;
        } catch (PackageManager.NameNotFoundException e) {
            return false;
        }
        return pm.checkSignatures(BRIDGE_PACKAGE, getPackageName()) == PackageManager.SIGNATURE_MATCH;
    }

    private boolean callerIdentityConsistent() {
        if (!UserHandle.getUserHandleForUid(callerUid).equals(Process.myUserHandle())) return false;
        try {
            return getPackageManager().getPackageUid(callerPackage, 0) == callerUid;
        } catch (PackageManager.NameNotFoundException e) {
            return false;
        }
    }

    private CharSequence label(String pkg) {
        try {
            PackageManager pm = getPackageManager();
            return pm.getApplicationLabel(pm.getApplicationInfo(pkg, 0));
        } catch (PackageManager.NameNotFoundException e) {
            return pkg;
        }
    }

    private int androidUser() {
        return Process.myUid() / 100000;
    }

    // ---- Copy & Run

    private void runRequest(Intent intent) {
        Uri data = intent.getData();
        if (data == null || !"android-terminal".equals(data.getScheme()) || !"start".equals(data.getHost())) {
            cancel();
            return;
        }
        String command = data.getQueryParameter("cmd");
        if (command == null) command = "";
        String destPath = data.getQueryParameter("dest_path");
        Uri source = null;
        ClipData clip = intent.getClipData();
        if (clip != null && clip.getItemCount() > 0) source = clip.getItemAt(0).getUri();
        String rel = null;
        if (destPath != null) {
            if (source == null) {
                Log.w(TAG, "Copy & Run rejected: dest_path without a source URI");
                cancel();
                return;
            }
            try {
                rel = AgentRules.destination(destPath);
            } catch (AgentRules.Rejected e) {
                blocked(getString(R.string.request_blocked_path, destPath));
                return;
            }
        }
        if (command.isEmpty() && rel == null) {
            cancel();
            return;
        }
        if (rel != null) {
            String why = sourceProblem(source);
            if (why != null) {
                Log.w(TAG, "Copy & Run rejected: source " + source + " from uid " + callerUid + ": " + why);
                blocked(getString(R.string.request_blocked_source, callerLabel, why));
                return;
            }
        }
        String problem = AgentRules.commandProblem(command);
        if (problem != null) {
            blocked(getString(R.string.request_blocked_command, problemText(problem)) + "\n\n" + command);
            return;
        }
        boolean background = data.getBooleanQueryParameter("background", false)
                || intent.getBooleanExtra(EXTRA_GO_BACKGROUND, false);
        final String cmd = command;
        final String dest = rel;
        final Uri src = source;
        withEnvironment(() -> {
            String size = dest == null ? null : sourceSize(src);
            main.post(() -> showRun(cmd, dest, src, size, background));
        });
    }

    private String problemText(String code) {
        switch (code) {
            case "non_ascii": return getString(R.string.request_problem_non_ascii);
            case "control": return getString(R.string.request_problem_control);
            case "heredoc": return getString(R.string.request_problem_heredoc);
            case "forkbomb": return getString(R.string.request_problem_forkbomb);
            case "obfuscated": return getString(R.string.request_problem_obfuscated);
            case "function": return getString(R.string.request_problem_function);
            default: return getString(R.string.request_problem_background);
        }
    }

    /**
     * Android's terminal's SourceUriVerifier for this environment: a content URI of this user,
     * not this app's or the bridge's own, from a provider the caller owns or holds a read
     * grant on. Null when acceptable.
     */
    private String sourceProblem(Uri uri) {
        if (!"content".equalsIgnoreCase(uri.getScheme())) return "only content URIs are accepted";
        AgentRules.Authority authority;
        try {
            authority = AgentRules.authority(uri.getEncodedAuthority());
        } catch (AgentRules.Rejected e) {
            return e.reason;
        }
        if (authority.userId >= 0 && authority.userId != androidUser()) return "the URI belongs to another user";
        PackageManager pm = getPackageManager();
        ProviderInfo provider = pm.resolveContentProvider(authority.name, 0);
        if (provider == null || provider.applicationInfo == null) return "no such provider";
        int owner = provider.applicationInfo.uid;
        int bridgeUid;
        try {
            bridgeUid = pm.getPackageUid(BRIDGE_PACKAGE, 0);
        } catch (PackageManager.NameNotFoundException e) {
            bridgeUid = -1;
        }
        if (owner == Process.myUid() || owner == bridgeUid) return "the URI points into Terminal itself";
        if (owner == callerUid) return null;
        if (checkUriPermission(uri, -1, callerUid, Intent.FLAG_GRANT_READ_URI_PERMISSION)
                == PackageManager.PERMISSION_GRANTED) return null;
        return "the caller has no read permission for it";
    }

    private String sourceSize(Uri uri) {
        try (Cursor c = getContentResolver().query(uri, new String[] {OpenableColumns.SIZE}, null, null, null)) {
            if (c != null && c.moveToFirst() && !c.isNull(0)) return Formatter.formatShortFileSize(this, c.getLong(0));
        } catch (RuntimeException e) {
            Log.w(TAG, "size of " + uri + ": " + e);
        }
        return getString(R.string.request_size_unknown);
    }

    private void showRun(String command, String rel, Uri source, String size, boolean background) {
        View body = getLayoutInflater().inflate(R.layout.agent_request, null);
        ((TextView) body.findViewById(R.id.request_body)).setText(getString(R.string.request_run_body, callerLabel));
        TextView cmdView = body.findViewById(R.id.request_command);
        if (command.isEmpty()) {
            body.findViewById(R.id.request_command_label).setVisibility(View.GONE);
            cmdView.setVisibility(View.GONE);
        } else {
            cmdView.setText(command);
        }
        TextView fileView = body.findViewById(R.id.request_file);
        if (rel == null) {
            body.findViewById(R.id.request_file_label).setVisibility(View.GONE);
            fileView.setVisibility(View.GONE);
        } else {
            fileView.setText(getString(R.string.request_file_value, AgentRules.DROID_HOME + "/" + rel, callerLabel, size));
        }
        TextView note = body.findViewById(R.id.request_note);
        StringBuilder notes = new StringBuilder();
        if (!command.isEmpty()) notes.append(getString(background ? R.string.request_run_background : R.string.request_run_foreground));
        // Apps for Android's terminal keep their projects (and tools) in shared storage, which
        // Linux reaches with the Terminal's own "All files access".
        boolean files = Environment.isExternalStorageManager();
        if (!files) notes.append(notes.length() > 0 ? "\n\n" : "").append(getString(R.string.request_run_files_access));
        if (notes.length() == 0) note.setVisibility(View.GONE);
        else note.setText(notes);
        AlertDialog.Builder b = new AlertDialog.Builder(this)
                .setIcon(icon())
                .setTitle(getString(command.isEmpty() ? R.string.request_copy_title : R.string.request_run_title, callerLabel))
                .setView(body)
                .setNegativeButton(R.string.request_deny, (d, w) -> cancel())
                .setPositiveButton(command.isEmpty() ? R.string.request_copy : R.string.request_allow, null);
        if (!files) b.setNeutralButton(R.string.request_share_grant, null);
        AlertDialog d = b.create();
        if (!files) {
            // Opening the setting keeps the request open; the run itself does not depend on it.
            showListener = x -> d.getButton(AlertDialog.BUTTON_NEUTRAL).setOnClickListener(v -> startActivity(
                    new Intent(Settings.ACTION_MANAGE_APP_ALL_FILES_ACCESS_PERMISSION,
                            Uri.parse("package:" + getPackageName()))));
        }
        show(d, body, () -> carryOutRun(command, rel, source, background));
    }

    private void carryOutRun(String command, String rel, Uri source, boolean background) {
        io.execute(() -> {
            try {
                prepareDroid();
                if (rel != null) copy(source, rel);
            } catch (IOException e) {
                Log.w(TAG, "Copy & Run for " + callerPackage + ": " + e.getMessage());
                main.post(() -> failed(e.getMessage()));
                return;
            }
            main.post(() -> {
                if (!command.isEmpty()) {
                    String token = DroidRequests.put(new DroidRequests.Approved(command, callerLabel.toString()));
                    if (background) {
                        startService(new Intent(this, SessionService.class)
                                .setAction(SessionService.ACTION_RUN_DROID)
                                .putExtra(SessionService.EXTRA_TOKEN, token));
                    } else {
                        startActivity(new Intent(this, TerminalActivity.class)
                                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                                .putExtra(TerminalActivity.EXTRA_DROID_TOKEN, token));
                    }
                }
                VmStatus.publish(this);
                done(RESULT_OK);
            });
        });
    }

    /** The droid account of this Terminal user, its home and storage mounted (gbos-linuxd). */
    private void prepareDroid() throws IOException {
        for (String op : new String[] {"droid_account", "droid_prepare"}) {
            JSONObject r = DaemonClient.request(DaemonClient.op(op));
            if (!r.optBoolean("ok", false)) {
                if ("droid_taken".equals(r.optString("code"))) throw new IOException(getString(R.string.request_droid_taken));
                throw new IOException(r.optString("error", op + " failed"));
            }
        }
    }

    /** Streams the source into /home/droid/REL through gbos-linuxd, chunk by chunk. */
    private void copy(Uri source, String rel) throws IOException {
        try (InputStream in = getContentResolver().openInputStream(source);
             DaemonClient daemon = DaemonClient.connect()) {
            if (in == null) throw new IOException("the source cannot be opened");
            byte[] buf = new byte[CHUNK];
            byte[] next = new byte[CHUNK];
            int n = readFully(in, buf);
            long offset = 0;
            while (true) {
                int m = n == CHUNK ? readFully(in, next) : 0;
                boolean last = m == 0;
                if (offset + n > FILE_MAX) throw new IOException(getString(R.string.request_too_large, (int) (FILE_MAX >> 20)));
                JSONObject req = DaemonClient.op("droid_file");
                try {
                    req.put("path", rel).put("offset", offset).put("final", last).put("mode", 0644)
                            .put("data", Base64.encodeToString(buf, 0, n, Base64.NO_WRAP));
                } catch (JSONException e) {
                    throw new AssertionError(e);
                }
                daemon.send(req);
                JSONObject r = daemon.read();
                if (r == null) throw new IOException("the Linux service closed the connection");
                if (!r.optBoolean("ok", false)) throw new IOException(r.optString("error", "copy failed"));
                offset += n;
                if (last) return;
                byte[] t = buf;
                buf = next;
                next = t;
                n = m;
            }
        } catch (SecurityException e) {
            throw new IOException("no permission to read the source: " + e.getMessage());
        }
    }

    private static int readFully(InputStream in, byte[] buf) throws IOException {
        int off = 0;
        while (off < buf.length) {
            int r = in.read(buf, off, buf.length - off);
            if (r < 0) break;
            off += r;
        }
        return off;
    }

    // ---- SHARE_DIRECTORY

    private void shareRequest(Intent intent) {
        Uri tree = intent.getData();
        if (tree == null || !"content".equals(tree.getScheme())) {
            cancel();
            return;
        }
        String folder;
        try {
            folder = AgentRules.sharedFolder(tree.getAuthority(), DocumentsContract.getTreeDocumentId(tree), androidUser());
        } catch (AgentRules.Rejected e) {
            blocked(e.reason);
            return;
        } catch (IllegalArgumentException e) {
            blocked("Not a folder: " + tree);
            return;
        }
        String why = sourceProblem(tree);
        if (why != null) {
            blocked(getString(R.string.request_blocked_source, callerLabel, why));
            return;
        }
        withEnvironment(() -> main.post(() -> showShare(folder)));
    }

    private void showShare(String folder) {
        if (dialog != null) dialog.dismiss();
        boolean access = Environment.isExternalStorageManager();
        pendingShareFolder = access ? null : folder;
        View body = getLayoutInflater().inflate(R.layout.agent_request, null);
        ((TextView) body.findViewById(R.id.request_body)).setText(getString(R.string.request_share_body, callerLabel));
        body.findViewById(R.id.request_command_label).setVisibility(View.GONE);
        ((TextView) body.findViewById(R.id.request_command)).setText(folder);
        body.findViewById(R.id.request_file_label).setVisibility(View.GONE);
        body.findViewById(R.id.request_file).setVisibility(View.GONE);
        TextView note = body.findViewById(R.id.request_note);
        if (access) note.setVisibility(View.GONE);
        else note.setText(R.string.request_share_files_access);
        AlertDialog d = new AlertDialog.Builder(this)
                .setIcon(icon())
                .setTitle(getString(R.string.request_share_title, callerLabel))
                .setView(body)
                .setNegativeButton(R.string.request_deny, (x, w) -> cancel())
                .setPositiveButton(access ? R.string.request_share_allow : R.string.request_share_grant, null)
                .create();
        if (access) {
            show(d, body, this::carryOutShare);
        } else {
            d.setOnShowListener(x -> d.getButton(AlertDialog.BUTTON_POSITIVE).setOnClickListener(v -> startActivity(
                    new Intent(Settings.ACTION_MANAGE_APP_ALL_FILES_ACCESS_PERMISSION,
                            Uri.parse("package:" + getPackageName())))));
            d.setOnCancelListener(x -> cancel());
            dialog = d;
            d.show();
        }
    }

    private void carryOutShare() {
        io.execute(() -> {
            try {
                prepareDroid();
            } catch (IOException e) {
                main.post(() -> failed(e.getMessage()));
                return;
            }
            main.post(() -> {
                VmStatus.publish(this);
                done(RESULT_OK);
            });
        });
    }

    // ---- common

    /** Runs next() on the worker once Linux is installed with this user's account. */
    private void withEnvironment(Runnable next) {
        io.execute(() -> {
            JSONObject s;
            try {
                s = DaemonClient.request(DaemonClient.op("status"));
            } catch (IOException e) {
                main.post(() -> failed(getString(R.string.error_service)));
                return;
            }
            if (!s.optBoolean("installed") || s.optJSONObject("user") == null) {
                main.post(this::notInstalled);
                return;
            }
            next.run();
        });
    }

    private void notInstalled() {
        dialog = new AlertDialog.Builder(this)
                .setIcon(icon())
                .setTitle(R.string.request_not_installed_title)
                .setMessage(getString(R.string.request_not_installed_body, callerLabel))
                .setPositiveButton(R.string.request_open_terminal, (d, w) -> {
                    startActivity(new Intent(this, TerminalActivity.class).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK));
                    cancel();
                })
                .setNegativeButton(R.string.cancel, (d, w) -> cancel())
                .setOnCancelListener(d -> cancel())
                .create();
        dialog.show();
    }

    /** A request that will not run, with the reason; the caller gets RESULT_CANCELED. */
    private void blocked(String reason) {
        dialog = new AlertDialog.Builder(this)
                .setIcon(icon())
                .setTitle(getString(R.string.request_blocked_title, callerLabel))
                .setMessage(reason)
                .setPositiveButton(R.string.request_ok, (d, w) -> cancel())
                .setOnCancelListener(d -> cancel())
                .create();
        dialog.show();
    }

    private void failed(String reason) {
        if (dialog != null) dialog.dismiss();
        dialog = new AlertDialog.Builder(this)
                .setIcon(icon())
                .setTitle(getString(R.string.request_blocked_title, callerLabel))
                .setMessage(getString(R.string.request_failed, reason))
                .setPositiveButton(R.string.request_ok, (d, w) -> cancel())
                .setOnCancelListener(d -> cancel())
                .create();
        dialog.show();
    }

    /** Shows an approval dialog whose positive button starts `work` and keeps it open meanwhile. */
    private void show(AlertDialog d, View body, Runnable work) {
        d.setOnCancelListener(x -> cancel());
        android.content.DialogInterface.OnShowListener before = showListener;
        showListener = null;
        d.setOnShowListener(x -> {
            if (before != null) before.onShow(x);
            Button ok = d.getButton(AlertDialog.BUTTON_POSITIVE);
            ok.setOnClickListener(v -> {
                ok.setEnabled(false);
                d.getButton(AlertDialog.BUTTON_NEGATIVE).setEnabled(false);
                d.setCancelable(false);
                ProgressBar progress = body.findViewById(R.id.request_progress);
                progress.setVisibility(View.VISIBLE);
                progress.setContentDescription(getString(R.string.request_working));
                work.run();
            });
        });
        dialog = d;
        d.show();
    }

    private android.graphics.drawable.Drawable icon() {
        try {
            return getPackageManager().getApplicationIcon(callerPackage);
        } catch (PackageManager.NameNotFoundException | NullPointerException e) {
            return getDrawable(R.mipmap.ic_launcher);
        }
    }

    private void cancel() {
        done(RESULT_CANCELED);
    }

    private void done(int result) {
        if (finished) return;
        finished = true;
        setResult(result);
        if (dialog != null) dialog.dismiss();
        finish();
    }
}
