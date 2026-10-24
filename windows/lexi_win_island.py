#!/usr/bin/env python3
"""
Lexi Dynamic Island for Windows
===============================
Native top-docked notch companion powered by PySide6 (Qt).
- Frameless, translucent dark-glass acrylic styling
- DeepSeek-V4.1-Flash (Vision) chat with streaming & reasoning
- Built-in ShareMe Beam tray for zero-data P2P LAN file transfers
- Windows hardware automation & desktop screen vision
"""

import os
import sys
import json
import time
import base64
import urllib.request
from pathlib import Path
from typing import Optional, List, Dict, Any

from PySide6.QtCore import Qt, QTimer, QPoint, QSize, Signal, QObject, QThread
from PySide6.QtGui import QColor, QFont, QPainter, QBrush, QPen, QLinearGradient, QAction, QIcon, QKeySequence
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QTextEdit, QScrollArea, QFrame,
    QFileDialog, QSystemTrayIcon, QMenu, QGraphicsDropShadowEffect
)

from lexi_win_actions import SystemActionEngineWindows
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
    "max_tokens": 4096,
    "system_prompt": (
        "You are Lexi, an intelligent autonomous desktop AI companion on Windows created by Shakeel.\n"
        "You can control the computer, open apps/files/URLs, control media & volume, and analyze screens.\n"
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

    def __init__(self, config: Dict[str, Any], prompt: str, image_b64: Optional[str] = None):
        super().__init__()
        self.config = config
        self.prompt = prompt
        self.image_b64 = image_b64

        provider = (self.config.get("provider") or "deepseek").lower()
        if provider == "groq":
            api_base = (self.config.get("groq_api_base") or "https://api.groq.com/openai/v1").rstrip("/")
            api_key = self.config.get("groq_api_key") or self.config.get("api_key") or ""
            model = self.config.get("groq_model") or self.config.get("model") or "llama-3.3-70b-versatile"
            if self.image_b64 and "vision" not in model.lower():
                model = "llama-3.2-11b-vision-preview"
        else:
            api_key = self.config.get("api_key") or self.config.get("deepseek_api_key") or ""
            api_base = (self.config.get("api_base") or "https://api.deepseek.com").rstrip("/")
            model = self.config.get("model") or "deepseek-flash"

        messages = [{"role": "system", "content": self.config.get("system_prompt", "")}]
        if self.image_b64:
            user_content = [
                {"type": "text", "text": self.prompt or "What is on my screen?"},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{self.image_b64}"}}
            ]
        else:
            user_content = self.prompt

        messages.append({"role": "user", "content": user_content})

        payload = {
            "model": model,
            "messages": messages,
            "stream": True,
            "max_tokens": self.config.get("max_tokens", 4096)
        }

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}"
        }

        url = f"{api_base}/chat/completions"
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


class LexiDynamicIslandWin(QMainWindow):
    """Windows Floating Top Notch with Acrylic Aesthetic."""
    def __init__(self):
        super().__init__()
        self.config = load_config()
        self.actions = SystemActionEngineWindows.get()
        self.drop_server = LexiWinDropServer()
        self.drop_server.on_incoming_request = self.on_incoming_file_proposed
        self.drop_server.on_transfer_progress = self.on_transfer_progress
        self.drop_server.on_transfer_completed = self.on_transfer_completed
        self.drop_server.start()

        self.expanded = False
        self.current_worker = None
        self.current_image_b64 = None

        self.init_ui()
        self.reposition_to_top_center()

    def init_ui(self):
        # Frameless, Stays On Top, Tool (no taskbar clutter)
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
        self.notch_layout.setSpacing(10)

        self.icon_lbl = QLabel("⚡")
        self.icon_lbl.setStyleSheet("font-size: 14px;")
        self.notch_layout.addWidget(self.icon_lbl)

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

        self.notch_layout.addStretch()

        self.snip_btn = QPushButton("📸 Snip")
        self.snip_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.snip_btn.setStyleSheet(self._button_style())
        self.snip_btn.clicked.connect(self.capture_snip)
        self.notch_layout.addWidget(self.snip_btn)

        self.beam_btn = QPushButton("📡 Beam")
        self.beam_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.beam_btn.setStyleSheet(self._button_style())
        self.beam_btn.clicked.connect(self.choose_file_to_beam)
        self.notch_layout.addWidget(self.beam_btn)

        self.toggle_btn = QPushButton("▼")
        self.toggle_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.toggle_btn.setStyleSheet("background: transparent; color: #888; border: none; font-size: 11px;")
        self.toggle_btn.clicked.connect(self.toggle_expand)
        self.notch_layout.addWidget(self.toggle_btn)

        self.layout.addWidget(self.notch_bar)

        # 2. Expanded Chat & Share Section
        self.expand_container = QWidget()
        self.expand_layout = QVBoxLayout(self.expand_container)
        self.expand_layout.setContentsMargins(0, 4, 0, 0)
        self.expand_layout.setSpacing(6)

        self.chat_display = QTextEdit()
        self.chat_display.setReadOnly(True)
        self.chat_display.setStyleSheet("""
            background: rgba(15, 17, 23, 0.85);
            color: #e2e8f0;
            border: 1px solid rgba(255, 255, 255, 0.08);
            border-radius: 8px;
            padding: 8px;
            font-family: 'Segoe UI', sans-serif;
            font-size: 13px;
        """)
        self.expand_layout.addWidget(self.chat_display)

        # Input box row
        self.input_row = QHBoxLayout()
        self.input_field = QLineEdit()
        self.input_field.setPlaceholderText("Ask Lexi or command Windows...")
        self.input_field.setStyleSheet("""
            background: rgba(255, 255, 255, 0.06);
            color: #fff;
            border: 1px solid rgba(255, 255, 255, 0.15);
            border-radius: 6px;
            padding: 6px 10px;
            font-size: 13px;
        """)
        self.input_field.returnPressed.connect(self.on_send)
        self.input_row.addWidget(self.input_field)

        self.send_btn = QPushButton("Send")
        self.send_btn.setStyleSheet(self._button_style(primary=True))
        self.send_btn.clicked.connect(self.on_send)
        self.input_row.addWidget(self.send_btn)

        self.expand_layout.addLayout(self.input_row)

        self.expand_container.setVisible(False)
        self.layout.addWidget(self.expand_container)

        self.apply_theme()
        self.resize(380, 42)

    def _button_style(self, primary=False):
        if primary:
            return """
                QPushButton {
                    background: #6366f1;
                    color: white;
                    border: none;
                    border-radius: 6px;
                    padding: 5px 12px;
                    font-size: 12px;
                    font-weight: 600;
                }
                QPushButton:hover { background: #4f46e5; }
            """
        return """
            QPushButton {
                background: rgba(255, 255, 255, 0.08);
                color: #e2e8f0;
                border: 1px solid rgba(255, 255, 255, 0.12);
                border-radius: 6px;
                padding: 4px 10px;
                font-size: 11px;
            }
            QPushButton:hover { background: rgba(255, 255, 255, 0.16); }
        """

    def apply_theme(self):
        self.root_widget.setStyleSheet("""
            #rootWidget {
                background-color: rgba(13, 15, 22, 0.94);
                border: 1px solid rgba(255, 255, 255, 0.12);
                border-bottom-left-radius: 14px;
                border-bottom-right-radius: 14px;
            }
        """)

    def reposition_to_top_center(self):
        screen = QApplication.primaryScreen().geometry()
        x = (screen.width() - self.width()) // 2
        self.move(x, 0)

    def toggle_expand(self):
        self.expanded = not self.expanded
        self.expand_container.setVisible(self.expanded)
        self.toggle_btn.setText("▲" if self.expanded else "▼")
        if self.expanded:
            self.resize(480, 420)
            self.input_field.setFocus()
        else:
            self.resize(380, 42)
        self.reposition_to_top_center()

    def on_send(self):
        text = self.input_field.text().strip()
        if not text:
            return
        self.input_field.clear()

        # Display user question
        self.chat_display.append(f"<b>You:</b> {text}\n")
        self.status_pill.setText("● Thinking...")

        # Worker for non-blocking stream
        self.current_worker = StreamWorker(self.config, text, self.current_image_b64)
        self.current_image_b64 = None  # Reset one-shot image
        self.chat_display.append("<b>Lexi:</b> ")

        self.current_worker.token_received.connect(self.on_token)
        self.current_worker.finished.connect(self.on_finished)
        self.current_worker.error.connect(self.on_error)
        self.current_worker.start()

    def on_token(self, token: str):
        cursor = self.chat_display.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        cursor.insertText(token)
        self.chat_display.setTextCursor(cursor)

    def on_finished(self, full_text: str):
        self.status_pill.setText("● deepseek-flash")
        self.chat_display.append("\n")

    def on_error(self, err_msg: str):
        self.status_pill.setText("⚠️ Error")
        self.chat_display.append(f"\n<i>Error: {err_msg}</i>\n")

    def capture_snip(self):
        tmp_path = Path(os.path.expanduser("~/AppData/Local/Temp/lexi_snip.jpg"))
        saved = self.actions.capture_screen(str(tmp_path))
        if saved and os.path.exists(saved):
            with open(saved, "rb") as f:
                self.current_image_b64 = base64.b64encode(f.read()).decode("utf-8")
            if not self.expanded:
                self.toggle_expand()
            self.chat_display.append("<i>[📸 Attached Screen Capture. Ask anything about it.]</i>\n")
            self.input_field.setFocus()

    def choose_file_to_beam(self):
        file_path, _ = QFileDialog.getOpenFileName(self, "Select Movie or File to Beam")
        if not file_path:
            return

        peers = list(self.drop_server.beacon.lan_peers.values())
        if not peers:
            if not self.expanded:
                self.toggle_expand()
            self.chat_display.append(f"📡 <i>Scanning LAN... No Lexi peers found yet. Share via link: http://{self.drop_server.local_ip}:7890</i>\n")
            return

        # Target first available peer (e.g. Linux laptop)
        target = peers[0]
        res = self.drop_server.beam_file_to_peer(file_path, target["ip"])
        if not self.expanded:
            self.toggle_expand()
        if res.get("success"):
            self.chat_display.append(f"⚡ <b>Beaming '{os.path.basename(file_path)}'</b> to <b>{target['hostname']}</b> ({target['ip']})...\n")
        else:
            self.chat_display.append(f"⚠️ Failed to beam: {res.get('error')}\n")

    def on_incoming_file_proposed(self, info: Dict[str, Any]):
        """Triggered when Linux machine beams a file to this Windows notch."""
        if not self.expanded:
            self.toggle_expand()
        name = info.get("filename")
        size = format_size(info.get("size_bytes", 0))
        sender = info.get("from_device", "Nearby Linux")
        self.chat_display.append(f"\n📥 <b>Incoming Beam from {sender}:</b>\n• File: <b>{name}</b> ({size})\n• Saving straight into Downloads...\n")

    def on_transfer_progress(self, filename: str, pct: float, speed_mb: float):
        self.status_pill.setText(f"⚡ {pct:.0f}% · {speed_mb:.1f} MB/s")

    def on_transfer_completed(self, filename: str, dest_path: str):
        self.status_pill.setText("✓ Complete!")
        self.chat_display.append(f"✓ <b>Received '{filename}'!</b>\nSaved to: <code>{dest_path}</code>\n")
        QTimer.singleShot(4000, lambda: self.status_pill.setText("● deepseek-flash"))


def main():
    app = QApplication(sys.argv)
    island = LexiDynamicIslandWin()
    island.show()
    sys.exit(app.exec())


if __name__ == '__main__':
    main()
