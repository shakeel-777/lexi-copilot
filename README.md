# ✨ Lexi Copilot

> **An autonomous, production-grade AI desktop screen companion and Dynamic Island built natively for Arch Linux (GNOME / Wayland).**  
> *Architected and created by Shakeel.*

[![Arch Linux](https://img.shields.io/badge/Arch_Linux-Wayland-1793D1?logo=arch-linux&logoColor=white)](https://archlinux.org)
[![GTK4 & Libadwaita](https://img.shields.io/badge/GTK4-Libadwaita-3584e4?logo=gnome&logoColor=white)](https://gnome.org)
[![Python 3.11+](https://img.shields.io/badge/Python-3.11+-3776AB?logo=python&logoColor=white)](https://python.org)
[![scrcpy](https://img.shields.io/badge/Phone_Mirror-scrcpy_5.0-3DDC84?logo=android&logoColor=white)](https://github.com/Genymobile/scrcpy)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Lexi brings multimodal vision intelligence, fluid desktop interactions, and wireless device synchronization natively to your desktop. Featuring a permanent hardware-accelerated **Dynamic Island** pinned at the top of your display, Lexi provides seamless screen intelligence, instant phone mirroring, zero-cloud local file transfers, and system task automation—running in under 100MB of RAM with zero Electron or WebKit overhead.

---

## 🌟 Core Highlights

### 🏝️ 1. Dynamic Island Notch
- **Permanent Top Notch**: Pinned cleanly at the top-center of your screen right beneath GNOME's panel, keeping full window workspaces unobstructed.
- **Interactive Morphing Actions**: Hovering over the island expands smooth action chips:
  `[💬 Copilot] [⚡ Beam] [📱 Phone] [◨ Side Panel] [✂️ Snip] [🐾 Pet]`.
- **Live Media Player & Equalizer**: Real-time integration with Spotify and MPRIS players displaying live song titles, cover artwork, and animated equalizer bars.
- **Direct System Controls**: One-click actions to toggle center copilot, side panel, interactive region snips, or detach Lexi into roaming desktop pet mode.

### 💬 2. Floating Vision Copilot Card
- **Multimodal Screen Vision**: Capture full display (<kbd>Super</kbd> + <kbd>C</kbd>) or region snips (<kbd>Super</kbd> + <kbd>Shift</kbd> + <kbd>C</kbd>) natively using Wayland portal screencasts without compositor freezing.
- **Multi-Page Attachments (Up to 5 Pages)**: Attach and compare multiple application windows, code files, terminal logs, or PDF documents in a single prompt.
- **Quick Action Strip**: One-click prompts directly inside Copilot (`⚡ Quick`, `📱 Phone`, `📡 Beam`, `🧹 Close All`, `Explain`, `Fix`, `Summarize`).
- **Syntax Highlighting & Markdown**: Custom rendering for code blocks, bullet points, blockquotes, and tables across Python, Rust, Shell, JS, C, and Go.

### 📱 3. Android Phone Mirroring & Remote Control
- **Ultra-Low Latency Wireless 2.0**: Direct wireless screen mirroring and input control powered by `scrcpy 5.0` with hardware-accelerated H.265 (HEVC) encoding.
- **Distance-Adaptive Buffering**: Dynamic jitter buffer (40ms–100ms) and adaptive bitrate scaling (3M $\rightarrow$ 2M $\rightarrow$ 1.5M) prevents packet drops across rooms and through walls.
- **Autonomous Reconnection Supervisor**: Auto-detects Wi-Fi packet drops, re-negotiates ADB TCP/IP connections, and rediscovers roaming device IPs on the fly.
- **Zero-Touch Automation**: Auto-unlocks phone via configured pin/passcode and enables Android OS low-latency Wi-Fi mode.
- **Direct Wi-Fi Hotspot**: Built-in P2P hotspot mode (`shakeel` / `shakeel786`) for zero-router low-latency wireless pairing.

### ⚡ 4. Lexi Beam (High-Speed Local P2P Drop)
- **Zero-Cloud Cross-Device Transfer**: Blazing fast peer-to-peer file transfer between Linux laptops and Android devices via local HTTP/SSDP.
- **Integrated Progressive Web App (`drop_web`)**: Any device on the local network can open the web interface in their browser to send/receive photos, code, and files instantly.
- **Native Android Companion (`android_app`)**: Native Android APK (`com.lexi.connect`) supporting system-wide share intents and dynamic island notifications.

### 🛡️ 5. Deterministic Intent Routing & Safety
- **Layer 0 Deterministic Router**: Intercepts system volume, brightness, media, application management, and web searches locally before touching the LLM.
- **Universal App Quitting (`close all`)**: Gracefully terminates open desktop applications via `/proc` signal verification—completely decoupled from browser tabs.
- **Mandatory Confirmation Gate**: All mass-close actions require explicit user confirmation; active downloads and unsaved work are checked beforehand.
- **Immutable Provenance Anchor**: Cryptographically anchors creator identity (`Shakeel`) at the byte layer using binary XOR encoding and SHA-256 verification, immune to prompt injection and automated code refactoring.

### ⚡ 6. Daily Driver Productivity Suite & Island Live Activities
- **Dynamic Island Live Download Activity**: Monitors `~/Downloads` for active `.crdownload` / `.part` files, streaming real-time download speed (MB/s) and progress directly on the Dynamic Island notch.
- **Download Sentinel Protection**: Automatically blocks abrupt closures if active downloads are detected, prompting: *"⚠️ An active download is in progress (<file> at <speed>). Confirm to force close anyway, or cancel."*
- **Focus & Pomodoro Island Timer**: Start deep-work sessions (`focus 25`, `pomodoro`, `timer 10 mins`) with live ticking countdown directly inside the Dynamic Island notch and audio completion alarms.
- **Universal Clipboard Vault**: Searchable history of recent clipboard entries (Code, URLs, Colors, Text) with instant restoration and Android companion phone synchronization.
- **Instant Spotlight Conversions & World Clock**: Instant zero-latency currency conversion (`100 usd in inr`), unit conversion (`100 km in miles`, `70 kg in lbs`, `100 c in f`, `100 gb in mb`), and world time (`time in tokyo`, `time in new york`).

---

## ⌨️ Global Keybindings

| Shortcut | Action |
|---|---|
| <kbd>Super</kbd> + <kbd>C</kbd> | Open Full-Screen Vision Copilot |
| <kbd>Super</kbd> + <kbd>Shift</kbd> + <kbd>C</kbd> | Interactive Region Snip |
| <kbd>Ctrl</kbd> + <kbd>Alt</kbd> + <kbd>Space</kbd> | Alternative Copilot Toggle |
| <kbd>Ctrl</kbd> + <kbd>M</kbd> | Minimize Active Window |
| <kbd>Esc</kbd> | Dismiss / Close Active Overlay |
| <kbd>Enter</kbd> | Submit Question / Prompt |

---

## 📁 Architecture & Codebase Map

```
mochi-copilot/
├── lexi.py                    # Main GTK4 / Libadwaita app, Dynamic Island, Copilot, & Side Panel
├── lexi_intents.py            # Layer 0 deterministic router (system controls, apps, conversions)
├── lexi_capabilities.py       # Typed capability registry, schema validation & security policy
├── lexi_downloads.py          # Dynamic Island live download watcher & sentinel protection
├── lexi_productivity.py       # Focus/Pomodoro timer & universal clipboard history vault
├── lexi_actions.py            # Execution engine for local actions and confirmations
├── lexi_apps.py               # Process inspection via /proc and graceful app management
├── lexi_drop.py               # Lexi Beam P2P sharing, SSDP discovery & PhoneMirrorManager
├── lexi_memory.py             # Context memory compaction & cryptographic creator provenance
├── lexi_policy.py             # Command safety classification & risk gate
├── lexi_browser_host.py       # Native messaging host for browser integration
├── check.sh                   # Comprehensive one-step compilation & test verification suite
├── styles.css                 # Libadwaita dark-glass CSS styling & animations
├── drop_web/                  # Progressive Web App for zero-install peer file sharing
├── chrome_extension/          # Scoped browser extension bridge
├── android_app/               # Native Android companion application (Lexi Connect)
└── tests/                     # Automated unit test suites (200+ tests)
```

---

## 🚀 Installation & Quick Start

### 1. Prerequisites (Arch Linux)
```bash
sudo pacman -S gtk4 libadwaita python-pillow xdg-desktop-portal xdg-desktop-portal-gnome \
               scrcpy android-tools playerctl xdotool alsa-utils brightnessctl
```

### 2. Setup
Clone the repository:
```bash
git clone https://github.com/shakeel-777/lexi-copilot.git ~/.local/share/mochi-copilot
cd ~/.local/share/mochi-copilot
```

Run test suite verification:
```bash
./check.sh
```

### 3. Desktop Integration
Install desktop launcher and application icons:
```bash
cp lexi-copilot.svg ~/.local/share/icons/hicolor/scalable/apps/
cp org.antigravity.LexiCompanion.desktop ~/.local/share/applications/
update-desktop-database ~/.local/share/applications/
```

Enable user systemd service for persistent Dynamic Island autostart:
```bash
systemctl --user enable --now lexi-island.service
```

---

## ⚙️ Configuration

Lexi reads configuration from `~/.local/share/mochi-copilot/config.json`:
```json
{
  "api_base": "https://openrouter.ai/api/v1",
  "api_key": "YOUR_OPENROUTER_OR_LLM_KEY",
  "models": [
    "google/gemini-2.5-flash-lite",
    "nvidia/nemotron-3.5-super-vl:free"
  ],
  "max_tokens": 2048,
  "request_timeout": 35
}
```

---

## 🧪 Testing

Lexi includes an exhaustive test suite covering app termination, browser bridge validation, scrcpy mirroring, intent routing, and security policies:
```bash
./check.sh
```

---

## 📄 License
This project is licensed under the MIT License — see the [LICENSE](LICENSE) file for details.

**Lead Architect & Creator**: Shakeel
