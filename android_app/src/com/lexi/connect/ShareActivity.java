package com.lexi.connect;

import android.app.Activity;
import android.content.Context;
import android.content.Intent;
import android.database.Cursor;
import android.net.Uri;
import android.os.Bundle;
import android.os.Vibrator;
import android.os.VibrationEffect;
import android.provider.OpenableColumns;
import android.view.View;
import android.widget.ProgressBar;
import android.widget.TextView;
import java.io.InputStream;
import java.util.ArrayList;

public class ShareActivity extends Activity {
    private TextView tvTitle;
    private TextView tvDetails;
    private TextView tvSpeed;
    private ProgressBar pbProgress;
    private volatile boolean cancelled = false;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_share);

        tvTitle = findViewById(R.id.tv_share_title);
        tvDetails = findViewById(R.id.tv_share_details);
        tvSpeed = findViewById(R.id.tv_share_speed);
        pbProgress = findViewById(R.id.pb_share_progress);

        findViewById(R.id.btn_share_close).setOnClickListener(v -> {
            cancelled = true;
            finish();
        });

        handleIncomingIntent();
    }

    private void handleIncomingIntent() {
        Intent intent = getIntent();
        String action = intent.getAction();
        String type = intent.getType();

        if (Intent.ACTION_SEND.equals(action) && type != null) {
            if ("text/plain".equals(type) && intent.getStringExtra(Intent.EXTRA_TEXT) != null) {
                handleSharedText(intent.getStringExtra(Intent.EXTRA_TEXT));
            } else {
                Uri streamUri = intent.getParcelableExtra(Intent.EXTRA_STREAM);
                if (streamUri != null) {
                    ArrayList<Uri> list = new ArrayList<>();
                    list.add(streamUri);
                    handleSharedUris(list);
                } else {
                    finish();
                }
            }
        } else if (Intent.ACTION_SEND_MULTIPLE.equals(action) && type != null) {
            ArrayList<Uri> streamUris = intent.getParcelableArrayListExtra(Intent.EXTRA_STREAM);
            if (streamUris != null && !streamUris.isEmpty()) {
                handleSharedUris(streamUris);
            } else {
                finish();
            }
        } else {
            finish();
        }
    }

    private void handleSharedText(String text) {
        tvTitle.setText("⚡ Syncing Text to Laptop...");
        tvDetails.setText(text.length() > 60 ? text.substring(0, 60) + "…" : text);
        pbProgress.setIndeterminate(true);

        new Thread(() -> {
            boolean ok = LexiApi.sendClipboardText(this, text);
            runOnUiThread(() -> {
                if (ok) {
                    tvTitle.setText("✓ Copied to Linux Clipboard!");
                    tvSpeed.setText("Injected into desktop clipboard (Ctrl+V)");
                    vibrateFeedback();
                    tvTitle.postDelayed(this::finish, 1400);
                } else {
                    tvTitle.setText("⚠️ Failed to reach laptop");
                    tvDetails.setText("Host: " + LexiApi.getHost(this) + ":" + LexiApi.getPort(this));
                    tvTitle.postDelayed(this::finish, 2500);
                }
            });
        }).start();
    }

    private void handleSharedUris(ArrayList<Uri> uris) {
        new Thread(() -> {
            int total = uris.size();
            int current = 0;

            for (Uri uri : uris) {
                if (cancelled) break;
                current++;
                final int idx = current;

                String filename = getFileName(uri);
                long fileSize = getFileSize(uri);

                runOnUiThread(() -> {
                    tvTitle.setText("⚡ Beaming " + idx + " of " + total + " to Lexi...");
                    tvDetails.setText(filename + " (" + formatSize(fileSize) + ")");
                    pbProgress.setIndeterminate(fileSize <= 0);
                    pbProgress.setProgress(0);
                });

                try {
                    InputStream in = getContentResolver().openInputStream(uri);
                    if (in != null) {
                        boolean ok = LexiApi.uploadStream(this, in, filename, fileSize, (transferred, tot, speedMbps) -> {
                            if (tot > 0) {
                                int pct = (int) ((transferred * 100) / tot);
                                runOnUiThread(() -> {
                                    pbProgress.setIndeterminate(false);
                                    pbProgress.setProgress(pct);
                                    tvSpeed.setText(String.format("%.1f MB/s · Zero-Copy Kernel Stream", speedMbps));
                                });
                            }
                        });
                        in.close();
                        if (!ok) {
                            runOnUiThread(() -> {
                                tvTitle.setText("⚠️ Upload failed");
                                tvDetails.setText("Check laptop Wi-Fi on " + LexiApi.getHost(this));
                            });
                            try { Thread.sleep(2000); } catch (Exception ignored) {}
                            break;
                        }
                    }
                } catch (Exception e) {
                    e.printStackTrace();
                }
            }

            runOnUiThread(() -> {
                if (!cancelled) {
                    tvTitle.setText("✓ Beamed to ~/Downloads!");
                    tvSpeed.setText("Transfer complete · Dispatched to Linux Island");
                    vibrateFeedback();
                    tvTitle.postDelayed(this::finish, 1400);
                }
            });
        }).start();
    }

    private String getFileName(Uri uri) {
        String result = null;
        if ("content".equals(uri.getScheme())) {
            Cursor cursor = getContentResolver().query(uri, null, null, null, null);
            try {
                if (cursor != null && cursor.moveToFirst()) {
                    int nameIdx = cursor.getColumnIndex(OpenableColumns.DISPLAY_NAME);
                    if (nameIdx != -1) result = cursor.getString(nameIdx);
                }
            } finally {
                if (cursor != null) cursor.close();
            }
        }
        if (result == null) {
            result = uri.getLastPathSegment();
        }
        return result != null ? result : "share_" + System.currentTimeMillis();
    }

    private long getFileSize(Uri uri) {
        long size = 0;
        if ("content".equals(uri.getScheme())) {
            Cursor cursor = getContentResolver().query(uri, null, null, null, null);
            try {
                if (cursor != null && cursor.moveToFirst()) {
                    int sizeIdx = cursor.getColumnIndex(OpenableColumns.SIZE);
                    if (sizeIdx != -1) size = cursor.getLong(sizeIdx);
                }
            } finally {
                if (cursor != null) cursor.close();
            }
        }
        return size;
    }

    private String formatSize(long bytes) {
        if (bytes <= 0) return "0 B";
        final String[] units = new String[] { "B", "KB", "MB", "GB", "TB" };
        int digitGroups = (int) (Math.log10(bytes) / Math.log10(1024));
        return String.format("%.1f %s", bytes / Math.pow(1024, digitGroups), units[digitGroups]);
    }

    private void vibrateFeedback() {
        try {
            Vibrator v = (Vibrator) getSystemService(Context.VIBRATOR_SERVICE);
            if (v != null && v.hasVibrator()) {
                if (android.os.Build.VERSION.SDK_INT >= android.os.Build.VERSION_CODES.O) {
                    v.vibrate(VibrationEffect.createOneShot(35, VibrationEffect.DEFAULT_AMPLITUDE));
                } else {
                    v.vibrate(35);
                }
            }
        } catch (Exception ignored) {}
    }
}
