# ✨ Lexi Copilot

> **The first native Dynamic Island AI Desktop Copilot & AirDrop built for Linux (GNOME / Wayland).**  
> *Architected and created by Shakeel.*

<div align="center">

[![Arch Linux](https://img.shields.io/badge/Arch_Linux-Wayland-1793D1?logo=arch-linux&logoColor=white)](https://archlinux.org)
[![Windows 10/11](https://img.shields.io/badge/Windows-10%20%2F%2011-0078D6?logo=windows&logoColor=white)](windows)
[![GTK4 & Libadwaita](https://img.shields.io/badge/GTK4-Libadwaita-3584e4?logo=gnome&logoColor=white)](https://gnome.org)
[![Python 3.11+](https://img.shields.io/badge/Python-3.11+-3776AB?logo=python&logoColor=white)](https://python.org)
[![scrcpy](https://img.shields.io/badge/Phone_Mirror-scrcpy_5.0-3DDC84?logo=android&logoColor=white)](https://github.com/Genymobile/scrcpy)
[![RAM Footprint](https://img.shields.io/badge/RAM-%3C_90_MB-success)](https://github.com/shakeel-777/lexi-copilot)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![GitHub Stars](https://img.shields.io/github/stars/shakeel-777/lexi-copilot?style=social)](https://github.com/shakeel-777/lexi-copilot/stargazers)

**[⚡ Quick Install](#-quick-install) • [🗑️ Uninstall](#️-clean-uninstallation) • [🌟 Features](#-core-highlights) • [📊 Comparison](#-why-lexi) • [⌨️ Shortcuts](#️-global-keybindings) • [⭐ Star This Project](#-star-history)**

</div>

---

## ⚡ Quick Install

### 🐧 Linux (Arch / Fedora / Ubuntu / Debian)

Install and launch Lexi Copilot in a single command:

```bash
curl -fsSL https://raw.githubusercontent.com/shakeel-777/lexi-copilot/master/install.sh | bash
```

Or on Arch Linux via **AUR**:
```bash
yay -S lexi-copilot-git
# or
paru -S lexi-copilot-git
```

### 🪟 Windows (Windows 10 / 11)

Install and launch the native **Lexi Dynamic Island, 60 FPS Phone Mirror & ShareMe Copilot** in a single **PowerShell** command:

```powershell
irm https://raw.githubusercontent.com/shakeel-777/lexi-copilot/master/windows/install.ps1 | iex
```

Or using standard `curl.exe`:
```cmd
curl.exe -sSL https://raw.githubusercontent.com/shakeel-777/lexi-copilot/master/windows/install.ps1 | powershell -
```

*Auto-detects Python, installs scrcpy phone mirror engine, sets up PySide6 runtime, prompts for your DeepSeek or Groq key, creates desktop & startup shortcuts, and launches the notch immediately.*

---

## 🗑️ Clean Uninstallation

If you ever wish to completely remove Lexi from your system:

### 🐧 Linux
Run this single command in terminal to stop background systemd services, remove desktop launchers, and delete all runtime files:
```bash
curl -fsSL https://raw.githubusercontent.com/shakeel-777/lexi-copilot/master/uninstall.sh | bash
```

### 🪟 Windows
Run this single command in **PowerShell** to terminate running processes, remove desktop/startup shortcuts, and clean up application data:
```powershell
irm https://raw.githubusercontent.com/shakeel-777/lexi-copilot/master/windows/uninstall.ps1 | iex
```

Or using standard `curl.exe`:
```cmd
curl.exe -sSL https://raw.githubusercontent.com/shakeel-777/lexi-copilot/master/windows/uninstall.ps1 | powershell -
```

---

## 📊 Why Lexi?

Most desktop AI assistants are 800MB+ Electron web wrappers that freeze your system and lack OS integration. **Lexi is built natively in Python, GTK4, and Libadwaita**, running as a permanent hardware-accelerated Dynamic Island notch at less than **90 MB RAM**.

| Feature | ⚡ Lexi Copilot | 🐌 Electron AI Wrappers | 🍏 macOS Raycast |
|---|:---:|:---:|:---:|
| **Memory Footprint** | **< 90 MB RAM** | 800 MB – 1.5 GB | ~150 MB |
| **Native Dynamic Island Notch** | **✅ Hardware-accelerated** | ❌ None | ❌ None |
| **Linux AirDrop (Lexi Beam)** | **✅ Local P2P + PWA** | ❌ None | ❌ None |
| **Android Phone Mirroring** | **✅ 60 FPS Wireless (scrcpy)** | ❌ None | ❌ None |
| **Wayland Portal Vision Snip** | **✅ Zero compositor freeze** | ⚠️ Slow / Broken | ❌ (macOS only) |
| **Port Inspector & Killer** | **✅ Built-in (`kill port 3000`)** | ❌ None | ⚠️ Paid extension |
| **Top Process Killer** | **✅ Built-in (`top processes`)** | ❌ None | ⚠️ Extra script |
| **Do Not Disturb (DND) Switch** | **✅ GNOME/Mako/Dunst/SwayNC** | ❌ None | ❌ None |
| **Zero Cloud Lock-in** | **✅ DeepSeek / OpenRouter / Local** | ❌ Vendor locked | ❌ Subscription |

---

## 🌟 Core Highlights

### 🏝️ 1. Dynamic Island Notch
- **Permanent Top Notch**: Pinned cleanly at the top-center of your screen right beneath the panel, keeping your primary workspace completely unobstructed.
- **Interactive Morphing Actions**: Hovering over the notch smoothly morphs into quick action pills:
  `[💬 Copilot] [⚡ Beam] [📱 Phone] [◨ Side Panel] [✂️ Snip] [🐾 Pet]`.
- **Live Media Player & Equalizer**: Real-time integration with Spotify and MPRIS players displaying live track progress, cover artwork, and animated equalizer spectrum.
- **Live Download Progress Activity**: Monitors active downloads (`.crdownload`, `.part`) and renders live throughput (MB/s) and progress indicators directly in the notch.

### 💬 2. Floating Multimodal Vision Copilot
- **Wayland Screen Intelligence**: Capture full screen (<kbd>Super</kbd> + <kbd>C</kbd>) or region snips (<kbd>Super</kbd> + <kbd>Shift</kbd> + <kbd>C</kbd>) natively using Wayland desktop screencast portals without compositor stutter.
- **Multi-Page Attachments (Up to 5 Pages)**: Attach and cross-analyze multiple application windows, terminal logs, or PDF documents in a single prompt.
- **Quick Action Strip**: One-click actions directly inside Copilot (`⚡ Quick`, `📱 Phone`, `📡 Beam`, `🧹 Close All`, `Explain`, `Fix`, `Summarize`).
- **Syntax Highlighting & Markdown**: Custom rendering for code blocks, bullet points, blockquotes, and tables across Python, Rust, Shell, JS, C, and Go.

### 🛠️ 3. Developer Power Tools (Zero-Latency Local CLI & Island)
- **Port Inspector & Killer**:
  - `port 3000` or `inspect port 8080` — Instant PID and process inspection.
  - `kill port 3000` — Gracefully frees blocked development ports in one second.
- **Top Process Manager**:
  - `top processes` or `top mem` — Lists resource hogs without leaving your screen.
  - `kill process <pid>` — Safely terminates runaway processes with protected-process safeguards (`systemd`, `init`, `bash` are shielded).
- **Do Not Disturb (DND) Toggle**:
  - `turn on dnd` / `mute notifications` — Instantly suppresses notifications across GNOME, Dunst, Mako, and SwayNC.
- **Quick Notes Vault**:
  - `note deploy server tomorrow` — Instant local scratchpad saved to markdown.
  - `list notes` / `clear notes` — Real-time notes viewer.
- **Sanitized Terminal Diagnostics**:
  - `diagnose terminal` — Analyzes your last terminal command and suggests fixes while scrubbing all API keys, bearer tokens, and passwords from history.

### 📱 4. Android Phone Mirroring & Remote Control
- **Ultra-Low Latency Wireless 2.0**: Direct wireless screen mirroring and input control powered by `scrcpy 5.0` with hardware-accelerated H.265 (HEVC) encoding.
- **Distance-Adaptive Buffering**: Dynamic jitter buffer (40ms–100ms) and adaptive bitrate scaling (3M $\rightarrow$ 2M $\rightarrow$ 1.5M) prevents packet drops across rooms.
- **Autonomous Reconnection Supervisor**: Auto-detects Wi-Fi packet drops, re-negotiates ADB TCP/IP connections, and rediscovers roaming device IPs on the fly.
- **Zero-Touch Automation**: Auto-unlocks phone via configured pin/passcode and enables Android OS low-latency Wi-Fi mode.

### ⚡ 5. Lexi Beam (AirDrop for Linux)
- **Zero-Cloud Cross-Device Transfer**: Peer-to-peer file transfer between Linux laptops and Android devices via local HTTP/SSDP.
- **Integrated Progressive Web App (`drop_web`)**: Any device on the local network can open `http://<laptop-ip>:7890` in Chrome/Safari to send/receive files instantly at wire speed.
- **Native Android Companion (`android_app`)**: Native Android APK (`com.lexi.connect`) supporting system-wide share intents and dynamic island notifications.
- **Path-Traversal & CSRF Hardened**: Kernel zero-copy streaming (`os.sendfile`) with strict directory boundary isolation and origin protection.

### 🤖 6. Universal Multi-Provider AI (Cloud & 100% Free Local Mode)
- **Interactive Provider Settings Dialog**: Accessible anytime via <kbd>Ctrl</kbd> + <kbd>,</kbd> or the `⚙️` header button.
- **Zero-Friction Provider Presets**:
  - **Ollama (100% Local / Free / Offline)**: Run offline on your CPU/GPU with zero API keys required (`llama3.2`, `qwen2.5`, `mistral`).
  - **Groq (Ultra-Fast LPU)**: Blazing sub-second inference with `llama-3.3-70b-versatile` & `llama-3.2-11b-vision-preview`.
  - **DeepSeek Direct**: High-throughput access to DeepSeek-V3 and DeepSeek-R1 with live reasoning indicators (`deepseek-flash`).
  - **OpenRouter**: Access 100+ models with multi-model fallback (`gemini-2.5-flash`, `deepseek`, `nemotron`).
  - **OpenAI Official**: Direct GPT-4o, GPT-4o-mini, and o-series reasoning models.
  - **OmniRoute**: Local resilient multi-provider gateway integration.
  - **Custom OpenAI-Compatible**: Connect to self-hosted vLLM, LM Studio, or LocalAI endpoints.
- **Non-Blocking Connection Tester**: Test latency and verify authentication live before saving.
- **Seamless In-Chat Slash Commands**:
  - `/settings` — Open provider settings modal.
  - `/provider <name>` — Switch between `ollama`, `groq`, `deepseek`, `openai`, `openrouter`, or `omniroute` on the fly.
  - `/model <name>` — Change the active model instantly.
  - `/key <token>` — Securely update credentials (stored locally with `0600` permissions).

---

## ⌨️ Global Keybindings

| Shortcut | Action |
|---|---|
| <kbd>Super</kbd> + <kbd>C</kbd> | Open Full-Screen Vision Copilot |
| <kbd>Super</kbd> + <kbd>Shift</kbd> + <kbd>C</kbd> | Interactive Region Snip |
| <kbd>Ctrl</kbd> + <kbd>,</kbd> / <kbd>Ctrl</kbd> + <kbd>P</kbd> | Open AI & Provider Settings Dialog |
| <kbd>Ctrl</kbd> + <kbd>L</kbd> | Clear Chat History |
| <kbd>Ctrl</kbd> + <kbd>B</kbd> | Toggle Vertical Side Panel Mode |
| <kbd>Ctrl</kbd> + <kbd>D</kbd> | Detach Lexi to Roam Desktop |
| <kbd>Ctrl</kbd> + <kbd>Alt</kbd> + <kbd>Space</kbd> | Alternative Copilot Toggle |
| <kbd>Ctrl</kbd> + <kbd>M</kbd> | Collapse to Dynamic Island Notch |
| <kbd>Esc</kbd> | Dismiss / Collapse to Notch |
| <kbd>Enter</kbd> | Submit Question / Prompt |

---

## 📁 Repository Structure

```
mochi-copilot/
├── lexi.py                    # Main GTK4 / Libadwaita app, Dynamic Island & Copilot
├── lexi_intents.py            # Layer 0 deterministic router (system controls, apps, tools)
├── lexi_productivity.py       # Developer power tools (Port killer, Process manager, DND, Notes)
├── lexi_downloads.py          # Dynamic Island live download throughput watcher
├── lexi_drop.py               # Lexi Beam P2P engine, SSDP discovery & PhoneMirrorManager
├── lexi_capabilities.py       # Typed capability registry & schema validation
├── lexi_apps.py               # Process inspection via /proc & graceful app manager
├── lexi_memory.py             # Context memory compaction & creator provenance
├── windows/                   # Native Windows Dynamic Island, ShareMe & 1-click installer
│   ├── lexi_win_island.py     # Windows PySide6 Dynamic Island notch & vision UI
│   ├── lexi_win_drop.py       # Windows ShareMe UDP discovery & zero-data TCP engine
│   ├── lexi_win_actions.py    # Windows Win32 action engine (volume, brightness, media)
│   └── install.ps1            # Windows 1-command automated PowerShell installer
├── install.sh                 # One-command automated installer for Linux
├── PKGBUILD                   # Official Arch Linux AUR package build script
├── requirements.txt           # Python package requirements
├── drop_web/                  # Progressive Web App for cross-device LAN file sharing
├── android_app/               # Native Android companion application (Lexi Connect)
└── tests/                     # Automated unit & security test suite (162+ tests)
```

---

## 🧪 Automated Test Suite

Lexi includes an exhaustive test suite covering app termination, browser bridge validation, scrcpy mirroring, intent routing, provider resolution, and security regression gates:

```bash
python3 -m unittest test_lexi_core tests.test_productivity_features tests.test_lexi_copilot_upgrades tests.test_security_remediation tests.test_phone_mirror tests.test_settings_and_providers tests.test_windows_parity
# 162 tests passed (0 failures)
```

---

## ⭐ Star History

If you love having a native Dynamic Island and AirDrop on Linux, give the repository a star! ⭐

[![Star History Chart](https://api.star-history.com/svg?repos=shakeel-777/lexi-copilot&type=Date)](https://star-history.com/#shakeel-777/lexi-copilot&Date)

---

## 📄 License

Licensed under the MIT License — see the [LICENSE](LICENSE) file for details.  
**Architect & Creator**: Shakeel
