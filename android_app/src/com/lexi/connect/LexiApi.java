package com.lexi.connect;

import android.content.Context;
import android.content.SharedPreferences;
import java.io.*;
import java.net.HttpURLConnection;
import java.net.URL;
import java.net.URLEncoder;

public class LexiApi {
    private static final String PREFS_NAME = "lexi_connect_prefs";
    private static final String KEY_HOST = "host_ip";
    private static final String KEY_PORT = "host_port";
    public static final String DEFAULT_HOST = "10.93.59.11";
    public static final int DEFAULT_PORT = 7890;

    public interface ProgressListener {
        void onProgress(long transferred, long total, float speedMbps);
    }

    public static String getHost(Context ctx) {
        SharedPreferences p = ctx.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE);
        return p.getString(KEY_HOST, DEFAULT_HOST);
    }

    public static void setHost(Context ctx, String host) {
        SharedPreferences p = ctx.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE);
        p.edit().putString(KEY_HOST, host.trim()).apply();
    }

    public static int getPort(Context ctx) {
        SharedPreferences p = ctx.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE);
        return p.getInt(KEY_PORT, DEFAULT_PORT);
    }

    public static String getBaseUrl(Context ctx) {
        return "http://" + getHost(ctx) + ":" + getPort(ctx);
    }

    /**
     * Uploads file to Lexi ~/Downloads with streaming chunk buffers and speed tracking.
     */
    public static boolean uploadStream(Context ctx, InputStream in, String filename, long totalBytes, ProgressListener listener) {
        HttpURLConnection conn = null;
        try {
            String encName = URLEncoder.encode(filename, "UTF-8");
            URL url = new URL(getBaseUrl(ctx) + "/api/upload?filename=" + encName);
            conn = (HttpURLConnection) url.openConnection();
            conn.setRequestMethod("POST");
            conn.setDoOutput(true);
            conn.setChunkedStreamingMode(4 * 1024 * 1024); // 4MB chunks
            conn.setRequestProperty("Content-Type", "application/octet-stream");
            conn.setRequestProperty("X-Filename", encName);
            conn.setConnectTimeout(8000);
            conn.setReadTimeout(60000);

            OutputStream out = conn.getOutputStream();
            byte[] buf = new byte[128 * 1024];
            int read;
            long transferred = 0;
            long t0 = System.currentTimeMillis();

            while ((read = in.read(buf)) != -1) {
                out.write(buf, 0, read);
                transferred += read;
                if (listener != null) {
                    long elapsed = Math.max(System.currentTimeMillis() - t0, 1);
                    float speedMbps = (transferred / (1024f * 1024f)) / (elapsed / 1000f);
                    listener.onProgress(transferred, totalBytes, speedMbps);
                }
            }
            out.flush();
            out.close();

            int code = conn.getResponseCode();
            return code >= 200 && code < 300;
        } catch (Exception e) {
            e.printStackTrace();
            return false;
        } finally {
            if (conn != null) conn.disconnect();
        }
    }

    /**
     * Uploads photo directly to Continuity Camera endpoint (/api/camera-snap).
     */
    public static boolean uploadCameraSnap(Context ctx, byte[] imageBytes) {
        HttpURLConnection conn = null;
        try {
            URL url = new URL(getBaseUrl(ctx) + "/api/camera-snap");
            conn = (HttpURLConnection) url.openConnection();
            conn.setRequestMethod("POST");
            conn.setDoOutput(true);
            conn.setRequestProperty("Content-Type", "image/jpeg");
            conn.setConnectTimeout(8000);
            conn.setReadTimeout(20000);

            OutputStream out = conn.getOutputStream();
            out.write(imageBytes);
            out.flush();
            out.close();

            int code = conn.getResponseCode();
            return code >= 200 && code < 300;
        } catch (Exception e) {
            e.printStackTrace();
            return false;
        } finally {
            if (conn != null) conn.disconnect();
        }
    }

    /**
     * Sends phone clipboard text to Linux laptop clipboard (/api/clipboard).
     */
    public static boolean sendClipboardText(Context ctx, String text) {
        HttpURLConnection conn = null;
        try {
            URL url = new URL(getBaseUrl(ctx) + "/api/clipboard");
            conn = (HttpURLConnection) url.openConnection();
            conn.setRequestMethod("POST");
            conn.setDoOutput(true);
            conn.setRequestProperty("Content-Type", "application/json");
            conn.setConnectTimeout(4000);
            conn.setReadTimeout(5000);

            String json = "{\"text\":" + escapeJson(text) + "}";
            OutputStream out = conn.getOutputStream();
            out.write(json.getBytes("UTF-8"));
            out.flush();
            out.close();

            return conn.getResponseCode() == 200;
        } catch (Exception e) {
            return false;
        } finally {
            if (conn != null) conn.disconnect();
        }
    }

    /**
     * Fetches current laptop clipboard text (/api/clipboard).
     */
    public static String fetchClipboardText(Context ctx) {
        HttpURLConnection conn = null;
        try {
            URL url = new URL(getBaseUrl(ctx) + "/api/clipboard");
            conn = (HttpURLConnection) url.openConnection();
            conn.setRequestMethod("GET");
            conn.setConnectTimeout(3000);
            conn.setReadTimeout(4000);

            if (conn.getResponseCode() == 200) {
                BufferedReader r = new BufferedReader(new InputStreamReader(conn.getInputStream(), "UTF-8"));
                StringBuilder sb = new StringBuilder();
                String line;
                while ((line = r.readLine()) != null) sb.append(line);
                r.close();
                String resp = sb.toString();
                // Simple parse {"text":"..."}
                int idx = resp.indexOf("\"text\":");
                if (idx != -1) {
                    int start = resp.indexOf("\"", idx + 7);
                    int end = resp.lastIndexOf("\"");
                    if (start != -1 && end > start) {
                        return unescapeJson(resp.substring(start + 1, end));
                    }
                }
            }
        } catch (Exception ignored) {}
        finally {
            if (conn != null) conn.disconnect();
        }
        return null;
    }

    /**
     * Queries host status and ping latency.
     */
    public static int pingHost(Context ctx) {
        long t0 = System.currentTimeMillis();
        HttpURLConnection conn = null;
        try {
            URL url = new URL(getBaseUrl(ctx) + "/api/status");
            conn = (HttpURLConnection) url.openConnection();
            conn.setConnectTimeout(2000);
            conn.setReadTimeout(2000);
            if (conn.getResponseCode() == 200) {
                return (int) (System.currentTimeMillis() - t0);
            }
        } catch (Exception ignored) {}
        finally {
            if (conn != null) conn.disconnect();
        }
        return -1;
    }

    /**
     * Triggers scrcpy screen mirroring on the laptop display.
     */
    public static boolean triggerMirror(Context ctx, boolean stealth) {
        HttpURLConnection conn = null;
        try {
            URL url = new URL(getBaseUrl(ctx) + "/api/mirror?stealth=" + stealth);
            conn = (HttpURLConnection) url.openConnection();
            conn.setRequestMethod("POST");
            conn.setConnectTimeout(3000);
            conn.setReadTimeout(5000);
            conn.setDoOutput(true);
            conn.getOutputStream().close();
            return conn.getResponseCode() == 200;
        } catch (Exception e) {
            return false;
        } finally {
            if (conn != null) conn.disconnect();
        }
    }

    /**
     * Announces companion device presence to Lexi server (/api/peer/register).
     */
    public static boolean registerCompanion(Context ctx) {
        HttpURLConnection conn = null;
        try {
            URL url = new URL(getBaseUrl(ctx) + "/api/peer/register");
            conn = (HttpURLConnection) url.openConnection();
            conn.setRequestMethod("POST");
            conn.setDoOutput(true);
            conn.setRequestProperty("Content-Type", "application/json");
            conn.setConnectTimeout(3000);
            conn.setReadTimeout(3000);

            String deviceModel = android.os.Build.MODEL != null ? android.os.Build.MODEL : "Android Device";
            String json = "{\"device\":\"" + escapeJsonRaw(deviceModel + " (Phone)") + "\",\"type\":\"phone\",\"os\":\"android\"}";
            OutputStream out = conn.getOutputStream();
            out.write(json.getBytes("UTF-8"));
            out.flush();
            out.close();
            return conn.getResponseCode() == 200;
        } catch (Exception ignored) {
            return false;
        } finally {
            if (conn != null) conn.disconnect();
        }
    }

    /**
     * Fetches list of all discovered Lexi peers from the local host (/api/peers).
     */
    public static String fetchPeersJson(Context ctx) {
        HttpURLConnection conn = null;
        try {
            URL url = new URL(getBaseUrl(ctx) + "/api/peers");
            conn = (HttpURLConnection) url.openConnection();
            conn.setRequestMethod("GET");
            conn.setConnectTimeout(3000);
            conn.setReadTimeout(3000);
            if (conn.getResponseCode() == 200) {
                BufferedReader r = new BufferedReader(new InputStreamReader(conn.getInputStream(), "UTF-8"));
                StringBuilder sb = new StringBuilder();
                String line;
                while ((line = r.readLine()) != null) sb.append(line);
                r.close();
                return sb.toString();
            }
        } catch (Exception ignored) {
        } finally {
            if (conn != null) conn.disconnect();
        }
        return null;
    }

    private static String escapeJsonRaw(String s) {
        if (s == null) return "";
        return s.replace("\"", "\\\"");
    }

    private static String escapeJson(String s) {
        if (s == null) return "\"\"";
        StringBuilder sb = new StringBuilder("\"");
        for (char c : s.toCharArray()) {
            switch (c) {
                case '"': sb.append("\\\""); break;
                case '\\': sb.append("\\\\"); break;
                case '\b': sb.append("\\b"); break;
                case '\f': sb.append("\\f"); break;
                case '\n': sb.append("\\n"); break;
                case '\r': sb.append("\\r"); break;
                case '\t': sb.append("\\t"); break;
                default:
                    if (c < ' ') sb.append(String.format("\\u%04x", (int) c));
                    else sb.append(c);
            }
        }
        sb.append("\"");
        return sb.toString();
    }

    private static String unescapeJson(String s) {
        return s.replace("\\n", "\n").replace("\\t", "\t").replace("\\\"", "\"").replace("\\\\", "\\");
    }
}
