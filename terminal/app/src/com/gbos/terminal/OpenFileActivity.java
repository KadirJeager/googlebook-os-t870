package com.gbos.terminal;

import android.app.Activity;
import android.content.ActivityNotFoundException;
import android.content.Intent;
import android.os.Bundle;
import android.widget.Toast;

import java.io.File;

/**
 * xdg-open of a file in Linux: gbos-linuxd starts this (as root, the activity is not exported)
 * with the file's Android path; the file is handed to the app that views its type.
 */
public final class OpenFileActivity extends Activity {
    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);
        File file = FilesProvider.allowed(getIntent().getStringExtra("path"));
        if (file == null || !file.isFile() || !file.canRead()) {
            Toast.makeText(this, R.string.open_failed, Toast.LENGTH_SHORT).show();
            finish();
            return;
        }
        Intent view = new Intent(Intent.ACTION_VIEW)
                .setDataAndType(FilesProvider.uriFor(file), FilesProvider.mimeType(file))
                .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION | Intent.FLAG_ACTIVITY_NEW_TASK);
        try {
            startActivity(view);
        } catch (ActivityNotFoundException e) {
            Toast.makeText(this, R.string.open_no_app, Toast.LENGTH_SHORT).show();
        }
        finish();
    }
}
