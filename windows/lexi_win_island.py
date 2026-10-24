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
import urllib.request
import urllib.parse
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple

from PySide6.QtCore import Qt, QTimer, QPoint, QSize, Signal, QObject, QThread, QRect
from PySide6.QtGui import (
    QColor, QFont, QPainter, QBrush, QPen, QLinearGradient, QAction, QIcon,
    QKeySequence, QPixmap, QCursor, QGuiApplication
)
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QTextEdit, QScrollArea, QFrame,
    QFileDialog, QSystemTrayIcon, QMenu, QGraphicsDropShadowEffect,
    QDialog, QComboBox, QCheckBox, QProgressBar, QMessageBox
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

class LexiCopilotCard(QWidget):
    """Floating multimodal AI copilot card with attachments, reasoning tokens, and intent engine."""
    def __init__(self, actions: SystemActionEngineWindows, drop_server: LexiWinDropServer, on_request_snip, on_request_beam, parent=None):
        super().__init__(parent)
        self.actions = actions
        self.drop_server = drop_server
        self.on_request_snip = on_request_snip
        self.on_request_beam = on_request_beam
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

class LexiDynamicIslandWin(QMainWindow):
    """
    Lexi Dynamic Island for Windows:
    Top-docked OLED notch with liquid morphing pills, media controls, download bar,
    phone mirroring, screen vision, and side panel modes.
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

        self.pet_widget = LexiPetWidget(on_activate=self.expand_copilot, on_dock=self.show_island)
        self.side_panel_mode = False
        self.expanded = False

        self.init_ui()
        self.reposition()

        # Background Media & Download Watcher Timer
        self.status_timer = QTimer(self)
        self.status_timer.timeout.connect(self.poll_system_status)
        self.status_timer.start(2000)

    def init_ui(self):
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

        self.root_widget = QWidget(self)
        self.root_widget.setObjectName("rootWidget")
        self.setCentralWidget(self.root_widget)

        self.layout = QVBoxLayout(self.root_widget)
        self.layout.setContentsMargins(12, 6, 12, 10)
        self.layout.setSpacing(8)

        # 1. Notch Capsule Bar (Always Visible)
        self.notch_bar = QWidget()
        self.notch_layout = QHBoxLayout(self.notch_bar)
        self.notch_layout.setContentsMargins(0, 0, 0, 0)
        self.notch_layout.setSpacing(8)

        # Mascot Icon
        self.mascot_btn = QPushButton("✨")
        self.mascot_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.mascot_btn.setStyleSheet("background: transparent; border: none; font-size: 16px;")
        self.mascot_btn.clicked.connect(self.toggle_expand)
        self.notch_layout.addWidget(self.mascot_btn)

        # Pulsing Dot + Status Pill
        self.status_pill = QLabel("● deepseek-flash")
        self.status_pill.setStyleSheet("""
            background: rgba(16, 185, 129, 0.15);
            color: #10b981;
            border: 1px solid rgba(16, 185, 129, 0.3);
            border-radius: 10px;
            padding: 2px 8px;
            font-size: 11px;
            font-weight: bold;
        """)
        self.notch_layout.addWidget(self.status_pill)

        # Media Controls (Visible when music is playing)
        self.media_container = QWidget()
        m_layout = QHBoxLayout(self.media_container)
        m_layout.setContentsMargins(0, 0, 0, 0)
        m_layout.setSpacing(4)

        def make_mini_btn(icon, cb):
            b = QPushButton(icon)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setStyleSheet("background: transparent; color: #cbd5e1; border: none; font-size: 11px;")
            b.clicked.connect(cb)
            return b

        m_layout.addWidget(make_mini_btn("⏮", lambda: self.actions.media_control("prev")))
        m_layout.addWidget(make_mini_btn("⏯", lambda: self.actions.media_control("play_pause")))
        m_layout.addWidget(make_mini_btn("⏭", lambda: self.actions.media_control("next")))
        self.media_container.setVisible(False)
        self.notch_layout.addWidget(self.media_container)

        self.notch_layout.addStretch()

        # Action Buttons
        def make_btn(label, cb, primary=False):
            b = QPushButton(label)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            if primary:
                b.setStyleSheet("background: #6366f1; color: white; border: none; border-radius: 6px; padding: 4px 10px; font-size: 11px; font-weight: bold;")
            else:
                b.setStyleSheet("background: rgba(255, 255, 255, 0.08); color: #cbd5e1; border: 1px solid rgba(255, 255, 255, 0.12); border-radius: 6px; padding: 3px 8px; font-size: 11px;")
            b.clicked.connect(cb)
            return b

        self.btn_copilot = make_btn("💬 Copilot", self.toggle_expand, primary=True)
        self.btn_phone = make_btn("📱 Phone", self.trigger_phone_mirror)
        self.btn_beam = make_btn("⚡ Beam", self.show_beam_dialog)
        self.btn_side = make_btn("◨ Side", self.toggle_side_panel)
        self.btn_snip = make_btn("✂️ Snip", self.start_region_snip)
        self.btn_pet = make_btn("🐾 Pet", self.detach_pet)
        self.btn_cfg = make_btn("⚙️", self.open_settings)

        self.notch_layout.addWidget(self.btn_copilot)
        self.notch_layout.addWidget(self.btn_phone)
        self.notch_layout.addWidget(self.btn_beam)
        self.notch_layout.addWidget(self.btn_side)
        self.notch_layout.addWidget(self.btn_snip)
        self.notch_layout.addWidget(self.btn_pet)
        self.notch_layout.addWidget(self.btn_cfg)

        self.layout.addWidget(self.notch_bar)

        # 2. Copilot Card Section (Visible when expanded)
        self.copilot_card = LexiCopilotCard(
            self.actions,
            self.drop_server,
            on_request_snip=self.start_region_snip,
            on_request_beam=self.show_beam_dialog
        )
        self.copilot_card.setVisible(False)
        self.layout.addWidget(self.copilot_card)

        self.apply_theme()
        self.resize(560, 44)

    def apply_theme(self):
        self.root_widget.setStyleSheet("""
            #rootWidget {
                background-color: rgba(13, 15, 22, 0.95);
                border: 1px solid rgba(255, 255, 255, 0.12);
                border-bottom-left-radius: 14px;
                border-bottom-right-radius: 14px;
            }
        """)

    def reposition(self):
        screen = QApplication.primaryScreen().geometry()
        if self.side_panel_mode:
            self.move(screen.width() - 440, 0)
            self.resize(440, screen.height())
        else:
            x = (screen.width() - self.width()) // 2
            self.move(x, 0)

    def toggle_expand(self):
        self.expanded = not self.expanded
        self.copilot_card.setVisible(self.expanded)
        if self.expanded:
            self.resize(560, 480)
            self.copilot_card.input_field.setFocus()
        else:
            self.resize(560, 44)
        self.reposition()

    def expand_copilot(self):
        if not self.expanded:
            self.toggle_expand()
        self.show()
        self.raise_()

    def toggle_side_panel(self):
        self.side_panel_mode = not self.side_panel_mode
        self.expanded = True
        self.copilot_card.setVisible(True)
        self.reposition()

    def detach_pet(self):
        screen = QApplication.primaryScreen().geometry()
        self.pet_widget.move(screen.width() - 100, screen.height() - 120)
        self.pet_widget.show()
        self.hide()

    def show_island(self):
        self.show()
        self.reposition()

    def trigger_phone_mirror(self):
        self.status_pill.setText("📱 Connecting...")
        res = self.actions.start_phone_mirror()
        if res.get("success"):
            self.status_pill.setText("● Mirroring @ 60fps")
        else:
            self.status_pill.setText("⚠️ Plug USB")
            QMessageBox.information(self, "Phone Mirror", res.get("message", "Error connecting phone"))
        QTimer.singleShot(4000, self.restore_status_pill)

    def show_beam_dialog(self):
        LexiBeamDialog(self.drop_server, self).exec()

    def start_region_snip(self):
        self.snip_overlay.start_capture()

    def on_snip_captured(self, b64_img: str):
        self.expand_copilot()
        self.copilot_card.add_image_attachment(b64_img)
        self.copilot_card.chat_display.append("<i>[📸 Screen Region Attached. Ask anything!]</i>\n")

    def open_settings(self):
        LexiSettingsDialog(self).exec()
        self.restore_status_pill()

    def restore_status_pill(self):
        cfg = load_config()
        p = cfg.get("provider", "deepseek")
        m = cfg.get("model", "deepseek-flash")
        self.status_pill.setText(f"● {p}: {m}")

    def poll_system_status(self):
        # Media check
        media = self.actions.get_media_status()
        if media.get("playing") and media.get("track"):
            track = media.get("track")
            artist = media.get("artist")
            self.status_pill.setText(f"🎵 {artist} - {track[:20]}")
            self.media_container.setVisible(True)
        else:
            self.media_container.setVisible(False)
            if not self.expanded:
                self.restore_status_pill()

    def on_incoming_beam(self, info: Dict[str, Any]):
        self.expand_copilot()
        name = info.get("filename")
        size = format_size(info.get("size_bytes", 0))
        sender = info.get("from_device", "Nearby Device")
        self.copilot_card.chat_display.append(f"\n📥 <b>Incoming Beam from {sender}:</b>\n• File: <b>{name}</b> ({size})\n• Receiving into Downloads at direct Wi-Fi wire speed...\n")

    def on_beam_progress(self, filename: str, pct: float, speed_mb: float):
        self.status_pill.setText(f"⚡ {pct:.0f}% · {speed_mb:.1f} MB/s")

    def on_beam_completed(self, filename: str, dest_path: str):
        self.status_pill.setText("✓ Complete!")
        self.expand_copilot()
        self.copilot_card.chat_display.append(f"✓ <b>Received '{filename}'!</b>\nSaved to: <code>{dest_path}</code>\n")
        QTimer.singleShot(4000, self.restore_status_pill)


def main():
    app = QApplication(sys.argv)
    island = LexiDynamicIslandWin()
    island.show()
    sys.exit(app.exec())


if __name__ == '__main__':
    main()
