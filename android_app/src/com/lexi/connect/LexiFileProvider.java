package com.lexi.connect;

import android.content.ContentProvider;
import android.content.ContentValues;
import android.database.Cursor;
import android.database.MatrixCursor;
import android.net.Uri;
import android.os.ParcelFileDescriptor;
import android.provider.OpenableColumns;

import java.io.File;
import java.io.FileNotFoundException;

/**
 * Minimal zero-dependency FileProvider for full-resolution camera captures.
 */
public class LexiFileProvider extends ContentProvider {

    public static final String AUTHORITY = "com.lexi.connect.provider";

    @Override
    public boolean onCreate() {
        return true;
    }

    @Override
    public ParcelFileDescriptor openFile(Uri uri, String mode) throws FileNotFoundException {
        if (getContext() == null) {
            throw new FileNotFoundException("Context is null");
        }
        String fileName = uri.getLastPathSegment();
        if (fileName == null || fileName.isEmpty()) {
            fileName = "capture.jpg";
        }
        File cacheDir = getContext().getCacheDir();
        File file = new File(cacheDir, fileName);
        return ParcelFileDescriptor.open(file, ParcelFileDescriptor.MODE_READ_WRITE | ParcelFileDescriptor.MODE_CREATE);
    }

    @Override
    public Cursor query(Uri uri, String[] projection, String selection, String[] selectionArgs, String sortOrder) {
        if (projection == null) {
            projection = new String[]{OpenableColumns.DISPLAY_NAME, OpenableColumns.SIZE};
        }
        MatrixCursor cursor = new MatrixCursor(projection, 1);
        if (getContext() != null) {
            String fileName = uri.getLastPathSegment();
            File file = new File(getContext().getCacheDir(), fileName != null ? fileName : "capture.jpg");
            Object[] row = new Object[projection.length];
            for (int i = 0; i < projection.length; i++) {
                if (OpenableColumns.DISPLAY_NAME.equals(projection[i])) {
                    row[i] = file.getName();
                } else if (OpenableColumns.SIZE.equals(projection[i])) {
                    row[i] = file.exists() ? file.length() : 0L;
                } else {
                    row[i] = null;
                }
            }
            cursor.addRow(row);
        }
        return cursor;
    }

    @Override
    public String getType(Uri uri) {
        return "image/jpeg";
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
