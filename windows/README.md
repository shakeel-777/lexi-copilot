# ⚡ Lexi Dynamic Island & ShareMe Beam for Windows

Native Windows companion for Lexi Copilot. Provides the top-screen dynamic notch, DeepSeek-V4.1-Flash (Vision) chat, and zero-data P2P ShareMe file transfers directly with your Linux laptop.

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
- Clones and sets up the isolated runtime in `%LOCALAPPDATA%\Lexi`
- Prompts for your **DeepSeek** (`sk-...`) or **Groq** (`gsk_...`) API key
- Creates a Desktop shortcut and auto-start shortcut on Windows boot (`shell:startup`)
- Launches the top-docked Dynamic Island notch immediately!

---

## 🧠 Supported AI Providers & Keys

Lexi on Windows natively supports:
- **Groq LPU**: Ultra-fast (500+ tokens/sec) with `llama-3.3-70b-versatile` and automatic `llama-3.2-11b-vision-preview` when capturing screen snips. Accepts any Groq key (`gsk_...`).
- **DeepSeek Direct**: Native `deepseek-flash` (DeepSeek-V4.1-Flash Vision) with deep reasoning chains. Accepts any DeepSeek key (`sk-...`).
- **OpenRouter & OpenAI**: Full OpenAI-compatible model routing.

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
