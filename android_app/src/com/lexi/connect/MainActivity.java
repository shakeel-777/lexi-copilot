package com.lexi.connect;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.ClipData;
import android.content.ClipboardManager;
import android.content.Context;
import android.content.Intent;
import android.database.Cursor;
import android.graphics.Bitmap;
import android.net.Uri;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.os.Vibrator;
import android.os.VibrationEffect;
import android.provider.MediaStore;
import android.provider.OpenableColumns;
import android.widget.Button;
import android.widget.EditText;
import android.widget.TextView;
import android.widget.Toast;
import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileInputStream;
import java.io.InputStream;
import java.util.ArrayList;

public class MainActivity extends Activity {
    private static final int REQ_CAMERA_SNAP = 101;
    private static final int REQ_PICK_FILES = 102;
    private static final int REQ_PICK_PHOTO = 103;

    private TextView tvHostStatus;
    private TextView tvLaptopClipPreview;
    private Button btnSnapCamera;
    private Button btnCopyLaptopClip;
    private Button btnSendPhoneClip;
    private Button btnLaunchMirror;
    private Button btnLaunchStealth;
    private Button btnPickFiles;
    private TextView btnEditHost;

    private final Handler handler = new Handler(Looper.getMainLooper());
    private String latestLaptopClip = "";

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_main);

        tvHostStatus = findViewById(R.id.tv_host_status);
        tvLaptopClipPreview = findViewById(R.id.tv_laptop_clipboard_preview);
        btnSnapCamera = findViewById(R.id.btn_snap_camera);
        btnCopyLaptopClip = findViewById(R.id.btn_copy_laptop_clip);
        btnSendPhoneClip = findViewById(R.id.btn_send_phone_clip);
        btnLaunchMirror = findViewById(R.id.btn_launch_mirror);
        btnLaunchStealth = findViewById(R.id.btn_launch_stealth);
        btnPickFiles = findViewById(R.id.btn_pick_files);
        btnEditHost = findViewById(R.id.btn_edit_host);

        initListeners();
    }

    @Override
    protected void onResume() {
        super.onResume();
        refreshTelemetry();
        refreshClipboardPreview();
    }

    private void initListeners() {
        btnEditHost.setOnClickListener(v -> showEditHostDialog());

        // 1. Continuity Camera Snap (Tap = Camera, Long-press = Gallery / Screenshot)
        btnSnapCamera.setOnClickListener(v -> {
            vibrate(20);
            Intent takePictureIntent = new Intent(MediaStore.ACTION_IMAGE_CAPTURE);
            File snapFile = new File(getCacheDir(), "continuity_snap.jpg");
            if (snapFile.exists()) snapFile.delete();
            Uri photoUri = Uri.parse("content://" + LexiFileProvider.AUTHORITY + "/continuity_snap.jpg");
            takePictureIntent.putExtra(MediaStore.EXTRA_OUTPUT, photoUri);
            takePictureIntent.addFlags(Intent.FLAG_GRANT_WRITE_URI_PERMISSION | Intent.FLAG_GRANT_READ_URI_PERMISSION);
            try {
                startActivityForResult(takePictureIntent, REQ_CAMERA_SNAP);
            } catch (Exception e) {
                try {
                    startActivityForResult(new Intent(MediaStore.ACTION_IMAGE_CAPTURE), REQ_CAMERA_SNAP);
                } catch (Exception ex) {
                    Toast.makeText(this, "Camera error: " + ex.getMessage(), Toast.LENGTH_SHORT).show();
                }
            }
        });

        btnSnapCamera.setOnLongClickListener(v -> {
            vibrate(30);
            Toast.makeText(this, "Select existing photo/screenshot...", Toast.LENGTH_SHORT).show();
            Intent pickPhoto = new Intent(Intent.ACTION_GET_CONTENT);
            pickPhoto.setType("image/*");
            startActivityForResult(Intent.createChooser(pickPhoto, "Choose Photo for Lexi Vision"), REQ_PICK_PHOTO);
            return true;
        });

        // 2. Universal Clipboard Sync
        btnCopyLaptopClip.setOnClickListener(v -> {
            vibrate(20);
            if (latestLaptopClip != null && !latestLaptopClip.isEmpty()) {
                ClipboardManager cm = (ClipboardManager) getSystemService(Context.CLIPBOARD_SERVICE);
                if (cm != null) {
                    cm.setPrimaryClip(ClipData.newPlainText("Lexi Laptop", latestLaptopClip));
                    Toast.makeText(this, "✓ Copied to Phone Clipboard!", Toast.LENGTH_SHORT).show();
                }
            } else {
                Toast.makeText(this, "No laptop clipboard available yet", Toast.LENGTH_SHORT).show();
            }
        });

        btnSendPhoneClip.setOnClickListener(v -> {
            vibrate(20);
            ClipboardManager cm = (ClipboardManager) getSystemService(Context.CLIPBOARD_SERVICE);
            if (cm != null && cm.hasPrimaryClip() && cm.getPrimaryClip().getItemCount() > 0) {
                CharSequence text = cm.getPrimaryClip().getItemAt(0).getText();
                if (text != null && text.length() > 0) {
                    final String str = text.toString();
                    new Thread(() -> {
                        boolean ok = LexiApi.sendClipboardText(this, str);
                        runOnUiThread(() -> {
                            if (ok) {
                                Toast.makeText(this, "✓ Sent to Linux Clipboard (Ctrl+V)!", Toast.LENGTH_SHORT).show();
                            } else {
                                Toast.makeText(this, "⚠️ Failed to reach laptop", Toast.LENGTH_SHORT).show();
                            }
                        });
                    }).start();
                    return;
                }
            }
            Toast.makeText(this, "Phone clipboard is empty", Toast.LENGTH_SHORT).show();
        });

        // 3. Screen Mirroring Triggers
        btnLaunchMirror.setOnClickListener(v -> {
            vibrate(20);
            Toast.makeText(this, "🚀 Starting 60 FPS mirror on laptop display...", Toast.LENGTH_SHORT).show();
            new Thread(() -> {
                boolean ok = LexiApi.triggerMirror(this, false);
                runOnUiThread(() -> {
                    if (ok) {
                        vibrate(40);
                        Toast.makeText(this, "📱 60 FPS Mirror active on laptop!", Toast.LENGTH_SHORT).show();
                    } else {
                        Toast.makeText(this, "⚠️ Failed to trigger mirror on laptop", Toast.LENGTH_SHORT).show();
                    }
                });
            }).start();
        });

        btnLaunchStealth.setOnClickListener(v -> {
            vibrate(20);
            Toast.makeText(this, "🌙 Starting stealth mirror (Screen Off)...", Toast.LENGTH_SHORT).show();
            new Thread(() -> {
                boolean ok = LexiApi.triggerMirror(this, true);
                runOnUiThread(() -> {
                    if (ok) {
                        vibrate(40);
                        Toast.makeText(this, "🌙 Stealth mirror active on laptop!", Toast.LENGTH_SHORT).show();
                    } else {
                        Toast.makeText(this, "⚠️ Failed to trigger stealth mirror", Toast.LENGTH_SHORT).show();
                    }
                });
            }).start();
        });

        // 4. File Picker (AirDrop)
        btnPickFiles.setOnClickListener(v -> {
            vibrate(20);
            Intent intent = new Intent(Intent.ACTION_GET_CONTENT);
            intent.setType("*/*");
            intent.putExtra(Intent.EXTRA_ALLOW_MULTIPLE, true);
            startActivityForResult(Intent.createChooser(intent, "Select files to beam to Lexi"), REQ_PICK_FILES);
        });
    }

    private void refreshTelemetry() {
        new Thread(() -> {
            int ping = LexiApi.pingHost(this);
            runOnUiThread(() -> {
                String host = LexiApi.getHost(this);
                int port = LexiApi.getPort(this);
                if (ping >= 0) {
                    tvHostStatus.setText("● Connected to " + host + ":" + port + " (" + ping + "ms LAN)");
                    tvHostStatus.setTextColor(getResources().getColor(R.color.accent_emerald));
                } else {
                    tvHostStatus.setText("○ Offline · " + host + ":" + port);
                    tvHostStatus.setTextColor(getResources().getColor(R.color.text_dim));
                }
            });
        }).start();
    }

    private void refreshClipboardPreview() {
        new Thread(() -> {
            String clip = LexiApi.fetchClipboardText(this);
            if (clip != null && !clip.isEmpty()) {
                latestLaptopClip = clip;
                runOnUiThread(() -> {
                    String preview = clip.replace("\n", " ");
                    if (preview.length() > 60) preview = preview.substring(0, 60) + "…";
                    tvLaptopClipPreview.setText("From Laptop: " + preview);
                    tvLaptopClipPreview.setTextColor(getResources().getColor(R.color.accent_cyan));
                });
            }
        }).start();
    }

    private void showEditHostDialog() {
        AlertDialog.Builder builder = new AlertDialog.Builder(this);
        builder.setTitle("Configure Lexi Laptop IP");
        final EditText input = new EditText(this);
        input.setText(LexiApi.getHost(this));
        input.setSingleLine();
        input.setTextColor(getResources().getColor(R.color.text_main));
        builder.setView(input);

        builder.setPositiveButton("Save", (dialog, which) -> {
            String newHost = input.getText().toString().trim();
            if (!newHost.isEmpty()) {
                LexiApi.setHost(this, newHost);
                refreshTelemetry();
                refreshClipboardPreview();
            }
        });
        builder.setNegativeButton("Cancel", null);
        builder.show();
    }

    @Override
    protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        super.onActivityResult(requestCode, resultCode, data);

        if (requestCode == REQ_CAMERA_SNAP && resultCode == RESULT_OK) {
            File snapFile = new File(getCacheDir(), "continuity_snap.jpg");
            if (snapFile.exists() && snapFile.length() > 0) {
                try {
                    byte[] bytes = new byte[(int) snapFile.length()];
                    FileInputStream fis = new FileInputStream(snapFile);
                    int read = fis.read(bytes);
                    fis.close();
                    if (read > 0) {
                        uploadSnappedBytes(bytes);
                        return;
                    }
                } catch (Exception ignored) {}
            }
            if (data != null && data.getExtras() != null) {
                Bitmap bmp = (Bitmap) data.getExtras().get("data");
                if (bmp != null) {
                    ByteArrayOutputStream stream = new ByteArrayOutputStream();
                    bmp.compress(Bitmap.CompressFormat.JPEG, 90, stream);
                    uploadSnappedBytes(stream.toByteArray());
                }
            }
        } else if (requestCode == REQ_PICK_PHOTO && resultCode == RESULT_OK && data != null) {
            Uri imageUri = data.getData();
            if (imageUri != null) {
                try {
                    InputStream in = getContentResolver().openInputStream(imageUri);
                    if (in != null) {
                        ByteArrayOutputStream baos = new ByteArrayOutputStream();
                        byte[] buf = new byte[16384];
                        int n;
                        while ((n = in.read(buf)) > 0) baos.write(buf, 0, n);
                        in.close();
                        uploadSnappedBytes(baos.toByteArray());
                    }
                } catch (Exception e) {
                    Toast.makeText(this, "Failed to read image: " + e.getMessage(), Toast.LENGTH_SHORT).show();
                }
            }
        } else if (requestCode == REQ_PICK_FILES && resultCode == RESULT_OK && data != null) {
            ArrayList<Uri> uris = new ArrayList<>();
            if (data.getClipData() != null) {
                int count = data.getClipData().getItemCount();
                for (int i = 0; i < count; i++) {
                    uris.add(data.getClipData().getItemAt(i).getUri());
                }
            } else if (data.getData() != null) {
                uris.add(data.getData());
            }
            if (!uris.isEmpty()) {
                Intent shareIntent = new Intent(this, ShareActivity.class);
                shareIntent.setAction(Intent.ACTION_SEND_MULTIPLE);
                shareIntent.setType("*/*");
                shareIntent.putParcelableArrayListExtra(Intent.EXTRA_STREAM, uris);
                startActivity(shareIntent);
            }
        }
    }

    private void uploadSnappedBytes(byte[] byteArray) {
        Toast.makeText(this, "📸 Beaming photo to Lexi Vision Tray...", Toast.LENGTH_SHORT).show();
        btnSnapCamera.setEnabled(false);
        btnSnapCamera.setText("⏳ Uploading to Lexi...");

        new Thread(() -> {
            boolean ok = LexiApi.uploadCameraSnap(this, byteArray);
            runOnUiThread(() -> {
                btnSnapCamera.setEnabled(true);
                btnSnapCamera.setText("📸 Snap Photo to Lexi");
                if (ok) {
                    vibrate(40);
                    Toast.makeText(this, "✓ Attached to Lexi Vision Tray on laptop!", Toast.LENGTH_LONG).show();
                } else {
                    Toast.makeText(this, "⚠️ Failed to beam image to " + LexiApi.getHost(this), Toast.LENGTH_LONG).show();
                }
            });
        }).start();
    }

    private void vibrate(long ms) {
        try {
            Vibrator v = (Vibrator) getSystemService(Context.VIBRATOR_SERVICE);
            if (v != null && v.hasVibrator()) {
                if (android.os.Build.VERSION.SDK_INT >= android.os.Build.VERSION_CODES.O) {
                    v.vibrate(VibrationEffect.createOneShot(ms, VibrationEffect.DEFAULT_AMPLITUDE));
                } else {
                    v.vibrate(ms);
                }
            }
        } catch (Exception ignored) {}
    }
}
