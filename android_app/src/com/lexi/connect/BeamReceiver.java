package com.lexi.connect;

import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.net.Uri;
import android.os.Build;
import android.os.VibrationEffect;
import android.os.Vibrator;
import java.io.File;

public class BeamReceiver extends BroadcastReceiver {
    public static final String ACTION_SET_HOST = "com.lexi.connect.SET_HOST";
    public static final String ACTION_BEAM_RECEIVED = "com.lexi.connect.BEAM_RECEIVED";
    public static final String ACTION_RING_PHONE = "com.lexi.connect.RING_PHONE";
    public static final String ACTION_SYNC_CLIPBOARD = "com.lexi.connect.SYNC_CLIPBOARD";
    private static final String NOTIF_CHANNEL_ID = "lexi_beam_channel";
    private static final int NOTIF_ID = 9902;

    @Override
    public void onReceive(Context context, Intent intent) {
        if (intent == null || intent.getAction() == null) return;
        String action = intent.getAction();

        if (ACTION_SET_HOST.equals(action)) {
            String host = intent.getStringExtra("host");
            int port = intent.getIntExtra("port", LexiApi.DEFAULT_PORT);
            if (host != null && !host.trim().isEmpty()) {
                LexiApi.setHost(context, host.trim());
                LexiApi.setPort(context, port);
            }
        } else if (ACTION_BEAM_RECEIVED.equals(action)) {
            String filename = intent.getStringExtra("filename");
            String path = intent.getStringExtra("path");
            long size = intent.getLongExtra("size", 0);

            if (filename == null) filename = "file";
            if (path == null) path = "/sdcard/Download/" + filename;

            // 1. Haptic feedback
            vibrate(context, 40);

            // 2. Expand floating Dynamic Island if active
            Intent serviceIntent = new Intent(context, DynamicIslandService.class);
            serviceIntent.putExtra("action", "beam_alert");
            serviceIntent.putExtra("filename", filename);
            try {
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                    context.startForegroundService(serviceIntent);
                } else {
                    context.startService(serviceIntent);
                }
            } catch (Exception ignored) {}

            // 3. Post system notification with tap to open
            showNotification(context, filename, path);
        } else if (ACTION_RING_PHONE.equals(action)) {
            vibrate(context, 1000);
            try {
                android.media.Ringtone r = android.media.RingtoneManager.getRingtone(
                        context,
                        android.media.RingtoneManager.getDefaultUri(android.media.RingtoneManager.TYPE_ALARM)
                );
                if (r != null) r.play();
            } catch (Exception ignored) {}
        } else if (ACTION_SYNC_CLIPBOARD.equals(action)) {
            String text = intent.getStringExtra("text");
            if (text != null && !text.isEmpty()) {
                try {
                    android.content.ClipboardManager cm = (android.content.ClipboardManager) context.getSystemService(Context.CLIPBOARD_SERVICE);
                    if (cm != null) {
                        cm.setPrimaryClip(android.content.ClipData.newPlainText("Lexi Copilot", text));
                        vibrate(context, 50);
                    }
                } catch (Exception ignored) {}
            }
        }
    }

    private void showNotification(Context context, String filename, String path) {
        NotificationManager nm = (NotificationManager) context.getSystemService(Context.NOTIFICATION_SERVICE);
        if (nm == null) return;

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            NotificationChannel chan = new NotificationChannel(
                    NOTIF_CHANNEL_ID,
                    "Lexi Beam Transfers",
                    NotificationManager.IMPORTANCE_HIGH
            );
            chan.setDescription("Notifications for files beamed from laptop");
            chan.enableVibration(true);
            nm.createNotificationChannel(chan);
        }

        Intent viewIntent = new Intent(Intent.ACTION_VIEW);
        File file = new File(path);
        if (file.exists()) {
            Uri contentUri = Uri.parse("content://" + LexiFileProvider.AUTHORITY + "/" + file.getName());
            viewIntent.setDataAndType(contentUri, "*/*");
            viewIntent.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION);
        } else {
            viewIntent = new Intent(android.app.DownloadManager.ACTION_VIEW_DOWNLOADS);
        }
        viewIntent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);

        PendingIntent pi = PendingIntent.getActivity(
                context, 0, viewIntent,
                Build.VERSION.SDK_INT >= Build.VERSION_CODES.M
                        ? PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT
                        : PendingIntent.FLAG_UPDATE_CURRENT
        );

        android.app.Notification.Builder builder = (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O)
                ? new android.app.Notification.Builder(context, NOTIF_CHANNEL_ID)
                : new android.app.Notification.Builder(context);

        builder.setContentTitle("⚡ Lexi Beam")
                .setContentText("Received " + filename + " in Downloads")
                .setSmallIcon(R.drawable.ic_launcher)
                .setContentIntent(pi)
                .setAutoCancel(true);

        nm.notify(NOTIF_ID, builder.build());
    }

    private void vibrate(Context context, long ms) {
        try {
            Vibrator v = (Vibrator) context.getSystemService(Context.VIBRATOR_SERVICE);
            if (v != null && v.hasVibrator()) {
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                    v.vibrate(VibrationEffect.createOneShot(ms, VibrationEffect.DEFAULT_AMPLITUDE));
                } else {
                    v.vibrate(ms);
                }
            }
        } catch (Exception ignored) {}
    }
}
