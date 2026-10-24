# ⚡ Lexi Dynamic Island & Phone Mirroring Copilot for Windows

Native Windows companion for Lexi Copilot — **100% feature parity with Linux**. Provides the top-screen hardware-accelerated dynamic notch, Android 60 FPS phone mirroring, multimodal vision AI, vertical side panel mode, detached desktop pet mascot, and zero-data P2P ShareMe file transfers directly with your Linux laptop.

---

## ⚡ 1-Command Install (PowerShell / curl)

Open PowerShell on any Windows 10/11 machine and run:

```powershell
irm https://raw.githubusercontent.com/shakeel-777/lexi-copilot/master/windows/install.ps1 | iex
```

Or using standard curl:

```cmd
curl.exe -sSL https://raw.githubusercontent.com/shakeel-777/lexi-copilot/master/windows/install.ps1 | powershell -
```

**What the installer does automatically:**
- Auto-detects or installs Python 3.11 via Windows `winget`
- Auto-installs **`scrcpy`** for Android Phone Mirroring via `winget install Genymobile.scrcpy`
- Clones and sets up the isolated runtime in `%LOCALAPPDATA%\Lexi`
- Prompts for your **DeepSeek** (`sk-...`) or **Groq** (`gsk_...`) API key
- Creates a Desktop shortcut and auto-start shortcut on Windows boot (`shell:startup`)
- Launches the top-docked Dynamic Island notch immediately!

---

## 🌟 Full Feature Parity with Linux

### 🏝️ 1. Dynamic Island Top Notch
- **Permanent Top Notch**: Pinned seamlessly at `y = 0` (top center of Windows screen) with OLED dark-glass acrylic styling.
- **Liquid Morphing Pills**: Hovering reveals instant action pills:
  `[💬 Copilot] [⚡ Beam] [📱 Phone] [◨ Side Panel] [✂️ Snip] [🐾 Pet] [❓ Guide] [⚙️ Settings]`
- **Live Media Player & Equalizer**: Live track title and artist ticker (Spotify, VLC, Chrome/Edge) + animated mini equalizer and quick controls `[⏮] [⏯] [⏭]`.
- **Live Download Progress Sentinel**: Real-time throughput (MB/s) and progress indicators for active browser downloads.
- **Drag-and-Drop Target**: Drop files directly onto the notch to beam to Linux or ask AI about the file.

### 📱 2. Android Phone Mirroring & Remote Control
- **60 FPS Wireless & USB Link**: Direct wireless screen mirroring and mouse/keyboard control powered by `scrcpy`.
- **Zero-Touch Unlock**: Automatically wakes device and enters lockscreen PIN from `~/.lexi_config.json`.
- **Autonomous Reconnect Supervisor**: Transparently restores stream if Wi-Fi packets drop across rooms.
- **Wireless 1-Click ADB**: Toggle wireless mode directly from the Guide with `adb tcpip 5555`.
- **H.265 / H.264 Resilience**: High-efficiency HEVC compression with automatic fallback.

### 💬 3. Multimodal Vision Copilot & Side Panel
- **Multi-Page Attachments**: Attach up to 5 screenshots, terminal outputs, or documents in a single prompt.
- **Deep Reasoning Display**: Renders DeepSeek-R1 reasoning process collapsible tokens (`💭 Reasoning Process`).
- **Interactive Region Snip (<kbd>✂️ Snip</kbd>)**: Rubber-band crosshair overlay to snip any screen area and pass directly into AI.
- **Vertical Side Panel Mode (<kbd>◨ Side</kbd>)**: Docks to the right screen edge as a persistent sidebar companion.

### ⌨️ 4. Global Windows Hotkeys & System Tray
- <kbd>Win</kbd> + <kbd>3</kbd> or <kbd>Ctrl</kbd> + <kbd>Shift</kbd> + <kbd>C</kbd> — Summon or dismiss Copilot anywhere
- <kbd>Win</kbd> + <kbd>Shift</kbd> + <kbd>S</kbd> or <kbd>Ctrl</kbd> + <kbd>Shift</kbd> + <kbd>S</kbd> — Interactive Screen Region Snip
- <kbd>Ctrl</kbd> + <kbd>Shift</kbd> + <kbd>B</kbd> — Toggle Vertical Side Panel mode
- <kbd>F1</kbd> or <kbd>Ctrl</kbd> + <kbd>Shift</kbd> + <kbd>H</kbd> — Open Interactive User Guide & Diagnostics
- <kbd>Ctrl</kbd> + <kbd>,</kbd> — Open AI Provider & Model Settings
- **System Tray Icon**: Full context menu control (Open Copilot, Phone Mirror, Beam, Snip, Side Panel, Pet, Settings, Exit).

### 🩺 5. Interactive User Guide & Live Diagnostics (<kbd>F1</kbd>)
- Built-in multi-tab helper dialog with beginner-friendly guides for Dynamic Island, Phone Mirroring, and AirDrop.
- **Live Self-Test**: Automatically checks scrcpy presence, Android ADB connection, LAN IP address, and AI API keys with 1-click fixes!

### 🐾 6. Detached Roaming Desktop Pet Mascot
- Detach Lexi as a cute borderless anime avatar floating on your desktop.
- Draggable anywhere; double-click opens Copilot; right-click context menu lets you re-dock to the top island notch.

### 🛠️ 7. Developer Power Tools & Natural Language Commands
- `kill port 3000` / `inspect port 8080` — Instant PID inspection & termination.
- `top processes` / `top mem` — Lists resource hogs directly in chat.
- `kill process <name/pid>` — Terminate rogue processes with system shields.
- `volume 80` / `mute` — Controls master volume.
- `pause` / `next song` — Global media keys.

### 🤖 8. Supported AI Providers
- **Groq LPU**: Ultra-fast (500+ T/s) with `llama-3.3-70b-versatile` & `llama-3.2-11b-vision-preview`.
- **DeepSeek Direct**: Native `deepseek-flash` with vision & reasoning tokens.
- **Ollama (100% Free / Local)**: Offline inference via local Ollama.
- **OpenRouter & OpenAI**: Universal OpenAI-compatible API support.

---

## 📡 ShareMe: Zero-Data P2P Direct File Transfers

### 1. Receiving a Movie/File from Linux
1. When your Linux laptop drops a file into Lexi and clicks your Windows PC, Lexi on Windows immediately prompts:
   `Incoming Beam from Shakeel-Linux: [Movie.mkv] (2.4 GB)`
2. The file streams directly into `C:\Users\<YourUser>\Downloads` at **full Wi-Fi hardware speed (30–80 MB/s)**.
3. **Zero Internet Data**: Bytes travel purely over local Wi-Fi radio waves. No browser needed!

### 2. Beaming a File from Windows to Linux
1. Click the **⚡ Beam** button on the Windows notch.
2. Select any movie, video, or folder.
3. Lexi discovers your nearby Linux laptop on the LAN automatically and pushes it straight to Linux `~/Downloads`!

---

## 📦 Building a Standalone `Lexi.exe`

To package Lexi into a portable single-file executable that requires zero Python installation:

```cmd
build_exe.bat
```

The compiled binary will be placed in `dist\Lexi.exe`. You can distribute `Lexi.exe` to any Windows 10/11 machine.

---

## ⚙️ Features
- **Top-Screen Dynamic Island**: Translucent dark acrylic glass capsule docking smoothly at `y = 0`.
- **DeepSeek-V4.1-Flash Vision**: Multimodal visual chat + deep reasoning chain tokens.
- **📸 Screen Snip**: 1-click full desktop capture analyzed instantly by DeepSeek.
- **Hardware Automation**: Media keys (Play/Pause/Next), master volume control, display brightness, screen lock.
