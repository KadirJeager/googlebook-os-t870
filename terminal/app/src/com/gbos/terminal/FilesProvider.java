package com.gbos.terminal;

import android.content.ContentProvider;
import android.content.ContentValues;
import android.database.Cursor;
import android.database.MatrixCursor;
import android.net.Uri;
import android.os.ParcelFileDescriptor;
import android.os.Process;
import android.provider.OpenableColumns;
import android.webkit.MimeTypeMap;

import java.io.File;
import java.io.FileNotFoundException;
import java.io.IOException;
import java.util.Locale;

/**
 * Read-only content:// access to files that xdg-open in Linux asked Android to open. Only paths
 * of this Android user's Linux world are served: the Linux root filesystem, this user's Linux
 * home and this user's shared storage. The caller gets access through a one-time URI grant.
 */
public final class FilesProvider extends ContentProvider {
    static final String AUTHORITY = "com.gbos.terminal.files";

    static Uri uriFor(File file) {
        return new Uri.Builder().scheme("content").authority(AUTHORITY).path(file.getAbsolutePath()).build();
    }

    /** The canonical file if it lies in an allowed root, else null. */
    static File allowed(String path) {
        if (path == null || !path.startsWith("/")) return null;
        File f;
        try {
            f = new File(path).getCanonicalFile();
        } catch (IOException e) {
            return null;
        }
        int user = Process.myUid() / 100000;  // Android user id (UserHandle.PER_USER_RANGE)
        String p = f.getPath();
        String[] roots = {
                "/data/gbos-linux/rootfs/",
                "/data/misc_ce/" + user + "/gbos-linux/home/",
                "/storage/emulated/" + user + "/",
        };
        for (String root : roots) {
            if (p.startsWith(root)) return f;
        }
        return null;
    }

    static String mimeType(File f) {
        String name = f.getName();
        int dot = name.lastIndexOf('.');
        if (dot >= 0) {
            String mime = MimeTypeMap.getSingleton().getMimeTypeFromExtension(name.substring(dot + 1).toLowerCase(Locale.ROOT));
            if (mime != null) return mime;
        }
        // Extension-less files in a Linux system are overwhelmingly text (README, LICENSE, scripts).
        return dot < 0 ? "text/plain" : "application/octet-stream";
    }

    private static File fileOf(Uri uri) throws FileNotFoundException {
        File f = allowed(uri.getPath());
        if (f == null || !f.isFile()) throw new FileNotFoundException(String.valueOf(uri));
        return f;
    }

    @Override
    public boolean onCreate() {
        return true;
    }

    @Override
    public String getType(Uri uri) {
        File f = allowed(uri.getPath());
        return f == null ? null : mimeType(f);
    }

    @Override
    public ParcelFileDescriptor openFile(Uri uri, String mode) throws FileNotFoundException {
        if (!"r".equals(mode)) throw new SecurityException("read-only");
        return ParcelFileDescriptor.open(fileOf(uri), ParcelFileDescriptor.MODE_READ_ONLY);
    }

    @Override
    public Cursor query(Uri uri, String[] projection, String selection, String[] args, String sort) {
        File f;
        try {
            f = fileOf(uri);
        } catch (FileNotFoundException e) {
            return null;
        }
        String[] cols = projection != null ? projection : new String[] {OpenableColumns.DISPLAY_NAME, OpenableColumns.SIZE};
        MatrixCursor c = new MatrixCursor(cols, 1);
        Object[] row = new Object[cols.length];
        for (int i = 0; i < cols.length; i++) {
            if (OpenableColumns.DISPLAY_NAME.equals(cols[i])) row[i] = f.getName();
            else if (OpenableColumns.SIZE.equals(cols[i])) row[i] = f.length();
        }
        c.addRow(row);
        return c;
    }

    @Override
    public Uri insert(Uri uri, ContentValues values) {
        throw new UnsupportedOperationException();
    }

    @Override
    public int delete(Uri uri, String selection, String[] args) {
        throw new UnsupportedOperationException();
    }

    @Override
    public int update(Uri uri, ContentValues values, String selection, String[] args) {
        throw new UnsupportedOperationException();
    }
}
