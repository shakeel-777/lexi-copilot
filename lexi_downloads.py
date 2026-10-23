"""
lexi_downloads.py - Dynamic Island Live Download Monitor and Sentinel.

Features:
1. Non-blocking watcher for ~/Downloads tracking active in-progress files
   (.crdownload, .part, .download, .tmp, or actively growing files).
2. Real-time download speed (MB/s or KB/s) and estimated size calculation.
3. Sentinel Safety Gate: Alerts and protects downloads from being interrupted
   if the user or an automated action tries to close Chrome, quit apps, or shut down.
4. Notifies listeners (Dynamic Island pill) when downloads start, progress, and finish.
"""

import os
import time
import threading
from typing import Dict, List, Optional, Callable


class DownloadItem:
    def __init__(self, path: str):
        self.path = path
        self.filename = os.path.basename(path)
        # Strip temporary extension to get the intended target filename
        self.target_name = self.filename
        for ext in ('.crdownload', '.part', '.download', '.tmp'):
            if self.filename.endswith(ext):
                self.target_name = self.filename[:-len(ext)]
                break

        self.last_size = 0
        self.current_size = 0
        self.speed_bps = 0.0
        self.last_check_time = time.time()
        self.start_time = self.last_check_time
        self.is_completed = False

    def update_size(self, new_size: int, now: float):
        dt = max(0.1, now - self.last_check_time)
        delta_bytes = max(0, new_size - self.current_size)
        # Exponential smoothing on download speed
        instant_speed = delta_bytes / dt
        if self.speed_bps == 0.0:
            self.speed_bps = instant_speed
        else:
            self.speed_bps = (self.speed_bps * 0.7) + (instant_speed * 0.3)

        self.last_size = self.current_size
        self.current_size = new_size
        self.last_check_time = now

    def format_size(self) -> str:
        s = self.current_size
        if s >= 1024 * 1024 * 1024:
            return f"{s / (1024 * 1024 * 1024):.2f} GB"
        if s >= 1024 * 1024:
            return f"{s / (1024 * 1024):.1f} MB"
        if s >= 1024:
            return f"{s / 1024:.0f} KB"
        return f"{s} B"

    def format_speed(self) -> str:
        spd = self.speed_bps
        if spd >= 1024 * 1024:
            return f"{spd / (1024 * 1024):.1f} MB/s"
        if spd >= 1024:
            return f"{spd / 1024:.0f} KB/s"
        return f"{spd:.0f} B/s"

    def to_dict(self) -> dict:
        return {
            "filename": self.target_name,
            "raw_filename": self.filename,
            "path": self.path,
            "size_str": self.format_size(),
            "speed_str": self.format_speed(),
            "bytes": self.current_size,
            "speed_bps": self.speed_bps,
            "is_completed": self.is_completed,
        }


class DownloadSentinel:
    """
    Watches ~/Downloads and provides real-time state for the Dynamic Island
    and safety verification for app termination.
    """
    _instance = None
    _lock = threading.Lock()

    @classmethod
    def get(cls, download_dir: Optional[str] = None):
        with cls._lock:
            if cls._instance is None:
                cls._instance = DownloadSentinel(download_dir=download_dir)
            return cls._instance

    def __init__(self, download_dir: Optional[str] = None):
        self.download_dir = download_dir or os.path.expanduser("~/Downloads")
        self._active_downloads: Dict[str, DownloadItem] = {}
        self._recent_completed: List[dict] = []
        self._listeners: List[Callable[[dict], None]] = []
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._state_lock = threading.RLock()

    def add_listener(self, cb: Callable[[dict], None]):
        """Subscribe to download status updates (e.g. Dynamic Island widget)."""
        with self._state_lock:
            if cb not in self._listeners:
                self._listeners.append(cb)

    def remove_listener(self, cb: Callable[[dict], None]):
        with self._state_lock:
            if cb in self._listeners:
                self._listeners.remove(cb)

    def start(self):
        with self._state_lock:
            if self._running:
                return
            self._running = True
            self._thread = threading.Thread(target=self._watch_loop, daemon=True, name="LexiDownloadSentinel")
            self._thread.start()

    def stop(self):
        with self._state_lock:
            self._running = False

    def scan_once(self):
        """Perform a single scan of the download directory."""
        if not os.path.isdir(self.download_dir):
            return

        now = time.time()
        found_active_paths = set()

        try:
            entries = os.scandir(self.download_dir)
        except Exception:
            return

        for entry in entries:
            try:
                name = entry.name
                if name.startswith("."):
                    continue

                # Check known active temporary download extensions (.crdownload, .part, etc.)
                is_partial = any(name.endswith(ext) for ext in ('.crdownload', '.part', '.download', '.tmp'))

                if is_partial and not entry.is_dir():
                    stat = entry.stat()
                    found_active_paths.add(entry.path)
                    with self._state_lock:
                        if entry.path not in self._active_downloads:
                            item = DownloadItem(entry.path)
                            item.update_size(stat.st_size, now)
                            self._active_downloads[entry.path] = item
                        else:
                            self._active_downloads[entry.path].update_size(stat.st_size, now)
            except Exception:
                continue

        events_to_emit = []
        # Check for completed downloads (files that vanished from active temporary list)
        with self._state_lock:
            missing_paths = set(self._active_downloads.keys()) - found_active_paths
            for p in missing_paths:
                item = self._active_downloads.pop(p)
                # Check if final file exists (temporary extension removed)
                target_full = os.path.join(self.download_dir, item.target_name)
                if os.path.exists(target_full):
                    item.is_completed = True
                    item_dict = item.to_dict()
                    self._recent_completed.append(item_dict)
                    if len(self._recent_completed) > 10:
                        self._recent_completed.pop(0)
                    events_to_emit.append(("completed", item_dict))

            if self._active_downloads:
                active_list = [it.to_dict() for it in self._active_downloads.values()]
                events_to_emit.append(("progress", active_list))
            elif not found_active_paths and missing_paths:
                events_to_emit.append(("idle", []))

        # Notify outside lock
        for ev, data in events_to_emit:
            self._notify_event(ev, data)

    def _watch_loop(self):
        while self._running:
            self.scan_once()
            time.sleep(1.5)

    def _notify_event(self, event_type: str, data):
        with self._state_lock:
            listeners = list(self._listeners)
        payload = {"event": event_type, "data": data}
        for cb in listeners:
            try:
                cb(payload)
            except Exception:
                pass

    def has_active_downloads(self) -> bool:
        """Sentinel check: True if there are any active downloads in progress."""
        with self._state_lock:
            return len(self._active_downloads) > 0

    def get_active_downloads(self) -> List[dict]:
        with self._state_lock:
            return [it.to_dict() for it in self._active_downloads.values()]

    def get_download_summary(self) -> str:
        """Returns a human-readable summary of in-progress downloads."""
        with self._state_lock:
            if not self._active_downloads:
                return "No active downloads in progress."
            lines = []
            for it in self._active_downloads.values():
                lines.append(f"• **{it.target_name}**: {it.format_size()} at {it.format_speed()}")
            return "Active Downloads:\n" + "\n".join(lines)
