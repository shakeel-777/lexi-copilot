# ⚡ Lexi Dynamic Island & ShareMe Beam for Windows

Native Windows companion for Lexi Copilot. Provides the top-screen dynamic notch, DeepSeek-V4.1-Flash (Vision) chat, and zero-data P2P ShareMe file transfers directly with your Linux laptop.

---

## 🚀 Quick Start (1-Click Run)

1. **Install Python 3.10+** from [python.org](https://www.python.org/) (ensure **"Add Python to PATH"** is checked).
2. Double-click `setup_windows.bat`.
3. Enter your DeepSeek API key when prompted (or leave blank to configure later).
4. **Lexi Dynamic Island** will appear immediately at the top center of your display!

---

## 📡 ShareMe: Zero-Data P2P Direct File Transfers

### 1. Receiving a Movie/File from Linux
1. When your Linux laptop drops a file into Lexi and clicks your Windows PC, Lexi on Windows immediately prompts:
   `Incoming Beam from Shakeel-Linux: [Movie.mkv] (2.4 GB)`
2. The file streams directly into `C:\Users\<YourUser>\Downloads` at **full Wi-Fi hardware speed (30–80 MB/s)**.
3. **Zero Internet Data**: Bytes travel purely over local Wi-Fi radio waves. No browser needed!

### 2. Beaming a File from Windows to Linux
1. Click the **📡 Beam** button on the Windows notch.
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
