#!/usr/bin/env python3
"""
Lexi Beam & Drop for Windows (ShareMe Native Engine)
===================================================
High-speed zero-data local peer-to-peer file transfer engine for Windows 10/11.
- Auto-discovery on LAN via UDP beacon (Port 7891)
- Compatible with Lexi Linux and Lexi Android Beam
- Native direct push: Receive incoming movies and files straight into ~/Downloads
- Zero internet data consumption: 100% direct Wi-Fi LAN transfer
"""

import os
import sys
import time
import json
import uuid
import socket
import asyncio
import threading
import urllib.request
import urllib.parse
import shutil
from pathlib import Path
from typing import Dict, Any, Optional, Callable
from aiohttp import web

PORT = 7890
BEACON_PORT = 7891

HOSTED_SHARES_DIR = Path.home() / "AppData" / "Local" / "Lexi" / "shares" if sys.platform == "win32" else Path.home() / ".local" / "share" / "mochi-copilot" / "shares"
HOSTED_SHARES_FILE = Path.home() / "AppData" / "Local" / "Lexi" / "hosted_shares.json" if sys.platform == "win32" else Path.home() / ".local" / "share" / "mochi-copilot" / "hosted_shares.json"

BEAM_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Lexi ShareMe Beam · Windows</title>
<style>
  :root {
    --bg: #0d0f17;
    --card: #161926;
    --card-border: rgba(255, 255, 255, 0.08);
    --accent: #6366f1;
    --accent-hover: #4f46e5;
    --success: #10b981;
    --text: #f1f5f9;
    --text-muted: #94a3b8;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    background-color: var(--bg);
    color: var(--text);
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    min-height: 100vh;
    display: flex;
    flex-direction: column;
    align-items: center;
    padding: 24px 16px;
  }
  .container { width: 100%; max-width: 580px; }
  .header { text-align: center; margin-bottom: 24px; }
  .badge {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    background: rgba(16, 185, 129, 0.15);
    color: var(--success);
    border: 1px solid rgba(16, 185, 129, 0.3);
    padding: 4px 12px;
    border-radius: 9999px;
    font-size: 12px;
    font-weight: 600;
    margin-bottom: 12px;
  }
  h1 { font-size: 24px; font-weight: 700; margin-bottom: 6px; }
  p.subtitle { color: var(--text-muted); font-size: 14px; }
  .card {
    background: var(--card);
    border: 1px solid var(--card-border);
    border-radius: 16px;
    padding: 24px;
    margin-bottom: 20px;
    box-shadow: 0 10px 30px rgba(0, 0, 0, 0.4);
  }
  .dropzone {
    border: 2px dashed rgba(99, 102, 241, 0.4);
    border-radius: 12px;
    padding: 36px 20px;
    text-align: center;
    cursor: pointer;
    transition: all 0.2s ease;
    background: rgba(99, 102, 241, 0.04);
  }
  .dropzone.dragover {
    border-color: var(--accent);
    background: rgba(99, 102, 241, 0.15);
    transform: scale(1.01);
  }
  .drop-icon { font-size: 40px; margin-bottom: 12px; display: block; }
  .btn {
    background: var(--accent);
    color: white;
    border: none;
    border-radius: 8px;
    padding: 10px 20px;
    font-size: 14px;
    font-weight: 600;
    cursor: pointer;
    margin-top: 14px;
    display: inline-block;
    transition: background 0.15s ease;
  }
  .btn:hover { background: var(--accent-hover); }
  .progress-wrap { display: none; margin-top: 18px; }
  .progress-bar-bg {
    background: rgba(255, 255, 255, 0.1);
    border-radius: 8px;
    height: 10px;
    overflow: hidden;
  }
  .progress-bar-fill {
    height: 100%;
    width: 0%;
    background: linear-gradient(90deg, #6366f1, #10b981);
    transition: width 0.15s ease;
  }
  .progress-info {
    display: flex;
    justify-content: space-between;
    font-size: 12px;
    color: var(--text-muted);
    margin-top: 6px;
  }
  .files-section h2 { font-size: 16px; margin-bottom: 12px; color: var(--text); }
  .file-item {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 12px;
    background: rgba(255, 255, 255, 0.03);
    border: 1px solid rgba(255, 255, 255, 0.06);
    border-radius: 10px;
    margin-bottom: 8px;
  }
  .file-name { font-weight: 600; font-size: 14px; }
  .file-size { color: var(--text-muted); font-size: 12px; }
  .file-download {
    background: rgba(16, 185, 129, 0.15);
    color: var(--success);
    border: 1px solid rgba(16, 185, 129, 0.3);
    border-radius: 6px;
    padding: 6px 12px;
    font-size: 12px;
    font-weight: 600;
    text-decoration: none;
  }
  .empty-state { color: var(--text-muted); font-size: 13px; text-align: center; padding: 12px; }
</style>
</head>
<body>
<div class="container">
  <div class="header">
    <div class="badge">● Zero-Data Direct LAN Beam</div>
    <h1 id="hostTitle">⚡ Lexi ShareMe Beam</h1>
    <p class="subtitle">Direct Wi-Fi file transfer · Saved to Windows Downloads</p>
  </div>

  <div class="card">
    <div class="dropzone" id="dropzone">
      <span class="drop-icon">📁</span>
      <h3>Drop movies or files to send to PC</h3>
      <p style="color: var(--text-muted); font-size: 13px; margin-top: 4px;">or click to select from your phone/device</p>
      <input type="file" id="fileInput" multiple style="display: none;">
      <button class="btn" onclick="document.getElementById('fileInput').click()">Select Files</button>
    </div>

    <div class="progress-wrap" id="progressWrap">
      <div class="progress-bar-bg">
        <div class="progress-bar-fill" id="progressFill"></div>
      </div>
      <div class="progress-info">
        <span id="progressText">Uploading...</span>
        <span id="speedText">0 MB/s</span>
      </div>
    </div>
  </div>

  <div class="card files-section">
    <h2>Available Files from PC</h2>
    <div id="fileList"><div class="empty-state">No files currently shared by PC</div></div>
  </div>
</div>

<script>
  const dropzone = document.getElementById('dropzone');
  const fileInput = document.getElementById('fileInput');
  const progressWrap = document.getElementById('progressWrap');
  const progressFill = document.getElementById('progressFill');
  const progressText = document.getElementById('progressText');
  const speedText = document.getElementById('speedText');
  const fileList = document.getElementById('fileList');

  fetch('/api/status').then(r => r.json()).then(data => {
    if (data.device) {
      document.getElementById('hostTitle').textContent = `⚡ Beam to ${data.device}`;
    }
  }).catch(() => {});

  function loadSharedFiles() {
    fetch('/api/files').then(r => r.json()).then(data => {
      if (data.files && data.files.length > 0) {
        fileList.innerHTML = data.files.map(f => `
          <div class="file-item">
            <div>
              <div class="file-name">${f.name}</div>
              <div class="file-size">${f.size_formatted}</div>
            </div>
            <a href="${f.url}" download="${f.name}" class="file-download">Download</a>
          </div>
        `).join('');
      } else {
        fileList.innerHTML = '<div class="empty-state">No files currently shared by PC</div>';
      }
    }).catch(() => {});
  }
  loadSharedFiles();
  setInterval(loadSharedFiles, 3000);

  ['dragenter', 'dragover'].forEach(name => {
    dropzone.addEventListener(name, (e) => { e.preventDefault(); dropzone.classList.add('dragover'); });
  });
  ['dragleave', 'drop'].forEach(name => {
    dropzone.addEventListener(name, (e) => { e.preventDefault(); dropzone.classList.remove('dragover'); });
  });

  dropzone.addEventListener('drop', (e) => {
    if (e.dataTransfer && e.dataTransfer.files.length > 0) {
      uploadFiles(Array.from(e.dataTransfer.files));
    }
  });

  fileInput.addEventListener('change', () => {
    if (fileInput.files.length > 0) {
      uploadFiles(Array.from(fileInput.files));
    }
  });

  async function uploadFiles(files) {
    for (const file of files) {
      await uploadSingleFile(file);
    }
  }

  function uploadSingleFile(file) {
    return new Promise((resolve) => {
      progressWrap.style.display = 'block';
      progressFill.style.width = '0%';
      progressText.textContent = `Uploading ${file.name}... (0%)`;
      speedText.textContent = '0 MB/s';

      const xhr = new XMLHttpRequest();
      const startTime = Date.now();

      xhr.upload.addEventListener('progress', (e) => {
        if (e.lengthComputable) {
          const pct = Math.round((e.loaded / e.total) * 100);
          progressFill.style.width = pct + '%';
          const elapsed = (Date.now() - startTime) / 1000;
          const mb = e.loaded / (1024 * 1024);
          const speed = elapsed > 0 ? (mb / elapsed).toFixed(1) : '0';
          progressText.textContent = `Uploading ${file.name}... (${pct}%)`;
          speedText.textContent = `${speed} MB/s`;
        }
      });

      xhr.addEventListener('load', () => {
        progressFill.style.width = '100%';
        progressText.textContent = `✓ Uploaded ${file.name}!`;
        setTimeout(() => { progressWrap.style.display = 'none'; }, 3000);
        resolve();
      });

      xhr.addEventListener('error', () => {
        progressText.textContent = `⚠️ Failed to upload ${file.name}`;
        resolve();
      });

      xhr.open('POST', `/api/upload?filename=${encodeURIComponent(file.name)}`);
      xhr.setRequestHeader('Content-Type', file.type || 'application/octet-stream');
      xhr.send(file);
    });
  }
</script>
</body>
</html>
"""

def _resolve_downloads_dir() -> Path:
    if sys.platform == "win32":
        try:
            import winreg
            sub_key = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders"
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, sub_key) as key:
                val, _ = winreg.QueryValueEx(key, "{374DE290-123F-4565-9164-39C4925E467B}")
                if val and os.path.exists(val):
                    return Path(val)
        except Exception:
            pass
    dl = Path.home() / "Downloads"
    try:
        dl.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return dl

DOWNLOADS_DIR = _resolve_downloads_dir()


def get_local_ip() -> str:
    """Detects primary local LAN IP on Windows."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(('10.255.255.255', 1))
        ip = s.getsockname()[0]
    except Exception:
        ip = '127.0.0.1'
    finally:
        s.close()
    return ip


def get_broadcast_ip(local_ip: str) -> str:
    """Derives subnet broadcast address."""
    parts = local_ip.split('.')
    if len(parts) == 4:
        return f"{parts[0]}.{parts[1]}.{parts[2]}.255"
    return "255.255.255.255"


def format_size(size_bytes: int) -> str:
    """Human-readable byte formatter."""
    if not size_bytes or size_bytes <= 0:
        return "0 B"
    sizes = ["B", "KB", "MB", "GB", "TB"]
    i = 0
    size = float(size_bytes)
    while size >= 1024.0 and i < len(sizes) - 1:
        size /= 1024.0
        i += 1
    return f"{size:.1f} {sizes[i]}"


class LexiWinPeerBeacon:
    """UDP Beacon for discovering nearby Linux and Windows peers."""
    def __init__(self, server):
        self.server = server
        self.port = server.port
        self.hostname = socket.gethostname() or "Windows-Lexi"
        self.running = False
        self.lan_peers: Dict[str, Dict[str, Any]] = {}
        self.lock = threading.Lock()

    def start(self):
        if self.running:
            return
        self.running = True
        threading.Thread(target=self._broadcast_loop, daemon=True, name="WinBeaconSender").start()
        threading.Thread(target=self._listen_loop, daemon=True, name="WinBeaconListener").start()

    def stop(self):
        self.running = False

    def _broadcast_loop(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)

        while self.running:
            try:
                curr_ip = self.server.local_ip
                bcast_ip = get_broadcast_ip(curr_ip)
                payload = json.dumps({
                    "type": "lexi_peer",
                    "hostname": self.hostname,
                    "ip": curr_ip,
                    "port": self.port,
                    "os": "windows",
                    "device_type": "laptop"
                }).encode('utf-8')
                sock.sendto(payload, ("255.255.255.255", BEACON_PORT))
                if bcast_ip != "255.255.255.255":
                    sock.sendto(payload, (bcast_ip, BEACON_PORT))
            except Exception:
                pass
            time.sleep(2.5)
        sock.close()

    def _listen_loop(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        except Exception:
            pass

        try:
            sock.bind(('', BEACON_PORT))
        except Exception as e:
            print(f"[LexiWinDrop] Beacon port {BEACON_PORT} bind error: {e}")
            sock.close()
            return

        sock.settimeout(2.0)
        while self.running:
            try:
                data, addr = sock.recvfrom(2048)
                peer_ip = addr[0]
                if peer_ip == self.server.local_ip or peer_ip in ("127.0.0.1", "localhost"):
                    continue

                msg = json.loads(data.decode('utf-8'))
                if msg.get("type") == "lexi_peer":
                    peer_host = msg.get("hostname", peer_ip)
                    if peer_host == self.hostname:
                        continue

                    now = time.time()
                    with self.lock:
                        is_new = peer_ip not in self.lan_peers
                        self.lan_peers[peer_ip] = {
                            "hostname": peer_host,
                            "ip": peer_ip,
                            "port": msg.get("port", PORT),
                            "last_seen": now,
                            "type": msg.get("device_type", "laptop"),
                            "os": msg.get("os", "unknown")
                        }
                    if is_new and self.server.on_peer_joined:
                        self.server.on_peer_joined(peer_host, peer_ip)
            except socket.timeout:
                pass
            except Exception:
                pass

            # Prune peers unseen for 8 seconds
            now = time.time()
            with self.lock:
                stale = [ip for ip, info in self.lan_peers.items() if now - info["last_seen"] > 8.0]
                for ip in stale:
                    removed = self.lan_peers.pop(ip)
                    if self.server.on_peer_left:
                        self.server.on_peer_left(removed.get("hostname", ip), ip)

        sock.close()


class LexiWinDropServer:
    """High-speed LAN receiver and sender for Windows."""
    def __init__(self, host="0.0.0.0", port=PORT):
        self.host = host
        self.port = port
        self.local_ip = get_local_ip()
        self.active_shared_files = {}  # token -> record
        self.latest_share_token = None
        self._load_persisted_shares()
        self.pending_incoming_transfers = {}  # token -> info
        self.beacon = LexiWinPeerBeacon(self)
        self.loop = None
        self.thread = None
        self.app = None
        self.runner = None
        self.site = None

        # Callbacks for UI
        self.on_peer_joined: Optional[Callable[[str, str], None]] = None
        self.on_peer_left: Optional[Callable[[str, str], None]] = None
        self.on_incoming_request: Optional[Callable[[Dict[str, Any]], None]] = None
        self.on_transfer_progress: Optional[Callable[[str, float, float], None]] = None
        self.on_transfer_completed: Optional[Callable[[str, str], None]] = None

    def start(self):
        """Starts the background event loop and web server."""
        if self.thread and self.thread.is_alive():
            return
        self.beacon.start()
        self.thread = threading.Thread(target=self._run_server, daemon=True, name="LexiWinDropServer")
        self.thread.start()

    def stop(self):
        """Stops the UDP beacon and gracefully shuts down the aiohttp server."""
        if hasattr(self, 'beacon') and self.beacon:
            self.beacon.stop()
        if self.loop and self.loop.is_running():
            try:
                if self.site:
                    asyncio.run_coroutine_threadsafe(self.site.stop(), self.loop)
                if self.runner:
                    asyncio.run_coroutine_threadsafe(self.runner.cleanup(), self.loop)
                self.loop.call_soon_threadsafe(self.loop.stop)
            except Exception:
                pass

    def _save_persisted_shares(self):
        try:
            HOSTED_SHARES_DIR.mkdir(parents=True, exist_ok=True)
            now = time.time()
            data = {}
            for tok, rec in list(self.active_shared_files.items()):
                if isinstance(rec, dict) and os.path.exists(rec.get("path", "")) and (rec.get("expires_at", 0) == 0 or rec.get("expires_at", 0) > now):
                    data[tok] = rec
            with open(HOSTED_SHARES_FILE, "w", encoding="utf-8") as f:
                json.dump({
                    "latest_token": getattr(self, "latest_share_token", None),
                    "shares": data
                }, f, indent=2)
        except Exception as e:
            print(f"[LexiWinDrop] Could not save hosted shares: {e}")

    def _load_persisted_shares(self):
        if not HOSTED_SHARES_FILE.exists():
            return
        try:
            with open(HOSTED_SHARES_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            now = time.time()
            shares = data.get("shares", {}) if (isinstance(data, dict) and "shares" in data) else (data if isinstance(data, dict) else {})
            valid = {}
            latest_tok = data.get("latest_token") if (isinstance(data, dict) and "latest_token" in data) else None
            latest_time = 0
            for tok, rec in list(shares.items()):
                if isinstance(rec, dict) and os.path.exists(rec.get("path", "")) and (rec.get("expires_at", 0) == 0 or rec.get("expires_at", 0) > now):
                    valid[tok] = rec
                    if rec.get("created_at", 0) >= latest_time:
                        latest_time = rec.get("created_at", 0)
                        if not latest_tok:
                            latest_tok = tok
            self.active_shared_files.update(valid)
            self.latest_share_token = latest_tok
            if valid:
                print(f"[LexiWinDrop] Restored {len(valid)} active 24h hosted shares.")
        except Exception as e:
            print(f"[LexiWinDrop] Could not restore hosted shares: {e}")

    def get_active_shares_summary(self):
        now = time.time()
        shares = []
        for tok, rec in list(self.active_shared_files.items()):
            if os.path.exists(rec["path"]) and (rec.get("expires_at", 0) == 0 or rec.get("expires_at", 0) > now):
                exp = rec.get("expires_at", 0)
                hrs = round((exp - now) / 3600.0, 1) if exp else 24.0
                shares.append({
                    "token": tok,
                    "name": rec["name"],
                    "size": rec["size"],
                    "size_formatted": format_size(rec["size"]),
                    "url": f"http://{self.local_ip}:{self.port}/api/download/{tok}",
                    "named_url": f"http://{self.local_ip}:{self.port}/download/{urllib.parse.quote(rec['name'])}",
                    "static_url": f"http://{self.local_ip}:{self.port}/download/latest",
                    "expires_in_seconds": max(0, int(exp - now)) if exp else 86400,
                    "hours_remaining": hrs,
                    "is_zip": rec.get("is_zip", False)
                })
        return shares

    def _run_server(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self.app = web.Application()

        self.app.router.add_get("/", self.handle_index)
        self.app.router.add_get("/api/status", self.handle_status)
        self.app.router.add_get("/api/files", self.handle_files_list)
        self.app.router.add_get("/api/shares", self.handle_shares_list)
        self.app.router.add_post("/api/upload", self.handle_upload)
        self.app.router.add_post("/api/peer/incoming", self.handle_peer_incoming)
        self.app.router.add_post("/api/peer/respond", self.handle_peer_respond)
        self.app.router.add_get("/api/download/{token}", self.handle_download)
        self.app.router.add_get("/download/latest", self.handle_download_latest)
        self.app.router.add_get("/download/{identifier}", self.handle_download_by_name_or_token)

        self.runner = web.AppRunner(self.app)
        self.loop.run_until_complete(self.runner.setup())
        self.site = web.TCPSite(self.runner, self.host, self.port, reuse_address=True)
        self.loop.run_until_complete(self.site.start())
        print(f"[LexiWinDrop] Ready on http://{self.local_ip}:{self.port}")
        self.loop.run_forever()

    async def handle_index(self, request):
        return web.Response(text=BEAM_HTML, content_type="text/html")

    async def handle_files_list(self, request):
        files = []
        for token, rec in list(self.active_shared_files.items()):
            if os.path.exists(rec["path"]):
                files.append({
                    "token": token,
                    "name": rec["name"],
                    "size": rec["size"],
                    "size_formatted": format_size(rec["size"]),
                    "url": f"/api/download/{token}"
                })
        return web.json_response({"files": files})

    async def handle_upload(self, request):
        """Streaming file upload handler (Phone/Browser/Peer -> Windows ~/Downloads)."""
        filename = request.query.get("filename") or request.headers.get("X-Filename")
        if not filename and request.content_type.startswith("multipart/form-data"):
            reader = await request.multipart()
            field = await reader.next()
            if field:
                filename = field.filename or f"upload_{int(time.time())}.bin"
                filename = urllib.parse.unquote(filename)
                dest_path = DOWNLOADS_DIR / filename
                stem = dest_path.stem
                suffix = dest_path.suffix
                idx = 1
                while dest_path.exists():
                    dest_path = DOWNLOADS_DIR / f"{stem} ({idx}){suffix}"
                    idx += 1
                part_path = dest_path.with_name(f".{dest_path.name}.lexipart")
                total_bytes = 0
                t0 = time.time()
                try:
                    with open(part_path, "wb") as f:
                        while True:
                            chunk = await field.read_chunk(4 * 1024 * 1024)
                            if not chunk:
                                break
                            f.write(chunk)
                            total_bytes += len(chunk)
                    os.replace(part_path, dest_path)
                except Exception:
                    if part_path.exists():
                        try:
                            part_path.unlink()
                        except Exception:
                            pass
                    raise
                elapsed = max(time.time() - t0, 0.001)
                speed_mb = (total_bytes / (1024 * 1024)) / elapsed
                if self.on_transfer_completed:
                    self.on_transfer_completed(dest_path.name, str(dest_path))
                return web.json_response({
                    "success": True,
                    "filename": dest_path.name,
                    "size": total_bytes,
                    "speed_mbps": round(speed_mb, 2)
                })

        filename = filename or f"upload_{int(time.time())}.bin"
        filename = urllib.parse.unquote(filename)
        dest_path = DOWNLOADS_DIR / filename
        stem = dest_path.stem
        suffix = dest_path.suffix
        idx = 1
        while dest_path.exists():
            dest_path = DOWNLOADS_DIR / f"{stem} ({idx}){suffix}"
            idx += 1
        part_path = dest_path.with_name(f".{dest_path.name}.lexipart")
        total_bytes = 0
        t0 = time.time()
        try:
            with open(part_path, "wb") as f:
                async for chunk in request.content.iter_chunked(4 * 1024 * 1024):
                    f.write(chunk)
                    total_bytes += len(chunk)
            os.replace(part_path, dest_path)
        except Exception:
            if part_path.exists():
                try:
                    part_path.unlink()
                except Exception:
                    pass
            raise
        elapsed = max(time.time() - t0, 0.001)
        speed_mb = (total_bytes / (1024 * 1024)) / elapsed
        if self.on_transfer_completed:
            self.on_transfer_completed(dest_path.name, str(dest_path))
        return web.json_response({
            "success": True,
            "filename": dest_path.name,
            "size": total_bytes,
            "speed_mbps": round(speed_mb, 2)
        })

    async def handle_status(self, request):
        return web.json_response({
            "status": "online",
            "device": socket.gethostname(),
            "os": "windows",
            "ip": self.local_ip,
            "port": self.port,
            "peers": len(self.beacon.lan_peers)
        })

    async def handle_peer_incoming(self, request):
        """Triggered when another Lexi machine (e.g. Linux) sends a file."""
        try:
            data = await request.json()
            token = data.get("token") or uuid.uuid4().hex[:10]
            transfer_info = {
                "token": token,
                "from_device": data.get("from_device", "Nearby Device"),
                "from_ip": data.get("from_ip", request.remote),
                "filename": data.get("filename", "transfer.bin"),
                "size_bytes": data.get("size_bytes", 0),
                "download_url": data.get("download_url")
            }
            self.pending_incoming_transfers[token] = transfer_info
            print(f"[LexiWinDrop] Incoming proposal: {transfer_info['filename']} ({format_size(transfer_info['size_bytes'])}) from {transfer_info['from_device']}")

            if self.on_incoming_request:
                self.on_incoming_request(transfer_info)
            else:
                # Default: auto-accept if no UI is bound
                threading.Thread(
                    target=self._download_peer_file_sync,
                    args=(transfer_info["download_url"], transfer_info["filename"], transfer_info["size_bytes"], token),
                    daemon=True
                ).start()

            return web.json_response({"status": "received_proposal", "token": token})
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)

    async def handle_peer_respond(self, request):
        """User responds to incoming proposal via UI."""
        try:
            data = await request.json()
            token = data.get("token")
            action = data.get("action", "accept")

            info = self.pending_incoming_transfers.pop(token, None)
            if not info:
                return web.json_response({"error": "Transfer expired"}, status=404)

            if action == "accept":
                threading.Thread(
                    target=self._download_peer_file_sync,
                    args=(info["download_url"], info["filename"], info["size_bytes"], token),
                    daemon=True
                ).start()
                return web.json_response({"status": "accepted", "token": token})
            return web.json_response({"status": "declined", "token": token})
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)

    async def handle_download(self, request):
        """Serves a staged file to downloading peers."""
        token = request.match_info.get("token")
        rec = self.active_shared_files.get(token)
        now = time.time()
        if not rec or not os.path.exists(rec["path"]) or (rec.get("expires_at", 0) and rec["expires_at"] < now):
            return web.Response(status=404, text="File expired or not found")
        filename = rec.get("name") or os.path.basename(rec["path"])
        return web.FileResponse(
            rec["path"],
            chunk_size=4 * 1024 * 1024,
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                "Content-Type": "application/octet-stream"
            }
        )

    async def handle_download_latest(self, request):
        """Streams the latest 24h hosted file or zip."""
        now = time.time()
        target_rec = None
        if self.latest_share_token and self.latest_share_token in self.active_shared_files:
            rec = self.active_shared_files[self.latest_share_token]
            if os.path.exists(rec["path"]) and (rec.get("expires_at", 0) == 0 or rec["expires_at"] > now):
                target_rec = rec

        if not target_rec:
            best_time = 0
            for rec in self.active_shared_files.values():
                if os.path.exists(rec["path"]) and (rec.get("expires_at", 0) == 0 or rec["expires_at"] > now):
                    if rec.get("created_at", 0) >= best_time:
                        best_time = rec.get("created_at", 0)
                        target_rec = rec

        if not target_rec:
            return web.Response(status=404, text="No active hosted files found.")

        filename = target_rec["name"]
        filepath = target_rec["path"]
        return web.FileResponse(
            filepath,
            chunk_size=4 * 1024 * 1024,
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                "Content-Type": "application/octet-stream"
            }
        )

    async def handle_download_by_name_or_token(self, request):
        """Permanent URL (/download/{identifier}) supporting either custom token or exact filename."""
        identifier = urllib.parse.unquote(request.match_info.get("identifier", ""))
        now = time.time()
        file_record = self.active_shared_files.get(identifier)
        if not file_record:
            for rec in self.active_shared_files.values():
                if rec.get("name") == identifier:
                    file_record = rec
                    break

        if not file_record or not os.path.exists(file_record["path"]) or (file_record.get("expires_at", 0) and file_record["expires_at"] < now):
            return web.Response(status=404, text=f"File '{identifier}' not found or hosting expired.")

        filename = file_record["name"]
        filepath = file_record["path"]
        return web.FileResponse(
            filepath,
            chunk_size=4 * 1024 * 1024,
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                "Content-Type": "application/octet-stream"
            }
        )

    async def handle_shares_list(self, request):
        """Returns JSON list of all active full-day hosted files."""
        shares = self.get_active_shares_summary()
        return web.json_response({
            "success": True,
            "port": self.port,
            "shares": shares,
            "latest_url": f"http://{self.local_ip}:{self.port}/download/latest" if shares else None
        })

    def _download_peer_file_sync(self, url: str, filename: str, total_bytes: int, token: str):
        """Streams the file over high-speed local TCP into ~/Downloads."""
        dest_path = DOWNLOADS_DIR / filename
        # Avoid overwriting
        stem = dest_path.stem
        suffix = dest_path.suffix
        idx = 1
        while dest_path.exists():
            dest_path = DOWNLOADS_DIR / f"{stem} ({idx}){suffix}"
            idx += 1

        print(f"[LexiWinDrop] Streaming {filename} to {dest_path}...")
        t0 = time.time()
        received_bytes = 0

        try:
            req = urllib.request.Request(url)
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(req, timeout=30) as resp, open(dest_path, "wb") as f:
                chunk_size = 2 * 1024 * 1024  # 2MB chunks for fast Windows buffer
                while True:
                    chunk = resp.read(chunk_size)
                    if not chunk:
                        break
                    f.write(chunk)
                    received_bytes += len(chunk)
                    elapsed = time.time() - t0
                    speed_mb = (received_bytes / (1024 * 1024)) / max(elapsed, 0.001)
                    pct = (received_bytes / total_bytes * 100.0) if total_bytes > 0 else 0.0
                    if self.on_transfer_progress:
                        self.on_transfer_progress(filename, pct, speed_mb)

            elapsed = max(time.time() - t0, 0.001)
            avg_speed = (received_bytes / (1024 * 1024)) / elapsed
            print(f"[LexiWinDrop] ✓ Transfer Complete! Saved to {dest_path} ({avg_speed:.1f} MB/s)")

            if self.on_transfer_completed:
                self.on_transfer_completed(filename, str(dest_path))

        except Exception as e:
            print(f"[LexiWinDrop] Download failed: {e}", file=sys.stderr)
            if dest_path.exists():
                try:
                    dest_path.unlink()
                except Exception:
                    pass

    def stage_file_for_sharing(self, file_path: str, pin_all_day: bool = True, custom_token: str = None) -> Dict[str, Any]:
        """Stages a local file or directory for 24h download by peers and browser clients."""
        p = Path(file_path)
        if not p.exists():
            return {"success": False, "error": "File does not exist"}

        actual_path = str(p.resolve())
        is_zip = False
        if p.is_dir():
            folder_name = p.name or "Archive"
            HOSTED_SHARES_DIR.mkdir(parents=True, exist_ok=True)
            zip_target = os.path.join(str(HOSTED_SHARES_DIR), f"{folder_name}.zip")
            shutil.make_archive(zip_target[:-4], 'zip', str(p))
            actual_path = zip_target
            is_zip = True
        elif p.name.lower().endswith(".zip"):
            is_zip = True

        size = os.path.getsize(actual_path)
        name = os.path.basename(actual_path)
        token = custom_token or uuid.uuid4().hex[:12]
        now = time.time()
        expires_at = now + (86400 if pin_all_day else 3600)

        record = {
            "token": token,
            "path": actual_path,
            "name": name,
            "size": size,
            "created_at": now,
            "expires_at": expires_at,
            "pin_all_day": pin_all_day,
            "is_zip": is_zip
        }
        self.active_shared_files[token] = record
        self.latest_share_token = token
        self._save_persisted_shares()

        url = f"http://{self.local_ip}:{self.port}/api/download/{token}"
        static_url = f"http://{self.local_ip}:{self.port}/download/latest"
        named_url = f"http://{self.local_ip}:{self.port}/download/{urllib.parse.quote(name)}"

        return {
            "success": True,
            "token": token,
            "filename": name,
            "name": name,
            "size": size,
            "download_url": url,
            "url": url,
            "static_url": static_url,
            "named_url": named_url,
            "expires_at": expires_at,
            "pin_all_day": pin_all_day,
            "is_zip": is_zip,
            "port": self.port
        }

    def beam_file_to_peer(self, file_path: str, peer_ip: str) -> Dict[str, Any]:
        """Pushes a file from Windows to a nearby Linux or Windows Lexi machine."""
        p = Path(file_path)
        if not p.exists():
            return {"success": False, "error": "File does not exist"}

        token = uuid.uuid4().hex[:12]
        self.active_shared_files[token] = {
            "token": token,
            "path": str(p.resolve()),
            "name": p.name,
            "size": p.stat().st_size
        }

        download_url = f"http://{self.local_ip}:{self.port}/api/download/{token}"
        target_port = PORT
        with self.beacon.lock:
            if peer_ip in self.beacon.lan_peers:
                target_port = self.beacon.lan_peers[peer_ip].get("port", PORT)

        payload = json.dumps({
            "from_device": socket.gethostname(),
            "from_ip": self.local_ip,
            "token": token,
            "filename": p.name,
            "size_bytes": p.stat().st_size,
            "download_url": download_url
        }).encode('utf-8')

        req = urllib.request.Request(
            f"http://{peer_ip}:{target_port}/api/peer/incoming",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        try:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(req, timeout=3.5) as resp:
                res = json.loads(resp.read().decode('utf-8'))
                res["success"] = True
                res["filename"] = p.name
                res["size"] = p.stat().st_size
                return res
        except Exception as e:
            return {"success": False, "error": f"Failed to reach peer: {e}"}


if __name__ == '__main__':
    server = LexiWinDropServer()
    server.start()
    print("Lexi Beam Windows Server is running. Press Ctrl+C to exit.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("Stopping...")
        server.beacon.stop()
