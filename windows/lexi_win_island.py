#!/usr/bin/env python3
"""
Lexi Dynamic Island for Windows (Full Feature Parity with Linux)
================================================================
Native top-docked notch companion powered by PySide6 (Qt).
- Frameless, translucent dark-glass acrylic styling (y = 0 top dock)
- Liquid morphing pills on hover: [💬 Copilot] [⚡ Beam] [📱 Phone] [◨ Side Panel] [✂️ Snip] [🐾 Pet] [⚙️ Settings]
- Live Media Playback: Title, artist, animated equalizer visualizer & controls [⏮] [⏯] [⏭]
- Live Downloads Sentinel: Active downloads throughput (MB/s) & progress in notch
- Phone Mirroring: 1-Click wireless & USB scrcpy phone mirror with auto-reconnect
- Multimodal Copilot: Reasoning tokens, screen vision, multi-file attachments (up to 5), quick actions
- Side Panel Mode: Vertical screen-edge docking
- Detached Roaming Desktop Pet Mascot
- Interactive Screen Region Snip overlay
- Multi-provider settings dialog (DeepSeek, Groq, Ollama, OpenRouter, OpenAI, OmniRoute)
- Zero-data ShareMe LAN P2P Beam Radar
"""

import os
import sys
import json
import time
import base64
import ctypes
import threading
import subprocess
import urllib.request
import urllib.parse
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple

from PySide6.QtCore import Qt, QTimer, QPoint, QSize, Signal, QObject, QThread, QRect, QUrl
from PySide6.QtGui import (
    QColor, QFont, QPainter, QBrush, QPen, QLinearGradient, QAction, QIcon,
    QKeySequence, QPixmap, QCursor, QGuiApplication, QDesktopServices
)
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QTextEdit, QScrollArea, QFrame,
    QFileDialog, QSystemTrayIcon, QMenu, QGraphicsDropShadowEffect,
    QDialog, QComboBox, QCheckBox, QProgressBar, QMessageBox, QTabWidget
)

from lexi_win_actions import SystemActionEngineWindows, PhoneMirrorManagerWindows
from lexi_win_drop import LexiWinDropServer, format_size

CONFIG_FILE = Path(os.path.expanduser("~/.lexi_config.json"))

DEFAULT_CONFIG = {
    "provider": "deepseek",
    "api_base": "https://api.deepseek.com",
    "api_key": "",
    "model": "deepseek-flash",
    "groq_api_base": "https://api.groq.com/openai/v1",
    "groq_api_key": "",
    "groq_model": "llama-3.3-70b-versatile",
    "ollama_api_base": "http://localhost:11434/v1",
    "ollama_model": "llama3.2",
    "max_tokens": 4096,
    "phone_password": "",
    "system_prompt": (
        "You are Lexi, an intelligent autonomous desktop AI companion on Windows created by Shakeel.\n"
        "You can control the computer, open apps/files/URLs, control media & volume, mirror Android phones, and analyze screens.\n"
        "Keep answers concise, direct, and conversational."
    )
}

def load_config() -> Dict[str, Any]:
    cfg = dict(DEFAULT_CONFIG)
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                cfg.update(json.load(f))
        except Exception:
            pass
    return cfg

def save_config(cfg: Dict[str, Any]):
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=4)
    except Exception as e:
        print(f"Error saving config: {e}")

class StreamWorker(QThread):
    token_received = Signal(str)
    reasoning_received = Signal(str)
    finished = Signal(str)
    error = Signal(str)

    def __init__(self, config: Dict[str, Any], prompt: str, images_b64: Optional[List[str]] = None):
        super().__init__()
        self.config = config
        self.prompt = prompt
        self.images_b64 = images_b64 or []

        provider = (self.config.get("provider") or "deepseek").lower()
        if provider == "groq":
            self.api_base = (self.config.get("groq_api_base") or "https://api.groq.com/openai/v1").rstrip("/")
            self.api_key = self.config.get("groq_api_key") or self.config.get("api_key") or ""
            self.model = self.config.get("groq_model") or self.config.get("model") or "llama-3.3-70b-versatile"
            if self.images_b64 and "vision" not in self.model.lower():
                self.model = "llama-3.2-11b-vision-preview"
        elif provider == "ollama":
            self.api_base = (self.config.get("ollama_api_base") or "http://localhost:11434/v1").rstrip("/")
            self.api_key = "ollama"
            self.model = self.config.get("ollama_model") or "llama3.2"
        else:
            self.api_key = self.config.get("api_key") or self.config.get("deepseek_api_key") or ""
            self.api_base = (self.config.get("api_base") or "https://api.deepseek.com").rstrip("/")
            self.model = self.config.get("model") or "deepseek-flash"

    def run(self):
        messages = [{"role": "system", "content": self.config.get("system_prompt", "")}]
        if self.images_b64:
            user_content = [{"type": "text", "text": self.prompt or "What is on my screen / in these images?"}]
            for img in self.images_b64:
                user_content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img}"}})
            messages.append({"role": "user", "content": user_content})
        else:
            messages.append({"role": "user", "content": self.prompt})

        payload = {
            "model": self.model,
            "messages": messages,
            "stream": True,
            "max_tokens": self.config.get("max_tokens", 4096)
        }

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}"
        }

        url = f"{self.api_base}/chat/completions"
        full_text = []

        try:
            req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers)
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(req, timeout=35) as resp:
                for line in resp:
                    line = line.decode("utf-8").strip()
                    if line.startswith("data: ") and line != "data: [DONE]":
                        try:
                            chunk = json.loads(line[6:])
                            delta_obj = chunk["choices"][0]["delta"]
                            delta = delta_obj.get("content") or ""
                            reasoning = delta_obj.get("reasoning_content") or ""
                            if reasoning:
                                self.reasoning_received.emit(reasoning)
                            if delta:
                                full_text.append(delta)
                                self.token_received.emit(delta)
                        except Exception:
                            pass
            self.finished.emit("".join(full_text))
        except Exception as e:
            self.error.emit(str(e))


class WinGlobalHotkeyListener(QThread):
    """
    Background daemon thread listening for global Windows system hotkeys:
    - Win + 3 / Ctrl + Shift + C -> Toggle Floating Copilot
    - Win + Shift + S / Ctrl + Shift + S -> Region Snip
    - F1 / Ctrl + Shift + H -> User Guide & Diagnostics
    - Ctrl + Shift + B -> Toggle Side Panel
    """
    hotkey_copilot = Signal()
    hotkey_snip = Signal()
    hotkey_guide = Signal()
    hotkey_side = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._running = True
        self._thread_id = None

    def run(self):
        if sys.platform != "win32":
            return

        try:
            import ctypes
            from ctypes import wintypes
            user32 = ctypes.windll.user32
            kernel32 = ctypes.windll.kernel32

            self._thread_id = kernel32.GetCurrentThreadId()

            MOD_ALT = 0x0001
            MOD_CONTROL = 0x0002
            MOD_SHIFT = 0x0004
            MOD_WIN = 0x0008
            MOD_NOREPEAT = 0x4000

            HK_COPILOT_WIN = 101   # Win + 3
            HK_COPILOT_CTRL = 102  # Ctrl + Shift + C
            HK_SNIP_WIN = 103      # Win + Shift + S
            HK_SNIP_CTRL = 104     # Ctrl + Shift + S
            HK_GUIDE_F1 = 105      # F1
            HK_GUIDE_CTRL = 106    # Ctrl + Shift + H
            HK_SIDE_CTRL = 107     # Ctrl + Shift + B

            # Register hotkeys
            user32.RegisterHotKey(None, HK_COPILOT_WIN, MOD_WIN | MOD_NOREPEAT, 0x33)           # '3'
            user32.RegisterHotKey(None, HK_COPILOT_CTRL, MOD_CONTROL | MOD_SHIFT | MOD_NOREPEAT, 0x43) # 'C'
            user32.RegisterHotKey(None, HK_SNIP_WIN, MOD_WIN | MOD_SHIFT | MOD_NOREPEAT, 0x53)   # 'S'
            user32.RegisterHotKey(None, HK_SNIP_CTRL, MOD_CONTROL | MOD_SHIFT | MOD_NOREPEAT, 0x53)  # 'S'
            user32.RegisterHotKey(None, HK_GUIDE_F1, MOD_NOREPEAT, 0x70)                       # VK_F1
            user32.RegisterHotKey(None, HK_GUIDE_CTRL, MOD_CONTROL | MOD_SHIFT | MOD_NOREPEAT, 0x48) # 'H'
            user32.RegisterHotKey(None, HK_SIDE_CTRL, MOD_CONTROL | MOD_SHIFT | MOD_NOREPEAT, 0x42)  # 'B'

            msg = wintypes.MSG()
            while self._running:
                res = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
                if res == 0 or res == -1:
                    break

                if msg.message == 0x0312:  # WM_HOTKEY
                    hk_id = msg.wParam
                    if hk_id in (HK_COPILOT_WIN, HK_COPILOT_CTRL):
                        self.hotkey_copilot.emit()
                    elif hk_id in (HK_SNIP_WIN, HK_SNIP_CTRL):
                        self.hotkey_snip.emit()
                    elif hk_id in (HK_GUIDE_F1, HK_GUIDE_CTRL):
                        self.hotkey_guide.emit()
                    elif hk_id == HK_SIDE_CTRL:
                        self.hotkey_side.emit()

                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))

            for hid in (HK_COPILOT_WIN, HK_COPILOT_CTRL, HK_SNIP_WIN, HK_SNIP_CTRL, HK_GUIDE_F1, HK_GUIDE_CTRL, HK_SIDE_CTRL):
                user32.UnregisterHotKey(None, hid)

        except Exception:
            pass

    def stop(self):
        self._running = False
        if sys.platform == "win32" and self._thread_id:
            try:
                import ctypes
                ctypes.windll.user32.PostThreadMessageW(self._thread_id, 0x0012, 0, 0)  # WM_QUIT
            except Exception:
                pass
        self.wait(1000)


class LexiSnipOverlay(QWidget):
    """Interactive transparent screen snip overlay with rubber-band crosshair."""
    snip_captured = Signal(str)

    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.start_pos = None
        self.current_pos = None

    def start_capture(self):
        screen = QApplication.primaryScreen()
        geo = screen.geometry()
        self.setGeometry(geo)
        self.start_pos = None
        self.current_pos = None
        self.showFullScreen()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.start_pos = event.pos()
            self.current_pos = event.pos()
            self.update()

    def mouseMoveEvent(self, event):
        if self.start_pos:
            self.current_pos = event.pos()
            self.update()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.start_pos and self.current_pos:
            rect = QRect(self.start_pos, self.current_pos).normalized()
            self.hide()
            if rect.width() > 10 and rect.height() > 10:
                screen = QApplication.primaryScreen()
                pix = screen.grabWindow(0, rect.x(), rect.y(), rect.width(), rect.height())
                tmp = Path(os.path.expanduser("~/AppData/Local/Temp/lexi_snip_region.jpg"))
                tmp.parent.mkdir(parents=True, exist_ok=True)
                pix.save(str(tmp), "JPEG", 90)
                with open(tmp, "rb") as f:
                    b64 = base64.b64encode(f.read()).decode("utf-8")
                self.snip_captured.emit(b64)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(0, 0, 0, 100))
        if self.start_pos and self.current_pos:
            rect = QRect(self.start_pos, self.current_pos).normalized()
            painter.setCompositionMode(QPainter.CompositionMode_Clear)
            painter.fillRect(rect, Qt.GlobalColor.transparent)
            painter.setCompositionMode(QPainter.CompositionMode_SourceOver)
            pen = QPen(QColor(99, 102, 241), 2)
            painter.setPen(pen)
            painter.drawRect(rect)

class LexiPetWidget(QWidget):
    """Detached cute roaming anime desktop pet mascot."""
    def __init__(self, on_activate, on_dock):
        super().__init__()
        self.on_activate = on_activate
        self.on_dock = on_dock
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.drag_pos = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)

        self.label = QLabel("✨")
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.label.setStyleSheet(
            "background: rgba(13, 15, 22, 0.88);"
            "border: 2px solid rgba(99, 102, 241, 0.6);"
            "border-radius: 28px;"
            "font-size: 24px;"
            "min-width: 56px;"
            "min-height: 56px;"
        )
        layout.addWidget(self.label)
        self.resize(68, 68)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
        elif event.button() == Qt.MouseButton.RightButton:
            menu = QMenu(self)
            a_copilot = menu.addAction("💬 Open Copilot")
            a_phone = menu.addAction("📱 Mirror Phone")
            a_dock = menu.addAction("🏝️ Dock to Dynamic Island")
            act = menu.exec(QCursor.pos())
            if act == a_copilot:
                self.on_activate()
            elif act == a_phone:
                SystemActionEngineWindows.get().start_phone_mirror()
            elif act == a_dock:
                self.hide()
                self.on_dock()

    def mouseMoveEvent(self, event):
        if self.drag_pos and event.buttons() == Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self.drag_pos)

    def mouseDoubleClickEvent(self, event):
        self.on_activate()

class LexiSettingsDialog(QDialog):
    """Multi-Provider AI Configuration Dialog with live connection tester."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("⚙️ Lexi AI Provider Settings")
        self.setFixedSize(460, 420)
        self.setStyleSheet("""
            QDialog { background-color: #0d0f16; color: #e2e8f0; }
            QLabel { color: #cbd5e1; font-size: 12px; }
            QLineEdit, QComboBox {
                background: rgba(255, 255, 255, 0.07);
                color: #fff;
                border: 1px solid rgba(255, 255, 255, 0.15);
                border-radius: 6px;
                padding: 6px 10px;
                font-size: 13px;
            }
            QPushButton {
                background: #6366f1;
                color: white;
                border: none;
                border-radius: 6px;
                padding: 7px 14px;
                font-size: 12px;
                font-weight: bold;
            }
            QPushButton:hover { background: #4f46e5; }
        """)

        self.cfg = load_config()
        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        # Provider Selector
        layout.addWidget(QLabel("<b>AI Provider:</b>"))
        self.prov_combo = QComboBox()
        self.prov_combo.addItems(["DeepSeek Direct", "Groq LPU (Ultra-Fast)", "Ollama (100% Local / Free)", "OpenRouter", "OpenAI Official"])
        current_prov = (self.cfg.get("provider") or "deepseek").lower()
        if current_prov == "groq":
            self.prov_combo.setCurrentIndex(1)
        elif current_prov == "ollama":
            self.prov_combo.setCurrentIndex(2)
        elif current_prov == "openrouter":
            self.prov_combo.setCurrentIndex(3)
        elif current_prov == "openai":
            self.prov_combo.setCurrentIndex(4)
        else:
            self.prov_combo.setCurrentIndex(0)
        self.prov_combo.currentIndexChanged.connect(self.on_prov_changed)
        layout.addWidget(self.prov_combo)

        # API Base
        layout.addWidget(QLabel("<b>API Base URL:</b>"))
        self.base_input = QLineEdit()
        layout.addWidget(self.base_input)

        # API Key
        layout.addWidget(QLabel("<b>API Key:</b>"))
        self.key_input = QLineEdit()
        self.key_input.setEchoMode(QLineEdit.EchoMode.Password)
        layout.addWidget(self.key_input)

        # Model
        layout.addWidget(QLabel("<b>Model Name:</b>"))
        self.model_input = QLineEdit()
        layout.addWidget(self.model_input)

        # Phone Password for Zero-Touch Unlock
        layout.addWidget(QLabel("<b>Phone PIN/Password (for Zero-Touch Unlock):</b>"))
        self.phone_pw_input = QLineEdit()
        self.phone_pw_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.phone_pw_input.setText(self.cfg.get("phone_password", ""))
        layout.addWidget(self.phone_pw_input)

        # Test Connection & Status
        self.test_lbl = QLabel("")
        layout.addWidget(self.test_lbl)

        btn_row = QHBoxLayout()
        self.test_btn = QPushButton("⚡ Test Connection")
        self.test_btn.setStyleSheet("background: rgba(255, 255, 255, 0.1); color: #fff;")
        self.test_btn.clicked.connect(self.test_connection)
        btn_row.addWidget(self.test_btn)

        self.save_btn = QPushButton("Save & Apply")
        self.save_btn.clicked.connect(self.save_and_close)
        btn_row.addWidget(self.save_btn)
        layout.addLayout(btn_row)

        self.on_prov_changed()

    def on_prov_changed(self):
        idx = self.prov_combo.currentIndex()
        if idx == 1:  # Groq
            self.base_input.setText(self.cfg.get("groq_api_base") or "https://api.groq.com/openai/v1")
            self.key_input.setText(self.cfg.get("groq_api_key") or "")
            self.model_input.setText(self.cfg.get("groq_model") or "llama-3.3-70b-versatile")
        elif idx == 2:  # Ollama
            self.base_input.setText(self.cfg.get("ollama_api_base") or "http://localhost:11434/v1")
            self.key_input.setText("ollama")
            self.model_input.setText(self.cfg.get("ollama_model") or "llama3.2")
        elif idx == 3:  # OpenRouter
            self.base_input.setText("https://openrouter.ai/api/v1")
            self.key_input.setText(self.cfg.get("api_key") or "")
            self.model_input.setText("deepseek/deepseek-v4.1-flash")
        elif idx == 4:  # OpenAI
            self.base_input.setText("https://api.openai.com/v1")
            self.key_input.setText(self.cfg.get("api_key") or "")
            self.model_input.setText("gpt-4o")
        else:  # DeepSeek
            self.base_input.setText(self.cfg.get("api_base") or "https://api.deepseek.com")
            self.key_input.setText(self.cfg.get("api_key") or "")
            self.model_input.setText(self.cfg.get("model") or "deepseek-flash")

    def test_connection(self):
        self.test_lbl.setText("Testing endpoint latency...")
        url = f"{self.base_input.text().rstrip('/')}/models"
        headers = {"Authorization": f"Bearer {self.key_input.text().strip()}"}
        try:
            t0 = time.time()
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=5) as resp:
                ms = int((time.time() - t0) * 1000)
                self.test_lbl.setStyleSheet("color: #10b981;")
                self.test_lbl.setText(f"✓ Connected successfully! ({ms} ms latency)")
        except Exception as e:
            self.test_lbl.setStyleSheet("color: #ef4444;")
            self.test_lbl.setText(f"⚠️ Connection error: {e}")

    def save_and_close(self):
        idx = self.prov_combo.currentIndex()
        prov_map = {0: "deepseek", 1: "groq", 2: "ollama", 3: "openrouter", 4: "openai"}
        prov = prov_map.get(idx, "deepseek")
        self.cfg["provider"] = prov
        self.cfg["phone_password"] = self.phone_pw_input.text().strip()

        if prov == "groq":
            self.cfg["groq_api_base"] = self.base_input.text().strip()
            self.cfg["groq_api_key"] = self.key_input.text().strip()
            self.cfg["groq_model"] = self.model_input.text().strip()
        elif prov == "ollama":
            self.cfg["ollama_api_base"] = self.base_input.text().strip()
            self.cfg["ollama_model"] = self.model_input.text().strip()
        else:
            self.cfg["api_base"] = self.base_input.text().strip()
            self.cfg["api_key"] = self.key_input.text().strip()
            self.cfg["model"] = self.model_input.text().strip()

        save_config(self.cfg)
        self.accept()


class LexiBeamDialog(QDialog):
    """ShareMe P2P Radar and Zero-Data Local Transfer window."""
    def __init__(self, drop_server: LexiWinDropServer, parent=None):
        super().__init__(parent)
        self.drop_server = drop_server
        self.setWindowTitle("⚡ Lexi ShareMe Beam (Zero-Data P2P)")
        self.setFixedSize(500, 380)
        self.setStyleSheet("""
            QDialog { background-color: #0d0f16; color: #e2e8f0; }
            QLabel { color: #cbd5e1; font-size: 13px; }
            QPushButton {
                background: #6366f1;
                color: white;
                border: none;
                border-radius: 6px;
                padding: 8px 16px;
                font-size: 12px;
                font-weight: bold;
            }
            QPushButton:hover { background: #4f46e5; }
        """)

        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        badge = QLabel("🟢 Direct Local Wi-Fi · 0 MB Internet Data Used")
        badge.setStyleSheet("""
            background: rgba(16, 185, 129, 0.15);
            color: #10b981;
            border: 1px solid rgba(16, 185, 129, 0.3);
            border-radius: 8px;
            padding: 6px 12px;
            font-weight: bold;
            font-size: 12px;
        """)
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(badge)

        self.link_lbl = QLabel(f"Web Receiver URL: <b>http://{self.drop_server.local_ip}:{self.drop_server.port}</b>")
        layout.addWidget(self.link_lbl)

        self.peers_lbl = QLabel("<b>Discovered LAN Devices (Linux, Windows, Android):</b>")
        layout.addWidget(self.peers_lbl)

        self.peers_box = QTextEdit()
        self.peers_box.setReadOnly(True)
        self.peers_box.setStyleSheet("background: rgba(255, 255, 255, 0.05); border: 1px solid rgba(255, 255, 255, 0.1); border-radius: 6px;")
        layout.addWidget(self.peers_box)

        btn_row = QHBoxLayout()
        self.send_btn = QPushButton("📁 Select Movie / File to Beam")
        self.send_btn.clicked.connect(self.send_file)
        btn_row.addWidget(self.send_btn)

        self.copy_btn = QPushButton("📋 Copy Link")
        self.copy_btn.setStyleSheet("background: rgba(255, 255, 255, 0.1); color: #fff;")
        self.copy_btn.clicked.connect(self.copy_link)
        btn_row.addWidget(self.copy_btn)

        layout.addLayout(btn_row)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh_peers)
        self.timer.start(1500)
        self.refresh_peers()

    def refresh_peers(self):
        peers = list(self.drop_server.beacon.lan_peers.values())
        if not peers:
            self.peers_box.setText("<i>Scanning local Wi-Fi for nearby Linux/Windows/Android peers...</i>")
        else:
            lines = [f"• <b>{p.get('hostname')}</b> ({p.get('ip')}) — OS: {p.get('os', 'unknown').upper()}" for p in peers]
            self.peers_box.setHtml("<br>".join(lines))

    def copy_link(self):
        url = f"http://{self.drop_server.local_ip}:{self.drop_server.port}"
        QApplication.clipboard().setText(url)
        self.copy_btn.setText("✓ Copied!")
        QTimer.singleShot(2000, lambda: self.copy_btn.setText("📋 Copy Link"))

    def send_file(self):
        fpath, _ = QFileDialog.getOpenFileName(self, "Select Movie or File to Beam")
        if not fpath:
            return
        peers = list(self.drop_server.beacon.lan_peers.values())
        if not peers:
            QMessageBox.information(self, "Share via Web", f"No auto-discovered peer found. Tell your friend to open:\nhttp://{self.drop_server.local_ip}:{self.drop_server.port}\nin their browser!")
            return
        target = peers[0]
        self.drop_server.beam_file_to_peer(fpath, target["ip"])
        QMessageBox.information(self, "Beam Started", f"Beaming '{os.path.basename(fpath)}' directly to {target['hostname']} at wire speed!")


class LexiGuideDialog(QDialog):
    """
    Comprehensive User Guide, Cheat Sheet, and System Self-Test Diagnostics for Windows.
    Empowers users to easily discover and operate all Linux-parity features on Windows.
    """
    def __init__(self, actions: SystemActionEngineWindows, parent=None):
        super().__init__(parent)
        self.actions = actions
        self.setWindowTitle("✨ Lexi Copilot — User Guide & Diagnostics")
        self.resize(720, 600)
        self.setStyleSheet("""
            QDialog {
                background-color: #0b0d14;
                color: #e2e8f0;
                font-family: 'Segoe UI', system-ui, sans-serif;
            }
            QTabWidget::pane {
                border: 1px solid rgba(168, 85, 247, 0.25);
                border-radius: 10px;
                background-color: #0f121d;
                padding: 10px;
            }
            QTabBar::tab {
                background: rgba(255, 255, 255, 0.05);
                color: #94a3b8;
                border: 1px solid rgba(255, 255, 255, 0.08);
                padding: 8px 14px;
                border-top-left-radius: 8px;
                border-top-right-radius: 8px;
                font-size: 12px;
                font-weight: bold;
                margin-right: 4px;
            }
            QTabBar::tab:selected {
                background: rgba(168, 85, 247, 0.22);
                color: #ffffff;
                border-bottom: 2px solid #a855f7;
            }
            QTextEdit {
                background: #090b10;
                color: #cbd5e1;
                border: 1px solid rgba(255, 255, 255, 0.07);
                border-radius: 8px;
                padding: 12px;
                font-size: 13px;
                line-height: 1.5;
            }
            QPushButton {
                background: rgba(255, 255, 255, 0.08);
                color: #f1f5f9;
                border: 1px solid rgba(255, 255, 255, 0.16);
                border-radius: 8px;
                padding: 7px 14px;
                font-size: 12px;
                font-weight: bold;
            }
            QPushButton:hover {
                background: rgba(168, 85, 247, 0.25);
                color: #ffffff;
                border-color: rgba(168, 85, 247, 0.5);
            }
            QPushButton#primaryAction {
                background: #a855f7;
                color: #09090b;
                border: 1px solid #c084fc;
            }
            QPushButton#primaryAction:hover {
                background: #c084fc;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(12)

        # Header Title
        hdr = QHBoxLayout()
        title_box = QVBoxLayout()
        title = QLabel("✨ Lexi Copilot — Complete User Guide")
        title.setStyleSheet("font-size: 18px; font-weight: bold; color: #ffffff;")
        subtitle = QLabel("Hardware-Accelerated Dynamic Island, 60 FPS Phone Mirror & ShareMe on Windows")
        subtitle.setStyleSheet("font-size: 12px; color: #a1a1aa;")
        title_box.addWidget(title)
        title_box.addWidget(subtitle)
        hdr.addLayout(title_box)
        hdr.addStretch()
        layout.addLayout(hdr)

        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)

        self.init_quickstart_tab()
        self.init_mirror_tab()
        self.init_beam_tab()
        self.init_commands_tab()
        self.init_diagnostics_tab()

        # Bottom Bar
        btn_box = QHBoxLayout()
        btn_box.addStretch()
        done_btn = QPushButton("Done")
        done_btn.clicked.connect(self.accept)
        btn_box.addWidget(done_btn)
        layout.addLayout(btn_box)

    def init_quickstart_tab(self):
        tab = QWidget()
        v = QVBoxLayout(tab)
        v.setSpacing(10)

        txt = QTextEdit()
        txt.setReadOnly(True)
        txt.setHtml("""
            <div style="font-family: 'Segoe UI', sans-serif;">
                <h3 style="color: #c084fc; margin-top: 0;">🏝️ 1. The Dynamic Island Notch</h3>
                <p>Lexi floats permanently as an OLED pure black capsule notch at the top-center of your screen, keeping your workspace 100% unobstructed.</p>
                <ul>
                    <li><b>Hover to Reveal Actions:</b> Move your cursor over the notch to expand instant tools: <code>💬 Copilot</code>, <code>⚡ Beam</code>, <code>📱 Phone</code>, <code>◨ Side</code>, <code>✂️ Snip</code>, <code>🐾 Pet</code>, and <code>⚙️ Settings</code>.</li>
                    <li><b>Live Media Player:</b> When playing Spotify or web music, the island expands to show current song info with live <code>⏮ ⏯ ⏭</code> controls.</li>
                    <li><b>Live Download Progress:</b> Monitors active downloads (.crdownload, .part) and displays live transfer throughput (MB/s).</li>
                    <li><b>Desktop Pet Mode:</b> Click <code>🐾 Pet</code> to detach Lexi into an animated desktop mascot. Double-click the pet to dock back!</li>
                </ul>

                <h3 style="color: #c084fc;">⌨️ 2. Global Windows Shortcuts</h3>
                <table border="0" cellpadding="6" style="color: #cbd5e1; font-size: 12px; background: rgba(255,255,255,0.03); border-radius: 8px;">
                    <tr><td><b style="color: #a855f7;">Win + 3</b> or <b>Ctrl + Shift + C</b></td><td>Toggle Floating Vision Copilot anywhere</td></tr>
                    <tr><td><b style="color: #a855f7;">Win + Shift + S</b> or <b>Ctrl + Shift + S</b></td><td>Interactive Screen Region Snip</td></tr>
                    <tr><td><b style="color: #a855f7;">Ctrl + Shift + B</b></td><td>Toggle Right Vertical Side Panel Mode</td></tr>
                    <tr><td><b style="color: #a855f7;">Ctrl + ,</b></td><td>Open AI Provider Settings Modal</td></tr>
                    <tr><td><b style="color: #a855f7;">F1</b> or <b>Ctrl + Shift + H</b></td><td>Open this User Guide & Diagnostics</td></tr>
                    <tr><td><b style="color: #a855f7;">Esc</b></td><td>Dismiss Copilot back into top island</td></tr>
                </table>
            </div>
        """)
        v.addWidget(txt)
        self.tabs.addTab(tab, "🚀 Quick Start & Shortcuts")

    def init_mirror_tab(self):
        tab = QWidget()
        v = QVBoxLayout(tab)
        v.setSpacing(10)

        txt = QTextEdit()
        txt.setReadOnly(True)
        txt.setHtml("""
            <div style="font-family: 'Segoe UI', sans-serif;">
                <h3 style="color: #4ade80; margin-top: 0;">📱 60 FPS Wireless Phone Mirroring (scrcpy)</h3>
                <p>Control your Android phone directly from Windows with zero-lag 60 FPS screen mirroring, clipboard sync, and drag-and-drop file transfer.</p>

                <h4 style="color: #f1f5f9;">Quick 30-Second Setup:</h4>
                <ol>
                    <li>Connect your Android phone to this PC via USB cable.</li>
                    <li>On your phone, enable <b>USB Debugging</b> in Developer Options.<br>
                        <i>(To unlock Developer Options: Go to Phone Settings → About Phone → Tap 'Build Number' 7 times).</i></li>
                    <li>When prompted on your phone screen, check <b>"Always allow from this computer"</b> and tap Allow.</li>
                    <li>Click <b>Launch Phone Mirror</b> below.</li>
                    <li>To go wireless: Click <b>Enable Wireless ADB</b> below, then unplug your USB cable!</li>
                </ol>
            </div>
        """)
        v.addWidget(txt)

        btn_row = QHBoxLayout()
        launch_btn = QPushButton("📱 Launch Phone Mirror")
        launch_btn.setObjectName("primaryAction")
        launch_btn.clicked.connect(self.launch_mirror)
        btn_row.addWidget(launch_btn)

        wireless_btn = QPushButton("📶 Enable Wireless ADB (Port 5555)")
        wireless_btn.clicked.connect(self.enable_wireless)
        btn_row.addWidget(wireless_btn)
        btn_row.addStretch()

        v.addLayout(btn_row)
        self.tabs.addTab(tab, "📱 Phone Mirror (scrcpy)")

    def init_beam_tab(self):
        tab = QWidget()
        v = QVBoxLayout(tab)
        v.setSpacing(10)

        diag = self.actions.run_system_diagnostics()
        local_ip = diag.get("network", {}).get("ip", "127.0.0.1")
        web_url = f"http://{local_ip}:7890"

        txt = QTextEdit()
        txt.setReadOnly(True)
        txt.setHtml(f"""
            <div style="font-family: 'Segoe UI', sans-serif;">
                <h3 style="color: #38bdf8; margin-top: 0;">⚡ Lexi Beam — Local Wi-Fi AirDrop</h3>
                <p>Instant peer-to-peer file transfer across Windows, Linux, Android, iPhone, and Mac at raw Wi-Fi speed (100–300 MB/s). No cloud, no size limits, 100% private.</p>

                <h4 style="color: #f1f5f9;">Two Ways to Share:</h4>
                <ul>
                    <li><b>PC to Nearby Devices:</b> Click <code>⚡ Beam</code> on the island or Copilot card to choose a file or drag-and-drop.</li>
                    <li><b>Phone/Device to PC:</b> Open the local web portal on any phone or laptop on your Wi-Fi:</li>
                </ul>
                <div style="background: rgba(56, 189, 248, 0.12); border: 1px solid rgba(56, 189, 248, 0.3); border-radius: 8px; padding: 10px 14px; margin: 8px 0;">
                    <span style="font-size: 11px; color: #94a3b8;">Local Wi-Fi Drop Portal URL:</span><br>
                    <b style="font-size: 15px; color: #38bdf8;">{web_url}</b>
                </div>
                <p style="font-size: 12px; color: #94a3b8;">Received files are placed directly into your Windows <code>Downloads</code> folder.</p>
            </div>
        """)
        v.addWidget(txt)

        btn_row = QHBoxLayout()
        open_web_btn = QPushButton("🌐 Open Web Drop Portal")
        open_web_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(web_url)))
        btn_row.addWidget(open_web_btn)

        copy_btn = QPushButton("📋 Copy Web Link")
        copy_btn.clicked.connect(lambda: self.copy_link(web_url, copy_btn))
        btn_row.addWidget(copy_btn)
        btn_row.addStretch()

        v.addLayout(btn_row)
        self.tabs.addTab(tab, "⚡ Lexi Beam (AirDrop)")

    def init_commands_tab(self):
        tab = QWidget()
        v = QVBoxLayout(tab)
        v.setSpacing(10)

        txt = QTextEdit()
        txt.setReadOnly(True)
        txt.setHtml("""
            <div style="font-family: 'Segoe UI', sans-serif;">
                <h3 style="color: #f59e0b; margin-top: 0;">🛠️ Dev Tools & Natural Language Commands</h3>
                <p>Type any of these commands directly into the Copilot chat box for instant execution:</p>

                <table border="0" cellpadding="6" style="color: #cbd5e1; font-size: 12px; background: rgba(255,255,255,0.03); border-radius: 8px;">
                    <tr style="color: #a855f7;"><th>Command</th><th>Action</th></tr>
                    <tr><td><code>port 3000</code> or <code>inspect port 8080</code></td><td>Shows PID and executable listening on that port</td></tr>
                    <tr><td><code>kill port 3000</code></td><td>Gracefully frees the port by killing the blocking task</td></tr>
                    <tr><td><code>top mem</code> or <code>top processes</code></td><td>Lists highest memory/CPU consuming tasks</td></tr>
                    <tr><td><code>kill process &lt;pid&gt;</code></td><td>Terminates runaway tasks (system shielded)</td></tr>
                    <tr><td><code>turn on dnd</code> / <code>mute notifications</code></td><td>Suppresses system notifications</td></tr>
                    <tr><td><code>note &lt;text&gt;</code></td><td>Appends an instant scratch note to markdown vault</td></tr>
                    <tr><td><code>list notes</code> / <code>clear notes</code></td><td>Views or clears saved notes</td></tr>
                    <tr><td><code>volume 80%</code> / <code>mute</code></td><td>Controls Windows system audio volume</td></tr>
                    <tr><td><code>play</code> / <code>pause</code> / <code>next</code></td><td>Media controls for Spotify / Music</td></tr>
                    <tr><td><code>/provider &lt;name&gt;</code></td><td>Switch provider (<code>deepseek</code>, <code>groq</code>, <code>ollama</code>, <code>openai</code>)</td></tr>
                    <tr><td><code>/model &lt;name&gt;</code></td><td>Switch active AI model on the fly</td></tr>
                    <tr><td><code>/clear</code></td><td>Clear conversation history</td></tr>
                </table>
            </div>
        """)
        v.addWidget(txt)
        self.tabs.addTab(tab, "🛠️ Commands & Dev Tools")

    def init_diagnostics_tab(self):
        tab = QWidget()
        v = QVBoxLayout(tab)
        v.setSpacing(10)

        desc = QLabel("🩺 Real-Time System Parity Diagnostics")
        desc.setStyleSheet("font-size: 14px; font-weight: bold; color: #c084fc;")
        v.addWidget(desc)

        self.diag_display = QTextEdit()
        self.diag_display.setReadOnly(True)
        v.addWidget(self.diag_display)

        btn_row = QHBoxLayout()
        rerun_btn = QPushButton("🔄 Re-Run Diagnostics")
        rerun_btn.clicked.connect(self.run_diagnostics)
        btn_row.addWidget(rerun_btn)

        cfg_btn = QPushButton("⚙️ Configure AI Provider")
        cfg_btn.clicked.connect(self.open_settings_from_diag)
        btn_row.addWidget(cfg_btn)
        btn_row.addStretch()

        v.addLayout(btn_row)
        self.tabs.addTab(tab, "🩺 Diagnostics & Self-Test")

        self.run_diagnostics()

    def run_diagnostics(self):
        diag = self.actions.run_system_diagnostics()
        html = ["<div style=\"font-family: 'Segoe UI', sans-serif;\">"]

        # Scrcpy status
        scrcpy = diag.get("scrcpy", {})
        if scrcpy.get("installed"):
            html.append(f"<p>✅ <b>scrcpy Phone Mirror:</b> Installed at <code>{scrcpy.get('path')}</code></p>")
        else:
            html.append(f"<p>⚠️ <b>scrcpy Phone Mirror:</b> Not Found<br><span style='color: #fb923c; font-size: 12px;'>Fix: {scrcpy.get('fix')}</span></p>")

        # ADB status
        adb = diag.get("adb", {})
        if adb.get("installed"):
            devs = adb.get("devices", [])
            dev_str = ", ".join(devs) if devs else "None currently attached"
            html.append(f"<p>✅ <b>Android ADB:</b> Installed at <code>{adb.get('path')}</code><br><span style='color: #94a3b8; font-size: 12px;'>Connected Devices: <b>{dev_str}</b></span></p>")
        else:
            html.append(f"<p>⚠️ <b>Android ADB:</b> Not Found<br><span style='color: #fb923c; font-size: 12px;'>Fix: {adb.get('fix')}</span></p>")

        # Network status
        net = diag.get("network", {})
        ip = net.get("ip", "127.0.0.1")
        html.append(f"<p>✅ <b>Local Network & ShareMe:</b> IP <code>{ip}</code> · Port <code>{net.get('shareme_port', 7890)}</code><br><span style='color: #94a3b8; font-size: 12px;'>LAN Web Portal: <b>http://{ip}:{net.get('shareme_port', 7890)}</b></span></p>")

        # AI status
        ai = diag.get("ai", {})
        prov = ai.get("provider", "deepseek").upper()
        mod = ai.get("model", "deepseek-flash")
        has_key = ai.get("has_key", False)
        if has_key:
            html.append(f"<p>✅ <b>AI Provider:</b> {prov} · Model: <code>{mod}</code> · API Key Configured</p>")
        else:
            html.append(f"<p>⚠️ <b>AI Provider:</b> {prov} · Model: <code>{mod}</code> · <span style='color: #ef4444;'>API Key Missing</span><br><span style='color: #fb923c; font-size: 12px;'>Click 'Configure AI Provider' below to add your DeepSeek, Groq, or OpenAI key.</span></p>")

        # Summary badge
        if diag.get("all_ok"):
            html.append("<div style='background: rgba(34, 197, 94, 0.15); border: 1px solid #22c55e; border-radius: 8px; padding: 10px; margin-top: 10px;'><b style='color: #4ade80;'>🎉 All Systems Operational: Full Linux Parity Active on Windows!</b></div>")
        else:
            html.append("<div style='background: rgba(245, 158, 11, 0.15); border: 1px solid #f59e0b; border-radius: 8px; padding: 10px; margin-top: 10px;'><b style='color: #fbbf24;'>💡 Ready to use with core features! Follow the hints above for optional tools.</b></div>")

        html.append("</div>")
        self.diag_display.setHtml("".join(html))

    def launch_mirror(self):
        res = self.actions.start_phone_mirror()
        QMessageBox.information(self, "Phone Mirror", res.get("message", "Triggered phone mirror."))

    def enable_wireless(self):
        res = self.actions.enable_wireless_adb()
        QMessageBox.information(self, "Wireless ADB", res.get("message", "Configured wireless mode."))

    def copy_link(self, text: str, btn: QPushButton):
        QApplication.clipboard().setText(text)
        btn.setText("✓ Copied!")
        QTimer.singleShot(2000, lambda: btn.setText("📋 Copy Web Link"))

    def open_settings_from_diag(self):
        LexiSettingsDialog(self).exec()
        self.run_diagnostics()


class LexiCopilotCard(QWidget):
    """Floating multimodal AI copilot card with attachments, reasoning tokens, and intent engine."""
    def __init__(self, actions: SystemActionEngineWindows, drop_server: LexiWinDropServer, on_request_snip, on_request_beam, on_request_guide=None, parent=None):
        super().__init__(parent)
        self.actions = actions
        self.drop_server = drop_server
        self.on_request_snip = on_request_snip
        self.on_request_beam = on_request_beam
        self.on_request_guide = on_request_guide
        self.config = load_config()

        self.current_worker = None
        self.attached_images_b64: List[str] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        # Quick Action Strip
        action_strip = QHBoxLayout()
        action_strip.setSpacing(6)

        def make_chip(text, cb):
            btn = QPushButton(text)
            btn.setStyleSheet("""
                QPushButton {
                    background: rgba(255, 255, 255, 0.08);
                    color: #cbd5e1;
                    border: 1px solid rgba(255, 255, 255, 0.12);
                    border-radius: 12px;
                    padding: 3px 10px;
                    font-size: 11px;
                }
                QPushButton:hover { background: rgba(99, 102, 241, 0.25); color: #fff; }
            """)
            btn.clicked.connect(cb)
            return btn

        action_strip.addWidget(make_chip("📱 Phone", self.trigger_phone_mirror))
        action_strip.addWidget(make_chip("📡 Beam", self.on_request_beam))
        action_strip.addWidget(make_chip("✂️ Snip", self.on_request_snip))
        action_strip.addWidget(make_chip("❓ Guide", self.open_guide))
        action_strip.addWidget(make_chip("🧹 Clear", self.clear_chat))
        action_strip.addWidget(make_chip("Explain", lambda: self.quick_prompt("Explain what is on my screen in detail.")))
        action_strip.addWidget(make_chip("Fix", lambda: self.quick_prompt("Find any error or bug on my screen and fix it.")))
        action_strip.addWidget(make_chip("Summarize", lambda: self.quick_prompt("Summarize the content on my screen.")))
        action_strip.addStretch()
        layout.addLayout(action_strip)

        # Attachment previews row
        self.attach_strip = QHBoxLayout()
        self.attach_container = QWidget()
        self.attach_container.setLayout(self.attach_strip)
        self.attach_container.setVisible(False)
        layout.addWidget(self.attach_container)

        # Chat display area
        self.chat_display = QTextEdit()
        self.chat_display.setReadOnly(True)
        self.chat_display.setStyleSheet("""
            background: rgba(15, 17, 23, 0.9);
            color: #e2e8f0;
            border: 1px solid rgba(255, 255, 255, 0.08);
            border-radius: 8px;
            padding: 10px;
            font-family: 'Segoe UI', sans-serif;
            font-size: 13px;
        """)
        self.chat_display.setHtml("""
            <div style="font-family: 'Segoe UI', sans-serif;">
                <h3 style="color: #c084fc; margin-top: 0; margin-bottom: 6px;">✨ Welcome to Lexi Copilot</h3>
                <p style="color: #94a3b8; font-size: 12px; margin-bottom: 8px;">
                    100% Linux parity on Windows: Dynamic Island, 60 FPS Phone Mirror, AirDrop & Dev Tools.
                </p>
                <div style="background: rgba(255,255,255,0.04); border: 1px solid rgba(255,255,255,0.08); border-radius: 8px; padding: 8px 12px; margin-bottom: 8px;">
                    <b style="color: #f1f5f9; font-size: 12px;">⚡ Key Shortcuts & Features:</b><br>
                    • <code>Win + 3</code> or <code>Ctrl+Shift+C</code> — Toggle this Copilot window<br>
                    • <code>Win + Shift + S</code> — Snip screen region & ask questions<br>
                    • <code>Ctrl + Shift + B</code> — Dock to right screen edge as Side Panel<br>
                    • <code>F1</code> — Interactive User Guide, Cheat Sheet & Diagnostics<br>
                    • <i>Hover Top Island</i> — Instant tools, media controls, and desktop pet
                </div>
                <div style="color: #cbd5e1; font-size: 12px;">
                    💡 <i>Try typing:</i> <code>mirror phone</code>, <code>kill port 3000</code>, <code>top mem</code>, or <code>/guide</code>.
                </div>
            </div>
        """)
        layout.addWidget(self.chat_display)

        # Reasoning expandable label
        self.reasoning_box = QTextEdit()
        self.reasoning_box.setReadOnly(True)
        self.reasoning_box.setStyleSheet("""
            background: rgba(99, 102, 241, 0.08);
            color: #a5b4fc;
            border: 1px dashed rgba(99, 102, 241, 0.3);
            border-radius: 6px;
            font-size: 11px;
            padding: 6px;
            max-height: 80px;
        """)
        self.reasoning_box.setVisible(False)
        layout.addWidget(self.reasoning_box)

        # Input row
        input_row = QHBoxLayout()
        input_row.setSpacing(6)

        self.input_field = QLineEdit()
        self.input_field.setPlaceholderText("Ask Lexi, command Windows, or type /help...")
        self.input_field.setStyleSheet("""
            background: rgba(255, 255, 255, 0.07);
            color: #fff;
            border: 1px solid rgba(255, 255, 255, 0.16);
            border-radius: 6px;
            padding: 8px 12px;
            font-size: 13px;
        """)
        self.input_field.returnPressed.connect(self.on_send)
        input_row.addWidget(self.input_field)

        self.send_btn = QPushButton("Send")
        self.send_btn.setStyleSheet("""
            QPushButton {
                background: #6366f1;
                color: white;
                border: none;
                border-radius: 6px;
                padding: 8px 16px;
                font-weight: bold;
                font-size: 12px;
            }
            QPushButton:hover { background: #4f46e5; }
        """)
        self.send_btn.clicked.connect(self.on_send)
        input_row.addWidget(self.send_btn)

        layout.addLayout(input_row)

    def trigger_phone_mirror(self):
        self.chat_display.append("📱 <i>Launching phone mirror...</i>\n")
        res = self.actions.start_phone_mirror()
        self.chat_display.append(f"{res.get('message')}\n")

    def clear_chat(self):
        self.chat_display.clear()
        self.reasoning_box.clear()
        self.reasoning_box.setVisible(False)
        self.attached_images_b64.clear()
        self.update_attach_ui()

    def add_image_attachment(self, b64_img: str):
        if len(self.attached_images_b64) >= 5:
            self.attached_images_b64.pop(0)
        self.attached_images_b64.append(b64_img)
        self.update_attach_ui()

    def update_attach_ui(self):
        # Clear existing items
        while self.attach_strip.count():
            item = self.attach_strip.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if not self.attached_images_b64:
            self.attach_container.setVisible(False)
            return

        self.attach_container.setVisible(True)
        for i, img_b64 in enumerate(self.attached_images_b64):
            thumb_lbl = QLabel(f"📷 Snip {i+1} [✕]")
            thumb_lbl.setStyleSheet("""
                background: rgba(99, 102, 241, 0.2);
                color: #e0e7ff;
                border: 1px solid rgba(99, 102, 241, 0.4);
                border-radius: 10px;
                padding: 2px 8px;
                font-size: 10px;
            """)
            idx = i
            thumb_lbl.mousePressEvent = lambda e, idx=idx: self.remove_attachment(idx)
            self.attach_strip.addWidget(thumb_lbl)
        self.attach_strip.addStretch()

    def remove_attachment(self, idx: int):
        if 0 <= idx < len(self.attached_images_b64):
            self.attached_images_b64.pop(idx)
            self.update_attach_ui()

    def quick_prompt(self, text: str):
        self.input_field.setText(text)
        self.on_send()

    def on_send(self):
        text = self.input_field.text().strip()
        if not text and not self.attached_images_b64:
            return
        self.input_field.clear()

        # Handle Slash commands
        if text.startswith("/"):
            self.handle_slash_command(text)
            return

        # Check Deterministic System Intents
        intent_handled, intent_msg = self.handle_system_intent(text)
        if intent_handled:
            self.chat_display.append(f"<b>You:</b> {text}\n")
            self.chat_display.append(f"<b>Lexi:</b> {intent_msg}\n")
            return

        # Multimodal AI prompt
        self.chat_display.append(f"<b>You:</b> {text}\n")
        self.reasoning_box.clear()
        self.reasoning_box.setVisible(False)

        images = list(self.attached_images_b64)
        self.attached_images_b64.clear()
        self.update_attach_ui()

        self.config = load_config()
        self.current_worker = StreamWorker(self.config, text, images)
        self.chat_display.append("<b>Lexi:</b> ")

        self.current_worker.token_received.connect(self.on_token)
        self.current_worker.reasoning_received.connect(self.on_reasoning)
        self.current_worker.finished.connect(self.on_finished)
        self.current_worker.error.connect(self.on_error)
        self.current_worker.start()

    def open_guide(self):
        if self.on_request_guide:
            self.on_request_guide()
        else:
            LexiGuideDialog(self.actions, self).exec()

    def handle_slash_command(self, cmd: str):
        parts = cmd.split(maxsplit=1)
        c = parts[0].lower()
        arg = parts[1] if len(parts) > 1 else ""

        if c == "/phone":
            self.trigger_phone_mirror()
        elif c == "/beam":
            self.on_request_beam()
        elif c == "/snip":
            self.on_request_snip()
        elif c in ("/guide", "/help", "/diag"):
            self.open_guide()
        elif c == "/clear":
            self.clear_chat()
        elif c == "/settings":
            LexiSettingsDialog(self).exec()
        elif c == "/provider" and arg:
            self.config["provider"] = arg.strip().lower()
            save_config(self.config)
            self.chat_display.append(f"<i>✓ Provider set to: {arg}</i>\n")
        elif c == "/model" and arg:
            self.config["model"] = arg.strip()
            save_config(self.config)
            self.chat_display.append(f"<i>✓ Model set to: {arg}</i>\n")
        elif c == "/key" and arg:
            self.config["api_key"] = arg.strip()
            save_config(self.config)
            self.chat_display.append("<i>✓ API Key updated securely.</i>\n")
        else:
            self.chat_display.append(
                "<i>Available Slash Commands:</i><br>"
                "• <b>/guide</b> — Open User Guide & Diagnostics<br>"
                "• <b>/phone</b> — Launch phone mirror<br>"
                "• <b>/beam</b> — Open ShareMe P2P radar<br>"
                "• <b>/snip</b> — Capture screen region<br>"
                "• <b>/settings</b> — Open provider settings<br>"
                "• <b>/provider &lt;name&gt;</b> — Switch provider (deepseek, groq, ollama)<br>"
                "• <b>/model &lt;name&gt;</b> — Switch model<br>"
                "• <b>/clear</b> — Clear conversation<br>"
            )

    def handle_system_intent(self, text: str) -> Tuple[bool, str]:
        t = text.lower().strip()
        # Guide & Diagnostics
        if t in ["guide", "help", "how to use", "cheat sheet", "diagnostics", "self test", "selftest"]:
            self.open_guide()
            return True, "📖 Opened User Guide & Diagnostics."

        # Phone mirror
        if any(w in t for w in ["mirror phone", "phone mirror", "screen mirror", "start scrcpy", "connect phone"]):
            res = self.actions.start_phone_mirror()
            return True, res.get("message", "Triggered phone mirror.")

        # Port Killer
        if "kill port" in t or "free port" in t:
            words = t.split()
            port = next((int(w) for w in words if w.isdigit()), None)
            if port:
                res = self.actions.kill_port(port)
                return True, res.get("message")
        if "port" in t and any(w in t for w in ["inspect", "check", "who is using"]):
            words = t.split()
            port = next((int(w) for w in words if w.isdigit()), None)
            if port:
                res = self.actions.inspect_port(port)
                return True, res.get("message")

        # Top processes
        if "top process" in t or "top cpu" in t or "top mem" in t:
            sort = "mem" if "mem" in t else "cpu"
            res = self.actions.top_processes(sort_by=sort)
            return True, res.get("message")

        # Media controls
        if t in ["pause", "play", "play music", "pause music", "resume"]:
            self.actions.media_control("play_pause")
            return True, "⏯ Media toggled."
        if "next song" in t or "next track" in t or t == "next":
            self.actions.media_control("next")
            return True, "⏭ Next track."
        if "previous song" in t or "prev song" in t or t == "prev":
            self.actions.media_control("prev")
            return True, "⏮ Previous track."

        # Volume
        if "volume" in t:
            words = t.split()
            lvl = next((w for w in words if w.isdigit()), "toggle")
            self.actions.set_volume(lvl)
            return True, f"🔊 Volume adjusted: {lvl}%"
        if "mute" in t:
            self.actions.set_volume("mute")
            return True, "🔇 Volume muted."

        return False, ""

    def on_token(self, token: str):
        cursor = self.chat_display.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        cursor.insertText(token)
        self.chat_display.setTextCursor(cursor)

    def on_reasoning(self, reasoning: str):
        if not self.reasoning_box.isVisible():
            self.reasoning_box.setVisible(True)
        cursor = self.reasoning_box.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        cursor.insertText(reasoning)
        self.reasoning_box.setTextCursor(cursor)

    def on_finished(self, full_text: str):
        self.chat_display.append("\n")

    def on_error(self, err_msg: str):
        self.chat_display.append(f"\n<i>⚠️ Error: {err_msg}</i>\n")

class LexiCopilotWindow(QMainWindow):
    """
    Multimodal Vision Copilot Window for Windows:
    Floating center card or docked vertical side panel with acrylic glass styling.
    """
    def __init__(self, actions, drop_server, on_request_snip, on_request_beam, on_request_guide=None, parent=None):
        super().__init__(parent)
        self.actions = actions
        self.drop_server = drop_server
        self.on_request_guide = on_request_guide
        self.side_panel_mode = False

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

        self.root = QWidget(self)
        self.root.setObjectName("copilotRoot")
        self.root.setStyleSheet("""
            #copilotRoot {
                background-color: rgba(13, 15, 22, 0.96);
                border: 1.5px solid rgba(168, 85, 247, 0.35);
                border-radius: 14px;
            }
        """)
        self.setCentralWidget(self.root)

        layout = QVBoxLayout(self.root)
        layout.setContentsMargins(12, 10, 12, 12)
        layout.setSpacing(6)

        # Header Titlebar
        hdr = QHBoxLayout()
        hdr.setContentsMargins(4, 2, 4, 4)
        
        title_lbl = QLabel("✨ Lexi Multimodal Copilot")
        title_lbl.setStyleSheet("color: #f4f4f5; font-size: 13px; font-weight: bold;")
        hdr.addWidget(title_lbl)

        hdr.addStretch()

        guide_btn = QPushButton("❓ Guide")
        guide_btn.setStyleSheet("""
            QPushButton {
                background: rgba(255, 255, 255, 0.08);
                color: #cbd5e1;
                border: 1px solid rgba(255, 255, 255, 0.12);
                border-radius: 10px;
                padding: 2px 8px;
                font-size: 11px;
            }
            QPushButton:hover { background: rgba(168, 85, 247, 0.25); color: #fff; }
        """)
        guide_btn.clicked.connect(self.open_guide)
        hdr.addWidget(guide_btn)

        self.side_btn = QPushButton("◨ Side")
        self.side_btn.setStyleSheet("""
            QPushButton {
                background: rgba(255, 255, 255, 0.08);
                color: #cbd5e1;
                border: 1px solid rgba(255, 255, 255, 0.12);
                border-radius: 10px;
                padding: 2px 8px;
                font-size: 11px;
            }
            QPushButton:hover { background: rgba(255, 255, 255, 0.16); color: #fff; }
        """)
        self.side_btn.clicked.connect(self.toggle_side_panel)
        hdr.addWidget(self.side_btn)

        close_btn = QPushButton("✕")
        close_btn.setStyleSheet("""
            QPushButton {
                background: transparent;
                color: #a1a1aa;
                border: none;
                font-size: 14px;
                padding: 2px 6px;
            }
            QPushButton:hover { color: #ef4444; }
        """)
        close_btn.clicked.connect(self.hide)
        hdr.addWidget(close_btn)

        layout.addLayout(hdr)

        self.card = LexiCopilotCard(
            self.actions,
            self.drop_server,
            on_request_snip=on_request_snip,
            on_request_beam=on_request_beam,
            on_request_guide=on_request_guide
        )
        layout.addWidget(self.card)

        self.resize(520, 500)
        self.reposition_center()

    def open_guide(self):
        if self.on_request_guide:
            self.on_request_guide()
        else:
            LexiGuideDialog(self.actions, self).exec()


    def reposition_center(self):
        screen = QApplication.primaryScreen().geometry()
        x = (screen.width() - 520) // 2
        y = 50  # Just below top dynamic island
        self.move(x, y)

    def toggle_side_panel(self):
        self.side_panel_mode = not self.side_panel_mode
        screen = QApplication.primaryScreen().geometry()
        if self.side_panel_mode:
            self.side_btn.setText("🗗 Center")
            self.move(screen.width() - 440, 0)
            self.resize(440, screen.height())
        else:
            self.side_btn.setText("◨ Side")
            self.resize(520, 500)
            self.reposition_center()


class LexiDynamicIslandWin(QMainWindow):
    """
    Lexi Dynamic Island for Windows:
    Top-docked OLED pure black pill (border-radius: 9999px) with smooth hover morphing,
    media equalizer, download monitor, phone mirroring, and system alerts.
    """
    def __init__(self):
        super().__init__()
        self.config = load_config()
        self.actions = SystemActionEngineWindows.get()

        # ShareMe Beam Server
        self.drop_server = LexiWinDropServer()
        self.drop_server.on_incoming_request = self.on_incoming_beam
        self.drop_server.on_transfer_progress = self.on_beam_progress
        self.drop_server.on_transfer_completed = self.on_beam_completed
        self.drop_server.start()

        # Overlays & Secondary Windows
        self.snip_overlay = LexiSnipOverlay()
        self.snip_overlay.snip_captured.connect(self.on_snip_captured)

        self.copilot_win = LexiCopilotWindow(
            self.actions,
            self.drop_server,
            on_request_snip=self.start_region_snip,
            on_request_beam=self.show_beam_dialog,
            on_request_guide=self.open_guide
        )

        self.pet_widget = LexiPetWidget(on_activate=self.expand_copilot, on_dock=self.show_island)
        self.is_hovered = False

        self.init_ui()
        self.collapse_to_resting()

        # Background Media & Download Watcher Timer
        self.status_timer = QTimer(self)
        self.status_timer.timeout.connect(self.poll_system_status)
        self.status_timer.start(2000)

        # System Tray & Global Hotkeys
        self.init_tray()
        self.init_hotkeys()

    def init_ui(self):
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

        self.root_widget = QWidget(self)
        self.setCentralWidget(self.root_widget)

        root_layout = QVBoxLayout(self.root_widget)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        # The Exact OLED Black Capsule Pill
        self.pill = QFrame()
        self.pill.setObjectName("dynamicIslandPill")
        self.apply_pill_style("default")

        self.pill_layout = QHBoxLayout(self.pill)
        self.pill_layout.setContentsMargins(10, 4, 12, 4)
        self.pill_layout.setSpacing(8)

        # 1. Mascot Avatar
        self.mascot_btn = QPushButton("✨")
        self.mascot_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.mascot_btn.setStyleSheet("background: transparent; border: none; font-size: 15px;")
        
        # Check if shakeel_chibi.png exists
        app_dir = Path(__file__).parent
        avatar_path = app_dir / "shakeel_chibi.png"
        if avatar_path.exists():
            pix = QPixmap(str(avatar_path)).scaled(22, 22, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            self.mascot_btn.setIcon(QIcon(pix))
            self.mascot_btn.setIconSize(QSize(22, 22))
            self.mascot_btn.setText("")

        self.mascot_btn.clicked.connect(self.expand_copilot)
        self.pill_layout.addWidget(self.mascot_btn)

        # 2. Status Label & Pulsing Dot
        self.status_label = QLabel("Lexi")
        self.status_label.setStyleSheet("color: #f4f4f5; font-size: 12px; font-weight: bold; letter-spacing: -0.2px;")
        self.pill_layout.addWidget(self.status_label)

        self.pulse_dot = QLabel("●")
        self.pulse_dot.setStyleSheet("color: #10b981; font-size: 8px; margin-right: 2px;")
        self.pill_layout.addWidget(self.pulse_dot)

        # 3. Media Controls (Visible on hover during media playback)
        self.media_container = QWidget()
        m_layout = QHBoxLayout(self.media_container)
        m_layout.setContentsMargins(0, 0, 0, 0)
        m_layout.setSpacing(4)

        def make_media_btn(icon, cb):
            b = QPushButton(icon)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setStyleSheet("""
                QPushButton {
                    background: rgba(255, 255, 255, 0.08);
                    color: #fff;
                    border: 1px solid rgba(255, 255, 255, 0.18);
                    border-radius: 11px;
                    min-height: 22px;
                    padding: 2px 7px;
                    font-size: 11px;
                    font-weight: bold;
                }
                QPushButton:hover { background: rgba(255, 255, 255, 0.22); }
            """)
            b.clicked.connect(cb)
            return b

        m_layout.addWidget(make_media_btn("⏮", lambda: self.actions.media_control("prev")))
        m_layout.addWidget(make_media_btn("⏯", lambda: self.actions.media_control("play_pause")))
        m_layout.addWidget(make_media_btn("⏭", lambda: self.actions.media_control("next")))
        self.media_container.setVisible(False)
        self.pill_layout.addWidget(self.media_container)

        # 4. Action Buttons Container (Morphs open on hover)
        self.actions_container = QWidget()
        self.actions_layout = QHBoxLayout(self.actions_container)
        self.actions_layout.setContentsMargins(0, 0, 0, 0)
        self.actions_layout.setSpacing(4)

        def make_pill_chip(label, cb, primary=False):
            b = QPushButton(label)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            if primary:
                b.setStyleSheet("""
                    QPushButton {
                        background: #ffffff;
                        color: #09090b;
                        border: 1px solid rgba(255, 255, 255, 0.4);
                        border-radius: 12px;
                        padding: 3px 10px;
                        font-size: 11px;
                        font-weight: bold;
                    }
                    QPushButton:hover { background: #f4f4f5; }
                """)
            else:
                b.setStyleSheet("""
                    QPushButton {
                        background: rgba(255, 255, 255, 0.08);
                        color: #e4e4e7;
                        border: 1px solid rgba(255, 255, 255, 0.14);
                        border-radius: 12px;
                        padding: 3px 9px;
                        font-size: 11px;
                        font-weight: bold;
                    }
                    QPushButton:hover { background: rgba(255, 255, 255, 0.18); color: #fff; }
                """)
            b.clicked.connect(cb)
            return b

        self.actions_layout.addWidget(make_pill_chip("💬 Copilot", self.expand_copilot, primary=True))
        self.actions_layout.addWidget(make_pill_chip("⚡ Beam", self.show_beam_dialog))
        self.actions_layout.addWidget(make_pill_chip("📱 Phone", self.trigger_phone_mirror))
        self.actions_layout.addWidget(make_pill_chip("◨ Side", self.toggle_side_panel))
        self.actions_layout.addWidget(make_pill_chip("✂️ Snip", self.start_region_snip))
        self.actions_layout.addWidget(make_pill_chip("🐾 Pet", self.detach_pet))
        self.actions_layout.addWidget(make_pill_chip("❓ Guide", self.open_guide))
        self.actions_layout.addWidget(make_pill_chip("⚙️", self.open_settings))

        self.actions_container.setVisible(False)
        self.pill_layout.addWidget(self.actions_container)

        root_layout.addWidget(self.pill)

    def apply_pill_style(self, mode="default"):
        glows = {
            "default": "border: 1.5px solid rgba(255, 255, 255, 0.28);",
            "hover": "border: 1.5px solid rgba(192, 132, 252, 0.9);",
            "green": "border: 1.5px solid #22c55e;",
            "cyan": "border: 1.5px solid #06b6d4;",
            "amber": "border: 1.5px solid #f59e0b;",
            "purple": "border: 1.5px solid #c084fc;"
        }
        border_css = glows.get(mode, glows["default"])
        self.pill.setStyleSheet(f"""
            #dynamicIslandPill {{
                background-color: #000000;
                border-radius: 18px;
                {border_css}
            }}
        """)

    def enterEvent(self, event):
        self.is_hovered = True
        self.actions_container.setVisible(True)
        self.apply_pill_style("hover")
        self.expand_to_actions()

    def leaveEvent(self, event):
        self.is_hovered = False
        QTimer.singleShot(250, self._check_leave)

    def _check_leave(self):
        if not self.is_hovered:
            self.actions_container.setVisible(False)
            self.apply_pill_style("default")
            self.collapse_to_resting()

    def collapse_to_resting(self):
        w = 180
        if self.status_label.text() != "Lexi":
            w = 260
        self.resize(w, 38)
        self.reposition_top()

    def expand_to_actions(self):
        self.resize(700, 38)
        self.reposition_top()

    def reposition_top(self):
        screen = QApplication.primaryScreen().geometry()
        x = (screen.width() - self.width()) // 2
        y = 6  # Pinned smoothly at the very top
        self.move(x, y)

    def expand_copilot(self):
        self.copilot_win.show()
        self.copilot_win.raise_()
        self.copilot_win.activateWindow()

    def toggle_copilot(self):
        if self.copilot_win.isVisible():
            self.copilot_win.hide()
        else:
            self.expand_copilot()

    def open_guide(self):
        LexiGuideDialog(self.actions, self).exec()

    def toggle_side_panel(self):
        self.copilot_win.show()
        self.copilot_win.toggle_side_panel()

    def detach_pet(self):
        screen = QApplication.primaryScreen().geometry()
        self.pet_widget.move(screen.width() - 100, screen.height() - 120)
        self.pet_widget.show()
        self.hide()

    def show_island(self):
        self.show()
        self.collapse_to_resting()

    def trigger_phone_mirror(self):
        self.trigger_alert("📱", "Mirroring Phone @ 60 FPS", "green", 3.5)
        res = self.actions.start_phone_mirror()
        if not res.get("success"):
            self.trigger_alert("📱", "Plug USB or Enable Debugging", "amber", 4.0)
            QMessageBox.information(self, "Phone Mirror", res.get("message", "Error connecting phone"))

    def trigger_alert(self, icon: str, text: str, glow_color: str = "green", duration: float = 3.0):
        self.mascot_btn.setText(icon)
        self.status_label.setText(text)
        self.apply_pill_style(glow_color)
        self.resize(320, 38)
        self.reposition_top()
        QTimer.singleShot(int(duration * 1000), self.restore_resting_state)

    def restore_resting_state(self):
        self.status_label.setText("Lexi")
        self.apply_pill_style("default")
        self.collapse_to_resting()

    def show_beam_dialog(self):
        LexiBeamDialog(self.drop_server, self).exec()

    def start_region_snip(self):
        self.snip_overlay.start_capture()

    def on_snip_captured(self, b64_img: str):
        self.expand_copilot()
        self.copilot_win.card.add_image_attachment(b64_img)
        self.copilot_win.card.chat_display.append("<i>[📸 Screen Region Attached. Ask anything!]</i>\n")

    def open_settings(self):
        LexiSettingsDialog(self).exec()

    def poll_system_status(self):
        media = self.actions.get_media_status()
        if media.get("playing") and media.get("track"):
            track = media.get("track")
            artist = media.get("artist")
            self.status_label.setText(f"🎵 {artist} - {track[:18]}")
            self.media_container.setVisible(True)
            if not self.is_hovered:
                self.resize(300, 38)
                self.reposition_top()
        else:
            self.media_container.setVisible(False)
            if not self.is_hovered:
                self.status_label.setText("Lexi")
                self.collapse_to_resting()

    def on_incoming_beam(self, info: Dict[str, Any]):
        name = info.get("filename")
        size = format_size(info.get("size_bytes", 0))
        sender = info.get("from_device", "Nearby Device")
        self.trigger_alert("📥", f"Incoming {name} ({size})", "purple", 4.0)
        self.expand_copilot()
        msg = f"<br>📥 <b>Incoming Beam from {sender}:</b><br>• File: <b>{name}</b> ({size})<br>• Receiving into Downloads at wire speed...<br>"
        self.copilot_win.card.chat_display.append(msg)

    def on_beam_progress(self, filename: str, pct: float, speed_mb: float):
        self.status_label.setText(f"⚡ {pct:.0f}% · {speed_mb:.1f} MB/s")

    def on_beam_completed(self, filename: str, dest_path: str):
        self.trigger_alert("✓", f"Received {filename}", "green", 4.0)
        self.expand_copilot()
        msg = f"✓ <b>Received '{filename}'!</b><br>Saved to: <code>{dest_path}</code><br>"
        self.copilot_win.card.chat_display.append(msg)

    def init_tray(self):
        self.tray = QSystemTrayIcon(self)
        app_dir = Path(__file__).parent
        avatar_path = app_dir / "shakeel_chibi.png"
        if avatar_path.exists():
            self.tray.setIcon(QIcon(str(avatar_path)))
        else:
            pix = QPixmap(32, 32)
            pix.fill(QColor(168, 85, 247))
            self.tray.setIcon(QIcon(pix))

        self.tray.setToolTip("Lexi Copilot — Dynamic Island & AirDrop")

        menu = QMenu()
        menu.setStyleSheet("""
            QMenu {
                background-color: #0f121d;
                color: #e2e8f0;
                border: 1px solid rgba(168, 85, 247, 0.3);
                border-radius: 8px;
                padding: 4px;
                font-family: 'Segoe UI', sans-serif;
                font-size: 12px;
            }
            QMenu::item {
                padding: 6px 20px;
                border-radius: 4px;
            }
            QMenu::item:selected {
                background-color: rgba(168, 85, 247, 0.25);
                color: #ffffff;
            }
            QMenu::separator {
                height: 1px;
                background: rgba(255, 255, 255, 0.1);
                margin: 4px 8px;
            }
        """)

        act_copilot = menu.addAction("✨ Open Copilot (Win+3)")
        act_copilot.triggered.connect(self.expand_copilot)

        act_guide = menu.addAction("❓ User Guide & Diagnostics (F1)")
        act_guide.triggered.connect(self.open_guide)

        menu.addSeparator()

        act_phone = menu.addAction("📱 60 FPS Phone Mirror")
        act_phone.triggered.connect(self.trigger_phone_mirror)

        act_beam = menu.addAction("⚡ Lexi Beam (AirDrop)")
        act_beam.triggered.connect(self.show_beam_dialog)

        act_snip = menu.addAction("✂️ Region Snip (Win+Shift+S)")
        act_snip.triggered.connect(self.start_region_snip)

        act_side = menu.addAction("◨ Side Panel Mode")
        act_side.triggered.connect(self.toggle_side_panel)

        act_pet = menu.addAction("🐾 Desktop Pet Mode")
        act_pet.triggered.connect(self.detach_pet)

        act_settings = menu.addAction("⚙️ AI Provider Settings")
        act_settings.triggered.connect(self.open_settings)

        menu.addSeparator()

        act_exit = menu.addAction("✕ Quit Lexi")
        act_exit.triggered.connect(self.quit_app)

        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self.on_tray_activated)
        self.tray.show()

    def on_tray_activated(self, reason):
        if reason in (QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick):
            self.toggle_copilot()

    def init_hotkeys(self):
        self.hotkey_listener = WinGlobalHotkeyListener(self)
        self.hotkey_listener.hotkey_copilot.connect(self.toggle_copilot)
        self.hotkey_listener.hotkey_snip.connect(self.start_region_snip)
        self.hotkey_listener.hotkey_guide.connect(self.open_guide)
        self.hotkey_listener.hotkey_side.connect(self.toggle_side_panel)
        self.hotkey_listener.start()

    def quit_app(self):
        if hasattr(self, 'hotkey_listener') and self.hotkey_listener:
            self.hotkey_listener.stop()
        if hasattr(self, 'drop_server') and self.drop_server:
            self.drop_server.stop()
        QApplication.quit()

    def closeEvent(self, event):
        self.quit_app()
        super().closeEvent(event)


def main():
    app = QApplication(sys.argv)
    island = LexiDynamicIslandWin()
    island.show()
    sys.exit(app.exec())


if __name__ == '__main__':
    main()
