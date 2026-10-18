# 🍡 Mochi Copilot

> A fast, cute, and production-grade AI desktop screen companion built natively for **Arch Linux** (GNOME 50 / Wayland).

[![Arch Linux](https://img.shields.io/badge/Arch_Linux-Wayland-1793D1?logo=arch-linux&logoColor=white)](https://archlinux.org)
[![GTK4 & Adwaita](https://img.shields.io/badge/GTK4-Libadwaita-3584e4?logo=gnome&logoColor=white)](https://gnome.org)
[![Python 3](https://img.shields.io/badge/Python-3.11+-3776AB?logo=python&logoColor=white)](https://python.org)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

Mochi brings multimodal vision intelligence to your desktop. Capture any screen area or window with a single keypress, attach up to 5 pages to compare documents or debug code, and converse seamlessly with your AI companion from a sleek, floating Dynamic Island anchored at the top of your screen.

---

## ✨ Features

- **🏝️ Coucou-Style Floating Island**: Anchored cleanly at the top-center of the screen right beneath GNOME's top panel, keeping your primary work area completely visible. Includes an interactive draggable header handle.
- **✂️ Interactive Region Snip (<kbd>Super</kbd> + <kbd>Shift</kbd> + <kbd>C</kbd>)**: Natively invokes GNOME Shell crosshairs without blocking clicks or freezing compositor surfaces.
- **🖥️ Full Screen Copilot (<kbd>Super</kbd> + <kbd>C</kbd>)**: One-touch full screen capture with a clean unmapping lifecycle.
- **📄 Multi-Page Attachment (Up to 5 Pages)**: Attach multiple windows, PDF pages, or terminal logs. Compare code diffs, logs vs implementation, or multi-step errors in a single prompt.
- **💬 Multi-Turn Conversational Memory**: Ask follow-up questions about your screen. Subsequent turns use cached text context—no redundant multi-megabyte image re-uploads.
- **🎨 Rich Markdown & Syntax Highlighting**: Custom parser renders headings, bullet lists, blockquotes, and syntax-highlighted code blocks (Python, Rust, Shell, JS, C, Go).
- **🛡️ Runaway Cost Protection**: Strict OpenRouter fallback chain (`google/gemini-2.5-flash-lite` $\rightarrow$ `nvidia/nemotron-3.5-super-vl:free`) with capped token limits ($0.000001 per query).
- **🪶 Lightweight & Native**: Built with PyGObject, Libadwaita, and XDG Desktop Portal. Zero Electron, zero WebKit—runs under 80MB of RAM.

---

## ⌨️ Keybindings

| Action | Shortcut |
|---|---|
| **Full Screen Copilot** | <kbd>Super</kbd> + <kbd>C</kbd> or <kbd>Ctrl</kbd> + <kbd>Alt</kbd> + <kbd>Space</kbd> |
| **Interactive Region Snip** | <kbd>Super</kbd> + <kbd>Shift</kbd> + <kbd>C</kbd> |
| **Minimize Mochi** | <kbd>Ctrl</kbd> + <kbd>M</kbd> or Header `_` Button |
| **Dismiss / Close** | <kbd>Esc</kbd> or Header `✕` Button |
| **Submit Question** | <kbd>Enter</kbd> |

---

## 🚀 Installation & Requirements

### Dependencies (Arch Linux)
```bash
sudo pacman -S gtk4 libadwaita python-pillow xdg-desktop-portal xdg-desktop-portal-gnome
```

### Installation
Clone the repository and link the executable:
```bash
git clone https://github.com/yourusername/mochi-copilot.git ~/.local/share/mochi-copilot
ln -sf ~/.local/share/mochi-copilot/mochi.py ~/.local/bin/mochi-copilot
chmod +x ~/.local/bin/mochi-copilot
```

Install desktop entry and icons:
```bash
cp ~/.local/share/mochi-copilot/mochi-copilot.svg ~/.local/share/icons/hicolor/scalable/apps/
cp ~/.local/share/mochi-copilot/org.antigravity.MochiCompanion.desktop ~/.local/share/applications/
update-desktop-database ~/.local/share/applications/
```

---

## ⚙️ Configuration

Configure your API key and preferences in `~/.local/share/mochi-copilot/config.json`:
```json
{
  "api_base": "https://openrouter.ai/api/v1",
  "api_key": "YOUR_OPENROUTER_API_KEY",
  "models": [
    "google/gemini-2.5-flash-lite",
    "nvidia/nemotron-3.5-super-vl:free"
  ],
  "max_tokens": 1000,
  "request_timeout": 35
}
```

---

## 📄 License
MIT License. Created by Shakeel.
