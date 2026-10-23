"""
lexi_productivity.py - Focus/Pomodoro Timer & Universal Clipboard Vault.

Features:
1. FocusTimer: Dynamic Island countdown timer with audio chime & desktop notifications.
2. ClipboardManager: Searchable history of recent clipboard entries (Code, URLs, Colors, Text)
   with instant copy-back and companion phone sync.
"""

import os
import re
import json
import time
import shutil
import threading
import subprocess
from typing import Dict, List, Optional, Callable


class FocusTimer:
    """
    Manages focus sessions, pomodoros, and countdown timers.
    Notifies Dynamic Island every second for smooth ticking UI.
    """
    _instance = None
    _lock = threading.RLock()

    @classmethod
    def get(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = FocusTimer()
            return cls._instance

    def __init__(self):
        self.active = False
        self.label = "Deep Work"
        self.total_seconds = 0
        self.remaining_seconds = 0
        self.end_time = 0.0
        self._listeners: List[Callable[[dict], None]] = []
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._state_lock = threading.RLock()

    def add_listener(self, cb: Callable[[dict], None]):
        with self._state_lock:
            if cb not in self._listeners:
                self._listeners.append(cb)

    def remove_listener(self, cb: Callable[[dict], None]):
        with self._state_lock:
            if cb in self._listeners:
                self._listeners.remove(cb)

    def start(self, minutes: int = 25, label: str = "Focus"):
        with self._state_lock:
            self.total_seconds = max(10, int(minutes * 60))
            self.remaining_seconds = self.total_seconds
            self.end_time = time.time() + self.remaining_seconds
            self.label = label or "Focus"
            self.active = True
            self._stop_event.clear()

            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._tick_loop, daemon=True, name="LexiFocusTimer")
                self._thread.start()

        self._notify("start")
        return {
            "success": True,
            "message": f"⏳ Started {minutes}-minute focus session ({self.label}). You've got this!",
            "remaining_seconds": self.remaining_seconds,
            "formatted": self.formatted_time(),
        }

    def stop(self):
        with self._state_lock:
            if not self.active:
                return {"success": True, "message": "No active focus timer to stop."}
            self.active = False
            self.remaining_seconds = 0
            self._stop_event.set()

        self._notify("stop")
        return {"success": True, "message": "⏹️ Focus timer stopped."}

    def status(self) -> dict:
        with self._state_lock:
            if not self.active:
                return {"active": False, "message": "No focus timer running. Say “focus 25 minutes” or “pomodoro” to start."}
            return {
                "active": True,
                "label": self.label,
                "remaining_seconds": self.remaining_seconds,
                "formatted": self.formatted_time(),
                "percent": int(((self.total_seconds - self.remaining_seconds) / max(1, self.total_seconds)) * 100),
            }

    def formatted_time(self) -> str:
        s = max(0, self.remaining_seconds)
        mins = s // 60
        secs = s % 60
        return f"{mins:02d}:{secs:02d}"

    def _tick_loop(self):
        while not self._stop_event.wait(1.0):
            with self._state_lock:
                if not self.active:
                    break
                now = time.time()
                rem = int(self.end_time - now)
                if rem <= 0:
                    self.active = False
                    self.remaining_seconds = 0
                    self._stop_event.set()
                    is_complete = True
                else:
                    self.remaining_seconds = rem
                    is_complete = False

            if is_complete:
                self._notify("complete")
                self._trigger_alarm()
                break
            else:
                self._notify("tick")

    def _notify(self, event: str):
        payload = {
            "event": event,
            "active": self.active,
            "label": self.label,
            "remaining_seconds": self.remaining_seconds,
            "formatted": self.formatted_time(),
        }
        with self._state_lock:
            listeners = list(self._listeners)
        for cb in listeners:
            try:
                cb(payload)
            except Exception:
                pass

    def _trigger_alarm(self):
        # Audible beep / chime via system bell or aplay
        try:
            if shutil.which("paplay"):
                subprocess.run(["paplay", "/usr/share/sounds/freedesktop/stereo/complete.oga"], capture_output=True, timeout=2)
            elif shutil.which("notify-send"):
                subprocess.run(["notify-send", "-u", "critical", "🔔 Lexi Focus Complete!", f"Great work on {self.label}! Take a 5-minute break."], capture_output=True, timeout=2)
        except Exception:
            pass


class ClipboardManager:
    """
    Monitors system clipboard, stores last 50 items with rich categorization
    (code, url, hex color, text) and provides instant restoration.
    """
    _instance = None
    _lock = threading.Lock()

    @classmethod
    def get(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = ClipboardManager()
            return cls._instance

    def __init__(self):
        self.data_path = os.path.expanduser("~/.local/share/mochi-copilot/clipboard_history.json")
        self.history: List[dict] = []
        self._last_copied = ""
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._state_lock = threading.Lock()
        self._load()

    def _load(self):
        if os.path.exists(self.data_path):
            try:
                with open(self.data_path, "r", encoding="utf-8") as f:
                    self.history = json.load(f)
            except Exception:
                self.history = []

    def _save(self):
        try:
            os.makedirs(os.path.dirname(self.data_path), exist_ok=True)
            with open(self.data_path, "w", encoding="utf-8") as f:
                json.dump(self.history[:50], f, indent=2)
        except Exception:
            pass

    def start_listener(self):
        with self._state_lock:
            if self._running:
                return
            self._running = True
            self._thread = threading.Thread(target=self._poll_loop, daemon=True, name="LexiClipboardPoll")
            self._thread.start()

    def _get_raw_clipboard(self) -> str:
        for cmd in (["wl-paste", "--no-newline"], ["xclip", "-selection", "clipboard", "-o"], ["xsel", "--clipboard", "--output"]):
            if shutil.which(cmd[0]):
                try:
                    r = subprocess.run(cmd, capture_output=True, text=True, timeout=0.8)
                    if r.returncode == 0 and r.stdout:
                        return r.stdout
                except Exception:
                    pass
        return ""

    def _classify_type(self, text: str) -> str:
        clean = text.strip()
        if re.match(r"^https?://[^\s]+$", clean):
            return "url"
        if re.match(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$", clean):
            return "color"
        if re.search(r"[{};=()\[\]]|\b(?:def|class|function|const|let|var|import|return|echo|sudo|pacman|apt)\b", clean):
            return "code"
        return "text"

    def record_text(self, text: str):
        if not text or not text.strip():
            return
        clean = text.strip()
        with self._state_lock:
            if clean == self._last_copied:
                return
            self._last_copied = clean

            # Deduplicate if already present
            self.history = [h for h in self.history if h.get("text") != clean]

            entry = {
                "text": clean,
                "preview": clean[:120].replace("\n", " "),
                "type": self._classify_type(clean),
                "timestamp": int(time.time()),
                "chars": len(clean),
            }
            self.history.insert(0, entry)
            if len(self.history) > 50:
                self.history = self.history[:50]
            self._save()

    def _poll_loop(self):
        while self._running:
            raw = self._get_raw_clipboard()
            if raw and raw.strip():
                self.record_text(raw)
            time.sleep(1.2)

    def get_history(self, limit: int = 10) -> List[dict]:
        with self._state_lock:
            return list(self.history[:limit])

    def copy_item(self, index: int) -> bool:
        """Copy item back to desktop clipboard by 1-based or 0-based index."""
        with self._state_lock:
            idx = index - 1 if index > 0 else index
            if idx < 0 or idx >= len(self.history):
                return False
            text = self.history[idx]["text"]

        # Push to clipboard
        copied = False
        if shutil.which("wl-copy"):
            try:
                subprocess.run(["wl-copy"], input=text, text=True, timeout=1.0)
                copied = True
            except Exception:
                pass
        if not copied and shutil.which("xclip"):
            try:
                subprocess.run(["xclip", "-selection", "clipboard"], input=text, text=True, timeout=1.0)
                copied = True
            except Exception:
                pass
        return copied

    def format_history_reply(self, limit: int = 5) -> str:
        items = self.get_history(limit)
        if not items:
            return "📋 Clipboard vault is currently empty."
        type_icons = {"code": "💻", "url": "🔗", "color": "🎨", "text": "📝"}
        lines = ["📋 **Recent Clipboard History**:"]
        for i, item in enumerate(items, 1):
            icon = type_icons.get(item.get("type"), "📝")
            preview = item.get("preview", "")[:60]
            lines.append(f"{i}. {icon} `{preview}`")
        lines.append("\n*Say “copy item <number>” to restore any snippet to your clipboard.*")
        return "\n".join(lines)
