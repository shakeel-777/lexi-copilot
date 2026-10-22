package com.lexi.connect;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.ClipData;
import android.content.ClipboardManager;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.graphics.PixelFormat;
import android.net.wifi.WifiManager;
import android.os.Build;
import android.os.Handler;
import android.os.IBinder;
import android.os.Looper;
import android.os.PowerManager;
import android.os.VibrationEffect;
import android.os.Vibrator;
import android.view.Gravity;
import android.view.LayoutInflater;
import android.view.MotionEvent;
import android.view.View;
import android.view.WindowManager;
import android.widget.Button;
import android.widget.LinearLayout;
import android.widget.TextView;
import android.widget.Toast;

public class DynamicIslandService extends Service implements View.OnTouchListener {
    private static final String CHANNEL_ID = "lexi_dynamic_island_channel";
    private static final int NOTIF_ID = 9901;
    private static final String PREFS_NAME = "lexi_island_prefs";

    private WindowManager windowManager;
    private View islandRoot;
    private LinearLayout llCompact;
    private LinearLayout llExpanded;
    private View viewCompactDot;
    private View viewExpandedDot;
    private TextView tvCompactStatus;
    private TextView tvExpandedTitle;

    private WindowManager.LayoutParams params;
    private final Handler handler = new Handler(Looper.getMainLooper());
    private boolean isExpanded = false;
    private WifiManager.WifiLock wifiLock;
    private PowerManager.WakeLock wakeLock;

    // Drag touch state
    private int initialX;
    private int initialY;
    private float initialTouchX;
    private float initialTouchY;
    private long touchStartTime;

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        if (intent != null) {
            String act = intent.getStringExtra("action");
            if ("expand".equals(act)) {
                handler.post(this::expandIsland);
            } else if ("collapse".equals(act)) {
                handler.post(this::collapseIsland);
            } else if ("beam_alert".equals(act)) {
                String fname = intent.getStringExtra("filename");
                handler.post(() -> showBeamAlert(fname));
            }
        }
        return START_STICKY;
    }

    @Override
    public void onCreate() {
        super.onCreate();
        createNotificationChannel();
        startForeground(NOTIF_ID, buildForegroundNotification());

        // Keep Wi-Fi chip in ultra-low latency mode to prevent distance packet drops
        try {
            WifiManager wm = (WifiManager) getApplicationContext().getSystemService(Context.WIFI_SERVICE);
            if (wm != null) {
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                    wifiLock = wm.createWifiLock(WifiManager.WIFI_MODE_FULL_LOW_LATENCY, "Lexi:LowLatencyLock");
                } else {
                    wifiLock = wm.createWifiLock(WifiManager.WIFI_MODE_FULL_HIGH_PERF, "Lexi:HighPerfLock");
                }
                wifiLock.setReferenceCounted(false);
                wifiLock.acquire();
            }
        } catch (Exception ignored) {}

        // Prevent CPU deep sleep drops during active link
        try {
            PowerManager pm = (PowerManager) getSystemService(Context.POWER_SERVICE);
            if (pm != null) {
                wakeLock = pm.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "Lexi:KeepAliveWakeLock");
                wakeLock.setReferenceCounted(false);
                wakeLock.acquire();
            }
        } catch (Exception ignored) {}

        windowManager = (WindowManager) getSystemService(WINDOW_SERVICE);
        LayoutInflater inflater = LayoutInflater.from(this);
        islandRoot = inflater.inflate(R.layout.layout_dynamic_island, null);

        llCompact = islandRoot.findViewById(R.id.ll_island_compact);
        llExpanded = islandRoot.findViewById(R.id.ll_island_expanded);
        viewCompactDot = islandRoot.findViewById(R.id.view_island_compact_dot);
        viewExpandedDot = islandRoot.findViewById(R.id.view_island_expanded_dot);
        tvCompactStatus = islandRoot.findViewById(R.id.tv_island_compact_status);
        tvExpandedTitle = islandRoot.findViewById(R.id.tv_island_expanded_title);

        SharedPreferences prefs = getSharedPreferences(PREFS_NAME, MODE_PRIVATE);
        int savedX = prefs.getInt("island_x", 0);
        int savedY = prefs.getInt("island_y", dpToPx(8));

        int windowType = (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O)
                ? WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY
                : WindowManager.LayoutParams.TYPE_PHONE;

        params = new WindowManager.LayoutParams(
                WindowManager.LayoutParams.WRAP_CONTENT,
                WindowManager.LayoutParams.WRAP_CONTENT,
                windowType,
                WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE
                        | WindowManager.LayoutParams.FLAG_NOT_TOUCH_MODAL
                        | WindowManager.LayoutParams.FLAG_LAYOUT_IN_SCREEN,
                PixelFormat.TRANSLUCENT
        );
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) {
            params.layoutInDisplayCutoutMode = WindowManager.LayoutParams.LAYOUT_IN_DISPLAY_CUTOUT_MODE_ALWAYS;
        }

        params.gravity = Gravity.TOP | Gravity.CENTER_HORIZONTAL;
        params.x = savedX;
        params.y = savedY;

        setupInteractions();
        try {
            windowManager.addView(islandRoot, params);
        } catch (Exception e) {
            e.printStackTrace();
        }

        scheduleTelemetry();
    }

    private void setupInteractions() {
        llCompact.setOnClickListener(v -> {
            vibrate(20);
            expandIsland();
        });
        llCompact.setOnTouchListener(this);

        TextView btnClose = islandRoot.findViewById(R.id.btn_island_collapse);
        if (btnClose != null) {
            btnClose.setOnClickListener(v -> {
                vibrate(15);
                collapseIsland();
            });
        }

        Button btnSnap = islandRoot.findViewById(R.id.btn_island_snap);
        if (btnSnap != null) {
            btnSnap.setOnClickListener(v -> {
                vibrate(30);
                Toast.makeText(this, "📸 Opening Vision Tray Snap...", Toast.LENGTH_SHORT).show();
                collapseIsland();
                Intent intent = new Intent(this, MainActivity.class);
                intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_CLEAR_TOP);
                intent.putExtra("action", "snap");
                startActivity(intent);
            });
        }

        Button btnClip = islandRoot.findViewById(R.id.btn_island_clip);
        if (btnClip != null) {
            btnClip.setOnClickListener(v -> {
                vibrate(25);
                ClipboardManager cm = (ClipboardManager) getSystemService(Context.CLIPBOARD_SERVICE);
                if (cm != null && cm.hasPrimaryClip() && cm.getPrimaryClip().getItemCount() > 0) {
                    CharSequence cs = cm.getPrimaryClip().getItemAt(0).getText();
                    if (cs != null && cs.length() > 0) {
                        final String text = cs.toString();
                        Toast.makeText(this, "📋 Syncing to Linux Clipboard...", Toast.LENGTH_SHORT).show();
                        new Thread(() -> {
                            boolean ok = LexiApi.sendClipboardText(this, text);
                            handler.post(() -> {
                                if (ok) {
                                    vibrate(40);
                                    Toast.makeText(this, "✓ Injected into Laptop (Ctrl+V)!", Toast.LENGTH_SHORT).show();
                                    collapseIsland();
                                } else {
                                    Toast.makeText(this, "⚠️ Failed to sync clipboard", Toast.LENGTH_SHORT).show();
                                }
                            });
                        }).start();
                        return;
                    }
                }
                Toast.makeText(this, "Phone clipboard empty", Toast.LENGTH_SHORT).show();
            });
        }

        Button btnMirror = islandRoot.findViewById(R.id.btn_island_mirror);
        if (btnMirror != null) {
            btnMirror.setOnClickListener(v -> {
                vibrate(30);
                Toast.makeText(this, "🚀 Starting 60 FPS mirror on laptop...", Toast.LENGTH_SHORT).show();
                new Thread(() -> {
                    boolean ok = LexiApi.triggerMirror(this, false);
                    handler.post(() -> {
                        if (ok) {
                            vibrate(40);
                            Toast.makeText(this, "📱 60 FPS Mirror active on laptop!", Toast.LENGTH_SHORT).show();
                            collapseIsland();
                        } else {
                            Toast.makeText(this, "⚠️ Failed to start mirror", Toast.LENGTH_SHORT).show();
                        }
                    });
                }).start();
            });
        }

        Button btnBeam = islandRoot.findViewById(R.id.btn_island_beam);
        if (btnBeam != null) {
            btnBeam.setOnClickListener(v -> {
                vibrate(20);
                collapseIsland();
                Intent intent = new Intent(this, MainActivity.class);
                intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_CLEAR_TOP);
                intent.putExtra("action", "beam");
                startActivity(intent);
            });
        }
    }

    @Override
    public boolean onTouch(View v, MotionEvent event) {
        switch (event.getAction()) {
            case MotionEvent.ACTION_DOWN:
                initialX = params.x;
                initialY = params.y;
                initialTouchX = event.getRawX();
                initialTouchY = event.getRawY();
                touchStartTime = System.currentTimeMillis();
                return true;

            case MotionEvent.ACTION_MOVE:
                int dx = (int) (event.getRawX() - initialTouchX);
                int dy = (int) (event.getRawY() - initialTouchY);
                if (Math.abs(dx) > 8 || Math.abs(dy) > 8) {
                    params.x = initialX + dx;
                    params.y = Math.max(0, initialY + dy);
                    try {
                        windowManager.updateViewLayout(islandRoot, params);
                    } catch (Exception ignored) {}
                }
                return true;

            case MotionEvent.ACTION_UP:
                long duration = System.currentTimeMillis() - touchStartTime;
                float totalDist = (float) Math.hypot(event.getRawX() - initialTouchX, event.getRawY() - initialTouchY);
                android.util.Log.d("LexiIsland", "ACTION_UP touch! duration=" + duration + " dist=" + totalDist);
                if (totalDist < 50) {
                    vibrate(20);
                    expandIsland();
                } else {
                    SharedPreferences.Editor ed = getSharedPreferences(PREFS_NAME, MODE_PRIVATE).edit();
                    ed.putInt("island_x", params.x);
                    ed.putInt("island_y", params.y);
                    ed.apply();
                }
                return true;
        }
        return false;
    }

    private void expandIsland() {
        if (isExpanded) return;
        isExpanded = true;
        params.width = dpToPx(320);
        params.height = WindowManager.LayoutParams.WRAP_CONTENT;
        llCompact.setVisibility(View.GONE);
        llExpanded.setVisibility(View.VISIBLE);
        islandRoot.requestLayout();
        try {
            windowManager.updateViewLayout(islandRoot, params);
        } catch (Exception ignored) {}

        handler.removeCallbacks(this::collapseIsland);
        handler.postDelayed(this::collapseIsland, 6000);
    }

    private void collapseIsland() {
        if (!isExpanded) return;
        isExpanded = false;
        handler.removeCallbacks(this::collapseIsland);
        params.width = WindowManager.LayoutParams.WRAP_CONTENT;
        params.height = WindowManager.LayoutParams.WRAP_CONTENT;
        llExpanded.setVisibility(View.GONE);
        llCompact.setVisibility(View.VISIBLE);
        islandRoot.requestLayout();
        try {
            windowManager.updateViewLayout(islandRoot, params);
        } catch (Exception ignored) {}
    }

    public void showBeamAlert(String filename) {
        if (filename == null || filename.isEmpty()) filename = "File";
        if (tvExpandedTitle != null) tvExpandedTitle.setText("⚡ Received " + filename + " (Downloads)");
        if (tvCompactStatus != null) tvCompactStatus.setText("⚡ " + filename);
        if (viewCompactDot != null) viewCompactDot.setVisibility(View.VISIBLE);
        if (viewExpandedDot != null) viewExpandedDot.setVisibility(View.VISIBLE);
        expandIsland();
        vibrate(35);
    }

    private int registerCounter = 0;

    private void scheduleTelemetry() {
        new Thread(() -> {
            int ping = LexiApi.pingHost(DynamicIslandService.this);
            if (ping >= 0 && (registerCounter++ % 4 == 0)) {
                LexiApi.registerCompanion(DynamicIslandService.this);
            }
            handler.post(() -> {
                if (ping >= 0) {
                    if (viewCompactDot != null) viewCompactDot.setVisibility(View.VISIBLE);
                    if (viewExpandedDot != null) viewExpandedDot.setVisibility(View.VISIBLE);
                    if (tvCompactStatus != null) tvCompactStatus.setText(ping + "ms");
                    if (tvExpandedTitle != null) tvExpandedTitle.setText("✨ Connected · Arch Linux (" + ping + "ms)");
                } else {
                    if (viewCompactDot != null) viewCompactDot.setVisibility(View.GONE);
                    if (viewExpandedDot != null) viewExpandedDot.setVisibility(View.GONE);
                    if (tvCompactStatus != null) tvCompactStatus.setText("Offline");
                    if (tvExpandedTitle != null) tvExpandedTitle.setText("⚠️ Offline · " + LexiApi.getHost(DynamicIslandService.this));
                }
            });
        }).start();
        handler.postDelayed(this::scheduleTelemetry, 4000);
    }

    private void vibrate(long ms) {
        try {
            Vibrator v = (Vibrator) getSystemService(Context.VIBRATOR_SERVICE);
            if (v != null && v.hasVibrator()) {
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                    v.vibrate(VibrationEffect.createOneShot(ms, VibrationEffect.DEFAULT_AMPLITUDE));
                } else {
                    v.vibrate(ms);
                }
            }
        } catch (Exception ignored) {}
    }

    private int dpToPx(int dp) {
        float density = getResources().getDisplayMetrics().density;
        return Math.round(dp * density);
    }

    private void createNotificationChannel() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            NotificationChannel chan = new NotificationChannel(
                    CHANNEL_ID,
                    "Lexi Dynamic Island",
                    NotificationManager.IMPORTANCE_LOW
            );
            chan.setDescription("Permanent desktop companion notch overlay");
            NotificationManager nm = getSystemService(NotificationManager.class);
            if (nm != null) {
                nm.createNotificationChannel(chan);
            }
        }
    }

    private Notification buildForegroundNotification() {
        Intent notifIntent = new Intent(this, MainActivity.class);
        PendingIntent pi = PendingIntent.getActivity(
                this, 0, notifIntent,
                Build.VERSION.SDK_INT >= Build.VERSION_CODES.M
                        ? PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT
                        : PendingIntent.FLAG_UPDATE_CURRENT
        );

        Notification.Builder builder = (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O)
                ? new Notification.Builder(this, CHANNEL_ID)
                : new Notification.Builder(this);

        return builder
                .setContentTitle("🏝️ Lexi Dynamic Island")
                .setContentText("Active floating companion over all apps")
                .setSmallIcon(R.drawable.ic_launcher)
                .setContentIntent(pi)
                .setOngoing(true)
                .build();
    }

    @Override
    public void onDestroy() {
        super.onDestroy();
        handler.removeCallbacksAndMessages(null);
        if (windowManager != null && islandRoot != null) {
            try {
                windowManager.removeView(islandRoot);
            } catch (Exception ignored) {}
        }
        if (wifiLock != null && wifiLock.isHeld()) {
            try {
                wifiLock.release();
            } catch (Exception ignored) {}
        }
        if (wakeLock != null && wakeLock.isHeld()) {
            try {
                wakeLock.release();
            } catch (Exception ignored) {}
        }
    }
}
