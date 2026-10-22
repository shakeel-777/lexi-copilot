# Lexi YouTube Bridge — Arch Linux User-Level Installation Guide

This guide explains how to install and verify the **Lexi YouTube Bridge** Chrome Manifest V3 extension and Native Messaging host on **Arch Linux (GNOME / Wayland)** under your regular user account (`shakeel`) without root privileges, Chrome remote debugging, or a local HTTP server.

## Overview of Files

- `chrome_extension/manifest.json` — Manifest V3 configuration scoped strictly to `https://www.youtube.com/*` and `https://youtu.be/*`.
- `chrome_extension/background.js` — Service worker managing the Native Messaging port (`com.lexi.browser_bridge`) and the bounded 3-stage workflow (`search`, `open_watch`, `fullscreen_and_playback`).
- `chrome_extension/content.js` — Scoped YouTube content script that selects video results, verifies the watch page and `<video>` element, and attempts playback/fullscreen when permitted by browser policy.
- `lexi_browser_host.py` — Local Python Native Messaging host and Unix-domain socket bridge (`$XDG_RUNTIME_DIR/lexi-browser-bridge.sock`, mode `0600`).
- `com.lexi.browser_bridge.json.template` — Native Messaging host manifest template requiring your unpacked extension ID in `allowed_origins`.
- `install_chrome_bridge.sh` — Non-root user-level setup script.

## Prerequisites on Arch Linux

Ensure your project files are placed in `/home/shakeel/.local/share/mochi-copilot/` and that Google Chrome or Chromium is installed:

```bash
cd /home/shakeel/.local/share/mochi-copilot
python -m py_compile lexi.py lexi_browser_host.py
chmod +x lexi.py lexi_browser_host.py install_chrome_bridge.sh
```

## Step 1: Load the Unpacked Extension in Developer Mode

1. Open Google Chrome (or Chromium) and navigate to:
   ```text
   chrome://extensions
   ```
2. Toggle **Developer mode** ON in the top-right corner.
3. Click **Load unpacked** in the top-left toolbar.
4. Select the extension directory:
   ```text
   /home/shakeel/.local/share/mochi-copilot/chrome_extension
   ```
5. Locate the newly added **Lexi YouTube Bridge** card and copy its 32-character **ID** (for example: `abcdefghijklmnopqrstuvwxyzabcdef`).

## Step 2: Install the User-Level Native Messaging Manifest

Run `install_chrome_bridge.sh` as your normal user (**do not use `sudo`**) and pass the 32-character extension ID from Step 1:

```bash
cd /home/shakeel/.local/share/mochi-copilot
./install_chrome_bridge.sh <YOUR_32_CHAR_EXTENSION_ID>
```

What this script does:
- Validates that the script is not running as root and that the extension ID matches Chrome's 32-character `a–p` format.
- Marks `/home/shakeel/.local/share/mochi-copilot/lexi_browser_host.py` executable (`chmod +x`).
- Populates `com.lexi.browser_bridge.json.template` with the exact path to `lexi_browser_host.py` and `"chrome-extension://<YOUR_32_CHAR_EXTENSION_ID>/"` in `allowed_origins`.
- Writes `com.lexi.browser_bridge.json` (mode `0600`) to:
  - `~/.config/google-chrome/NativeMessagingHosts/com.lexi.browser_bridge.json`
  - `~/.config/chromium/NativeMessagingHosts/com.lexi.browser_bridge.json`

### Manual Alternative (Filling `allowed_origins` Directly)

If you prefer to install the manifest manually without the script:

1. Copy `com.lexi.browser_bridge.json.template` to `~/.config/google-chrome/NativeMessagingHosts/com.lexi.browser_bridge.json`.
2. Replace `__LEXI_HOST_PATH__` with `/home/shakeel/.local/share/mochi-copilot/lexi_browser_host.py`.
3. Replace `__EXTENSION_ID__` in `allowed_origins` with your unpacked extension ID:
   ```json
   {
     "name": "com.lexi.browser_bridge",
     "description": "Lexi YouTube Native Messaging Bridge",
     "path": "/home/shakeel/.local/share/mochi-copilot/lexi_browser_host.py",
     "type": "stdio",
     "allowed_origins": [
       "chrome-extension://YOUR_32_CHAR_EXTENSION_ID/"
     ]
   }
   ```

## Step 3: Activate the Bridge and Restart Lexi

1. In `chrome://extensions`, click the **Reload** (`↻`) button on the **Lexi YouTube Bridge** card so it connects to the newly registered native host.
2. Verify the user-private Unix socket is active:
   ```bash
   ls -l "${XDG_RUNTIME_DIR}/lexi-browser-bridge.sock"
   ```
3. Restart the Lexi user systemd service and confirm a single process is running:
   ```bash
   systemctl --user restart lexi-island.service
   systemctl --user status lexi-island.service
   pgrep -af 'lexi.py --island'
   ```

## Step 4: Usage and Expected Behavior

Ask Lexi:
> “Play Kutty Story on YouTube fullscreen.”

Or use the direct command shortcut in Lexi:
```text
/yt Kutty Story
```

Lexi emits:
```xml
<action type="browser" command="youtube_play_fullscreen" query="Kutty Story"></action>
```

How Lexi handles the result:
1. **Search Stage**: Opens or reuses a YouTube tab (`https://www.youtube.com/results?search_query=Kutty+Story`), waits for results, and selects the first non-ad video match (`search_verified: true`).
2. **Watch Stage**: Navigates to the selected watch page (`https://www.youtube.com/watch?v=...`) and confirms the HTML5 `<video>` element is mounted (`watch_verified: true`).
3. **Playback & Fullscreen Stage**:
   - Attempts `video.play()` and checks `!video.paused` (`playing_verified`).
   - Attempts fullscreen and checks `document.fullscreenElement` (`fullscreen_verified`).
   - Because Chrome enforces a transient user-activation gesture requirement (`Element.requestFullscreen()`) for unclicked extension-initiated fullscreen requests, when Chrome blocks automatic fullscreen, the bridge returns `fullscreen_verified: false` with `"reason": "Chrome required a user gesture for fullscreen..."`, and Lexi explicitly reports that **fullscreen is not verified** and tells you that a single click or `f` keypress in the tab is needed.


## Closing WhatsApp / Instagram tabs (v1.1.0)

The extension can close tabs for exactly two sites, on request from Lexi: `web.whatsapp.com`
and `www.instagram.com` / `instagram.com`. It cannot close any other tab, and Lexi cannot send
it URLs or scripts. After changing the extension, open `chrome://extensions` and press **Reload**
on the Lexi extension. No new permissions are requested.

Try it: open a WhatsApp Web tab, then tell Lexi "close WhatsApp". "Close all" asks you to say
"confirm" first.
