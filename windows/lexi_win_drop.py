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
from pathlib import Path
from typing import Dict, Any, Optional, Callable
from aiohttp import web

PORT = 7890
BEACON_PORT = 7891
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

    def _run_server(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self.app = web.Application()

        self.app.router.add_post("/api/peer/incoming", self.handle_peer_incoming)
        self.app.router.add_post("/api/peer/respond", self.handle_peer_respond)
        self.app.router.add_get("/api/download/{token}", self.handle_download)
        self.app.router.add_get("/api/status", self.handle_status)

        self.runner = web.AppRunner(self.app)
        self.loop.run_until_complete(self.runner.setup())
        self.site = web.TCPSite(self.runner, self.host, self.port)
        self.loop.run_until_complete(self.site.start())
        print(f"[LexiWinDrop] Ready on http://{self.local_ip}:{self.port}")
        self.loop.run_forever()

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
        if not rec or not os.path.exists(rec["path"]):
            return web.Response(status=404, text="File expired")
        return web.FileResponse(rec["path"], chunk_size=4 * 1024 * 1024)

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

    def beam_file_to_peer(self, file_path: str, peer_ip: str) -> Dict[str, Any]:
        """Pushes a file from Windows to a nearby Linux or Windows Lexi machine."""
        p = Path(file_path)
        if not p.exists():
            return {"success": False, "error": "File does not exist"}

        token = uuid.uuid4().hex[:12]
        self.active_shared_files[token] = {
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
