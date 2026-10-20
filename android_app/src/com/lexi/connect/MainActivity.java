package com.lexi.connect;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.ClipData;
import android.content.ClipboardManager;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.database.Cursor;
import android.graphics.Bitmap;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.os.Vibrator;
import android.os.VibrationEffect;
import android.provider.MediaStore;
import android.provider.OpenableColumns;
import android.provider.Settings;
import android.view.View;
import android.widget.Button;
import android.widget.EditText;
import android.widget.ImageView;
import android.widget.Switch;
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
    private View viewStatusDot;
    private TextView tvLaptopClipPreview;
    private ImageView ivCameraIris;
    private Button btnSnapCamera;
    private Button btnCopyLaptopClip;
    private Button btnSendPhoneClip;
    private Button btnLaunchMirror;
    private Switch swStealthMode;
    private Switch swDynamicIsland;
    private Button btnPickFiles;
    private View btnEditHost;
    private TextView tvBeamTelemetry;

    private final Handler handler = new Handler(Looper.getMainLooper());
    private String latestLaptopClip = "";

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_main);

        tvHostStatus = findViewById(R.id.tv_host_status);
        viewStatusDot = findViewById(R.id.view_status_dot);
        tvLaptopClipPreview = findViewById(R.id.tv_laptop_clip_preview);
        ivCameraIris = findViewById(R.id.iv_camera_iris);
        btnSnapCamera = findViewById(R.id.btn_snap_camera);
        btnCopyLaptopClip = findViewById(R.id.btn_copy_laptop_clip);
        btnSendPhoneClip = findViewById(R.id.btn_send_phone_clip);
        btnLaunchMirror = findViewById(R.id.btn_launch_mirror);
        swStealthMode = findViewById(R.id.sw_stealth_mode);
        swDynamicIsland = findViewById(R.id.sw_dynamic_island);
        btnPickFiles = findViewById(R.id.btn_pick_files);
        btnEditHost = findViewById(R.id.btn_edit_host);
        tvBeamTelemetry = findViewById(R.id.tv_beam_telemetry);

        initListeners();
    }

    @Override
    protected void onResume() {
        super.onResume();
        refreshTelemetry();
        refreshClipboardPreview();

        Intent intent = getIntent();
        if (intent != null && intent.getStringExtra("action") != null) {
            String act = intent.getStringExtra("action");
            intent.removeExtra("action");
            if ("snap".equals(act)) {
                handler.postDelayed(() -> btnSnapCamera.performClick(), 250);
            } else if ("beam".equals(act)) {
                handler.postDelayed(() -> btnPickFiles.performClick(), 250);
            }
        }
    }

    private void initListeners() {
        btnEditHost.setOnClickListener(v -> showEditHostDialog());

        // Dynamic Island Overlay Toggle
        if (swDynamicIsland != null) {
            SharedPreferences prefs = getSharedPreferences("lexi_island_prefs", MODE_PRIVATE);
            boolean islandEnabled = prefs.getBoolean("island_enabled", false);
            swDynamicIsland.setChecked(islandEnabled);

            swDynamicIsland.setOnCheckedChangeListener((buttonView, isChecked) -> {
                vibrate(20);
                if (isChecked) {
                    if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M && !Settings.canDrawOverlays(this)) {
                        Toast.makeText(this, "Enable 'Display over other apps' for Lexi Island", Toast.LENGTH_LONG).show();
                        Intent overlayIntent = new Intent(Settings.ACTION_MANAGE_OVERLAY_PERMISSION,
                                Uri.parse("package:" + getPackageName()));
                        startActivity(overlayIntent);
                        swDynamicIsland.setChecked(false);
                        return;
                    }
                    prefs.edit().putBoolean("island_enabled", true).apply();
                    startService(new Intent(this, DynamicIslandService.class));
                    Toast.makeText(this, "🏝️ Lexi Dynamic Island activated!", Toast.LENGTH_SHORT).show();
                } else {
                    prefs.edit().putBoolean("island_enabled", false).apply();
                    stopService(new Intent(this, DynamicIslandService.class));
                    Toast.makeText(this, "Dynamic Island deactivated", Toast.LENGTH_SHORT).show();
                }
            });

            if (islandEnabled) {
                if (Build.VERSION.SDK_INT < Build.VERSION_CODES.M || Settings.canDrawOverlays(this)) {
                    startService(new Intent(this, DynamicIslandService.class));
                }
            }
        }

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

        // Tap on Camera Iris
        if (ivCameraIris != null) {
            ivCameraIris.setOnClickListener(v -> btnSnapCamera.performClick());
        }

        // 3. Screen Mirroring Trigger with Stealth Switch
        btnLaunchMirror.setOnClickListener(v -> {
            vibrate(25);
            boolean stealth = swStealthMode != null && swStealthMode.isChecked();
            String msg = stealth ? "🌙 Starting stealth mirror (Screen Off)..." : "🚀 Starting 60 FPS mirror on laptop display...";
            Toast.makeText(this, msg, Toast.LENGTH_SHORT).show();
            new Thread(() -> {
                boolean ok = LexiApi.triggerMirror(this, stealth);
                runOnUiThread(() -> {
                    if (ok) {
                        vibrate(40);
                        String activeMsg = stealth ? "🌙 Stealth mirror active on laptop!" : "📱 60 FPS Mirror active on laptop!";
                        Toast.makeText(this, activeMsg, Toast.LENGTH_SHORT).show();
                    } else {
                        Toast.makeText(this, "⚠️ Failed to trigger mirror on laptop", Toast.LENGTH_SHORT).show();
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

        if (tvBeamTelemetry != null) {
            tvBeamTelemetry.setOnClickListener(v -> {
                vibrate(20);
                showDeviceSelectDialog();
            });
        }
    }

    private void refreshTelemetry() {
        new Thread(() -> {
            int ping = LexiApi.pingHost(this);
            runOnUiThread(() -> {
                String host = LexiApi.getHost(this);
                int port = LexiApi.getPort(this);
                if (ping >= 0) {
                    if (viewStatusDot != null) viewStatusDot.setVisibility(View.VISIBLE);
                    tvHostStatus.setText("CONNECTED · ARCH LINUX (" + ping + "ms LAN)");
                    tvHostStatus.setTextColor(getResources().getColor(R.color.accent_emerald));
                    if (tvBeamTelemetry != null) {
                        tvBeamTelemetry.setText("⚡ Target: 💻 " + host + ":" + port + " · Tap to Switch");
                        tvBeamTelemetry.setTextColor(getResources().getColor(R.color.accent_cyan));
                    }
                } else {
                    if (viewStatusDot != null) viewStatusDot.setVisibility(View.GONE);
                    tvHostStatus.setText("OFFLINE · " + host + ":" + port);
                    tvHostStatus.setTextColor(getResources().getColor(R.color.text_dim));
                    if (tvBeamTelemetry != null) {
                        tvBeamTelemetry.setText("⚠️ Offline · Tap to Configure Target");
                        tvBeamTelemetry.setTextColor(getResources().getColor(R.color.text_muted));
                    }
                }
            });
        }).start();
    }

    private void showDeviceSelectDialog() {
        new Thread(() -> {
            String peersJson = LexiApi.fetchPeersJson(this);
            final ArrayList<String> deviceLabels = new ArrayList<>();
            final ArrayList<String> deviceIps = new ArrayList<>();

            String currentHost = LexiApi.getHost(this);
            deviceLabels.add("💻 Arch Linux (" + currentHost + ") [Active]");
            deviceIps.add(currentHost);

            if (peersJson != null && peersJson.contains("\"peers\"")) {
                try {
                    org.json.JSONObject obj = new org.json.JSONObject(peersJson);
                    org.json.JSONArray arr = obj.optJSONArray("peers");
                    if (arr != null) {
                        for (int i = 0; i < arr.length(); i++) {
                            org.json.JSONObject p = arr.getJSONObject(i);
                            String ip = p.optString("ip");
                            String dev = p.optString("device", "Lexi Peer");
                            String type = p.optString("type", "laptop");
                            String icon = "phone".equals(type) ? "📱" : "💻";
                            if (!ip.isEmpty() && !deviceIps.contains(ip)) {
                                deviceLabels.add(icon + " " + dev + " (" + ip + ")");
                                deviceIps.add(ip);
                            }
                        }
                    }
                } catch (Exception ignored) {}
            }

            runOnUiThread(() -> {
                AlertDialog.Builder b = new AlertDialog.Builder(this);
                b.setTitle("⚡ Select Lexi Target Device");
                String[] items = deviceLabels.toArray(new String[0]);
                b.setItems(items, (dialog, which) -> {
                    String selectedIp = deviceIps.get(which);
                    LexiApi.setHost(this, selectedIp);
                    Toast.makeText(this, "Target set to " + selectedIp, Toast.LENGTH_SHORT).show();
                    refreshTelemetry();
                });
                b.setNegativeButton("Manual IP...", (dialog, which) -> showEditHostDialog());
                b.show();
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
