#!/usr/bin/env python3
"""
Lexi Beam & Drop Engine
=======================
Proprietary high-speed local file sharing and LAN peer transfer engine for Lexi.
- Zero-copy Linux kernel streaming (os.sendfile) with 4MB chunk buffers
- True Peer-to-Peer direct transfer between Lexi Linux machines (UDP Beacon & Direct Push)
- Dynamic QR code generation for instant Android camera scanning
- Integrated PWA web receiver for Windows/Mac/mobile with live WebSocket telemetry
"""

import os
import sys
import time
import uuid
import json
import socket
import asyncio
import threading
import subprocess
import shutil
import urllib.request
from pathlib import Path
from aiohttp import web, WSMsgType

PORT = 7890
BEACON_PORT = 7891
DROP_WEB_DIR = Path(__file__).parent / "drop_web"
DOWNLOADS_DIR = Path(os.path.expanduser("~/Downloads"))
DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)


def get_local_ip():
    """Dynamically determine the primary local Wi-Fi / LAN IP address."""
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
    """Derive subnet broadcast IP from local IP."""
    parts = local_ip.split('.')
    if len(parts) == 4:
        return f"{parts[0]}.{parts[1]}.{parts[2]}.255"
    return "255.255.255.255"


def auto_unlock_phone(device_id: str):
    """
    Zero-touch automated unlock sequence for Android device over ADB.
    Loads password from config.json ('phone_password') and handles wake + swipe + PIN.
    """
    try:
        time.sleep(0.5)  # Wait for scrcpy video stream initialization
        app_dir = Path(__file__).parent
        cfg_file = app_dir / "config.json"
        password = None
        if cfg_file.exists():
            with open(cfg_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                password = data.get("phone_password")

        # Check keyguard / sleep state
        check = subprocess.run(
            ['adb', '-s', device_id, 'shell', 'dumpsys window | grep isKeyguardShowing; dumpsys power | grep mWakefulness='],
            capture_output=True, text=True, timeout=3
        )
        out = check.stdout.lower()
        if 'iskeyguardshowing=true' in out or 'asleep' in out or 'dozing' in out:
            # 1. Wake screen up
            subprocess.run(['adb', '-s', device_id, 'shell', 'input', 'keyevent', '224'], timeout=2)
            time.sleep(0.7)
            # 2. Swipe up to reveal lockscreen PIN/password input
            subprocess.run(['adb', '-s', device_id, 'shell', 'input', 'swipe', '540', '1800', '540', '500', '250'], timeout=2)
            time.sleep(0.5)
            # 3. Enter password if configured
            if password:
                subprocess.run(['adb', '-s', device_id, 'shell', 'input', 'text', str(password)], timeout=3)
                time.sleep(0.3)
                subprocess.run(['adb', '-s', device_id, 'shell', 'input', 'keyevent', '66'], timeout=2)
    except Exception as e:
        print(f"[LexiDrop] Auto-unlock error: {e}", file=sys.stderr)


def get_unique_destination_path(directory: Path, filename: str) -> Path:
    """Avoid overwriting existing files by appending incremental counters."""
    stem = Path(filename).stem
    suffix = Path(filename).suffix
    candidate = directory / filename
    counter = 1
    while candidate.exists():
        candidate = directory / f"{stem} ({counter}){suffix}"
        counter += 1
    return candidate


class LexiPeerBeacon:
    """
    Lightweight UDP Multicast / Broadcast Peer Discovery Beacon.
    Continuously announces Lexi's presence on the LAN and discovers nearby Lexi machines.
    """
    def __init__(self, server):
        self.server = server
        self.local_ip = server.local_ip
        self.port = server.port
        self.hostname = socket.gethostname() or "Linux-Lexi"
        self.running = False
        self.lan_peers = {}  # ip -> {hostname, ip, port, last_seen, type}
        self.lock = threading.Lock()

    def start(self):
        if self.running:
            return
        self.running = True
        threading.Thread(target=self._broadcast_loop, daemon=True, name="LexiBeaconSender").start()
        threading.Thread(target=self._listen_loop, daemon=True, name="LexiBeaconListener").start()

    def stop(self):
        self.running = False

    def _broadcast_loop(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        bcast_ip = get_broadcast_ip(self.local_ip)

        payload = json.dumps({
            "type": "lexi_peer",
            "hostname": self.hostname,
            "ip": self.local_ip,
            "port": self.port,
            "os": "linux"
        }).encode('utf-8')

        while self.running:
            try:
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
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        except Exception:
            pass

        try:
            sock.bind(('', BEACON_PORT))
        except Exception as e:
            print(f"[LexiBeacon] Failed to bind beacon port {BEACON_PORT}: {e}")
            sock.close()
            return

        sock.settimeout(2.0)
        while self.running:
            try:
                data, addr = sock.recvfrom(2048)
                peer_ip = addr[0]
                if peer_ip == self.local_ip:
                    continue  # Ignore self
                msg = json.loads(data.decode('utf-8'))
                if msg.get("type") == "lexi_peer":
                    peer_host = msg.get("hostname", peer_ip)
                    now = time.time()
                    with self.lock:
                        is_new = peer_ip not in self.lan_peers
                        self.lan_peers[peer_ip] = {
                            "hostname": peer_host,
                            "ip": peer_ip,
                            "port": msg.get("port", PORT),
                            "last_seen": now,
                            "type": "linux_p2p"
                        }
                    if is_new and self.server.on_peer_joined:
                        self.server.on_peer_joined(peer_host, peer_ip)
            except socket.timeout:
                pass
            except Exception:
                pass

            # Prune stale peers older than 8 seconds
            now = time.time()
            with self.lock:
                stale = [ip for ip, info in self.lan_peers.items() if now - info["last_seen"] > 8.0]
                for ip in stale:
                    removed = self.lan_peers.pop(ip)
                    if self.server.on_peer_left:
                        self.server.on_peer_left(removed.get("hostname", ip), ip)

        sock.close()


class LexiDropServer:
    def __init__(self, host="0.0.0.0", port=PORT):
        self.host = host
        self.port = port
        self.local_ip = get_local_ip()
        self.active_shared_files = {}  # token -> {path, name, size, time}
        self.connected_websockets = set()
        self.connected_peers = {}      # ws -> {device, ip, connected_at}
        self.pending_incoming_transfers = {}  # token -> transfer_dict
        self.loop = None
        self.thread = None
        self.app = None
        self.runner = None
        self.site = None

        # Beacon for True P2P Discovery
        self.beacon = LexiPeerBeacon(self)

        # Callbacks for GTK / Dynamic Island integration
        self.on_peer_joined = None
        self.on_peer_left = None
        self.on_file_received = None
        self.on_p2p_incoming_request = None
        self.on_transfer_progress = None
        self.on_clipboard_received = None
        self.on_camera_snap_received = None
        self.current_clipboard_text = ""

    def get_server_url(self):
        return f"http://{self.local_ip}:{self.port}"

    def get_all_peers(self) -> list:
        """Returns unified list of active peers (both Linux P2P and Web/PWA clients)."""
        seen_ips = set()
        result = []

        # 1. Linux P2P peers discovered via UDP beacon
        with self.beacon.lock:
            for ip, info in self.beacon.lan_peers.items():
                seen_ips.add(ip)
                result.append({
                    "device": info["hostname"],
                    "ip": ip,
                    "type": "linux_p2p",
                    "port": info.get("port", PORT)
                })

        # 2. Web / PWA connected peers (e.g. Windows PC, Android Phone)
        for info in self.connected_peers.values():
            ip = info.get("ip")
            if ip not in seen_ips:
                seen_ips.add(ip)
                result.append({
                    "device": info.get("device", "Device"),
                    "ip": ip,
                    "type": "web_pwa",
                    "port": PORT
                })

        return result

    def generate_qr_code(self, target_url=None, save_path="/tmp/lexi_beam_qr.png") -> str:
        """Generates a high-contrast dark obsidian QR code for Android phone camera scanning."""
        import qrcode
        url = target_url or self.get_server_url()
        qr = qrcode.QRCode(version=1, box_size=8, border=2)
        qr.add_data(url)
        qr.make(fit=True)
        img = qr.make_image(fill_color='white', back_color='#0a0b0e').convert('RGB')
        img.save(save_path)
        return save_path

    async def handle_index(self, request):
        return web.FileResponse(DROP_WEB_DIR / "index.html")

    async def handle_manifest(self, request):
        return web.FileResponse(DROP_WEB_DIR / "manifest.json", headers={
            "Content-Type": "application/manifest+json"
        })

    async def handle_sw(self, request):
        return web.FileResponse(DROP_WEB_DIR / "sw.js", headers={
            "Content-Type": "application/javascript"
        })

    async def handle_style(self, request):
        return web.FileResponse(DROP_WEB_DIR / "style.css", headers={
            "Content-Type": "text/css"
        })

    async def handle_app_js(self, request):
        return web.FileResponse(DROP_WEB_DIR / "app.js", headers={
            "Content-Type": "application/javascript"
        })

    async def handle_icon(self, request):
        icon_file = DROP_WEB_DIR / "icon.png"
        if not icon_file.exists():
            icon_file = DROP_WEB_DIR / "icon-512.png"
        return web.FileResponse(icon_file, headers={"Content-Type": "image/png"})

    async def handle_icon_192(self, request):
        return web.FileResponse(DROP_WEB_DIR / "icon-192.png", headers={"Content-Type": "image/png"})

    async def handle_icon_512(self, request):
        return web.FileResponse(DROP_WEB_DIR / "icon-512.png", headers={"Content-Type": "image/png"})

    async def handle_apk(self, request):
        apk_file = DROP_WEB_DIR / "lexi-connect.apk"
        if not apk_file.exists():
            return web.Response(status=404, text="APK not found")
        return web.FileResponse(apk_file, headers={
            "Content-Type": "application/vnd.android.package-archive",
            "Content-Disposition": 'attachment; filename="lexi-connect.apk"'
        })

    async def handle_ws(self, request):
        """Realtime WebSocket endpoint for peer synchronization and transfer notifications."""
        ws = web.WebSocketResponse(heartbeat=25.0)
        await ws.prepare(request)

        client_ip = request.remote or "Unknown"
        self.connected_websockets.add(ws)
        self.connected_peers[ws] = {
            "device": "Device",
            "ip": client_ip,
            "connected_at": time.time()
        }

        # Send initial host handshake
        await ws.send_json({
            "type": "peer_update",
            "host_name": socket.gethostname() or "Linux",
            "ip": self.local_ip,
            "port": self.port
        })

        try:
            async for msg in ws:
                if msg.type == WSMsgType.TEXT:
                    try:
                        data = json.loads(msg.data)
                        msg_type = data.get("type")
                        if msg_type == "handshake":
                            device_label = data.get("device", "Nearby Device")
                            self.connected_peers[ws]["device"] = device_label
                            if self.on_peer_joined:
                                self.on_peer_joined(device_label, client_ip)
                        elif msg_type == "clipboard_sync":
                            text = data.get("text", "")
                            if text:
                                self.current_clipboard_text = text
                                if self.on_clipboard_received:
                                    self.on_clipboard_received(text)
                                # Broadcast to other connected clients
                                self.broadcast_message({"type": "clipboard_sync", "text": text, "source": "mobile"})
                        elif msg_type == "camera_snap":
                            b64_data = data.get("b64", "")
                            if b64_data:
                                import base64
                                raw_bytes = base64.b64decode(b64_data)
                                snap_filename = f"camera_snap_{int(time.time())}.jpg"
                                snap_path = Path("/tmp") / snap_filename
                                with open(snap_path, "wb") as f:
                                    f.write(raw_bytes)
                                if self.on_camera_snap_received:
                                    self.on_camera_snap_received(str(snap_path))
                    except Exception as e:
                        print(f"[LexiDrop WS] Error parsing message: {e}")
                elif msg.type == WSMsgType.ERROR:
                    print(f"[LexiDrop WS] Connection error: {ws.exception()}")
        finally:
            peer_info = self.connected_peers.pop(ws, None)
            self.connected_websockets.discard(ws)
            if peer_info and self.on_peer_left:
                self.on_peer_left(peer_info.get("device", "Device"), client_ip)

        return ws

    async def handle_upload(self, request):
        """Streaming file upload handler (Windows/Android -> Linux ~/Downloads)."""
        filename = request.query.get("filename") or request.headers.get("X-Filename") or f"upload_{int(time.time())}.bin"
        import urllib.parse
        filename = urllib.parse.unquote(filename)
        dest_path = get_unique_destination_path(DOWNLOADS_DIR, filename)

        total_bytes = 0
        t0 = time.time()
        chunk_size = 4 * 1024 * 1024

        with open(dest_path, "wb") as f:
            async for chunk in request.content.iter_chunked(chunk_size):
                f.write(chunk)
                total_bytes += len(chunk)

        elapsed = max(time.time() - t0, 0.001)
        speed_mbps = (total_bytes / (1024 * 1024)) / elapsed

        result = {
            "success": True,
            "filename": dest_path.name,
            "saved_to": str(dest_path),
            "size_bytes": total_bytes,
            "speed_mbps": round(speed_mbps, 2)
        }

        if self.on_file_received:
            self.on_file_received(str(dest_path), total_bytes, speed_mbps)

        return web.json_response(result)

    async def handle_download(self, request):
        """Zero-copy Linux kernel streaming (os.sendfile) for file downloads."""
        token = request.match_info.get("token")
        file_record = self.active_shared_files.get(token)
        if not file_record or not os.path.exists(file_record["path"]):
            return web.Response(status=404, text="File not found or transfer expired")

        filepath = file_record["path"]
        filename = file_record["name"]

        response = web.FileResponse(
            filepath,
            chunk_size=4 * 1024 * 1024,
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                "Content-Type": "application/octet-stream"
            }
        )
        return response

    async def handle_share_request(self, request):
        try:
            data = await request.json()
            filepath = data.get("filepath")
            if not filepath or not os.path.exists(filepath):
                return web.json_response({"error": "File does not exist"}, status=400)
            res = self.share_file(filepath)
            return web.json_response(res)
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)

    # ---------------------------------------------------------------------------
    # True P2P Direct Push Protocols (Linux-to-Linux Lexi)
    # ---------------------------------------------------------------------------

    async def handle_peer_incoming(self, request):
        """Endpoint called by another Lexi machine proposing an incoming file transfer."""
        try:
            data = await request.json()
            token = data.get("token") or uuid.uuid4().hex[:10]
            transfer_info = {
                "token": token,
                "from_device": data.get("from_device", "Nearby Linux"),
                "from_ip": data.get("from_ip", request.remote),
                "filename": data.get("filename", "transfer.bin"),
                "size_bytes": data.get("size_bytes", 0),
                "download_url": data.get("download_url")
            }
            self.pending_incoming_transfers[token] = transfer_info

            # Notify Dynamic Island to pop up: [ ✓ Accept ] [ ✕ Decline ]
            if self.on_p2p_incoming_request:
                self.on_p2p_incoming_request(transfer_info)

            return web.json_response({"status": "received_proposal", "token": token})
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)

    async def handle_peer_respond(self, request):
        """Accepts or declines a pending P2P file transfer."""
        try:
            data = await request.json()
            token = data.get("token")
            action = data.get("action", "accept")

            info = self.pending_incoming_transfers.pop(token, None)
            if not info:
                return web.json_response({"error": "Transfer not found or expired"}, status=404)

            if action == "accept":
                # Spawn background thread to stream file into ~/Downloads
                threading.Thread(
                    target=self._download_peer_file_sync,
                    args=(info["download_url"], info["filename"], info["size_bytes"], token),
                    daemon=True
                ).start()
                return web.json_response({"status": "accepted", "token": token})
            else:
                return web.json_response({"status": "declined", "token": token})
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)

    def _download_peer_file_sync(self, download_url: str, filename: str, expected_size: int, token: str):
        """Pulls the file from the remote peer directly to ~/Downloads with 4MB chunks."""
        dest_path = get_unique_destination_path(DOWNLOADS_DIR, filename)
        t0 = time.time()
        received = 0
        chunk_size = 4 * 1024 * 1024

        try:
            req = urllib.request.Request(download_url, headers={"User-Agent": "Lexi-P2P/1.0"})
            with urllib.request.urlopen(req, timeout=30) as resp, open(dest_path, "wb") as f:
                while True:
                    chunk = resp.read(chunk_size)
                    if not chunk:
                        break
                    f.write(chunk)
                    received += len(chunk)

                    if self.on_transfer_progress and expected_size > 0:
                        pct = int((received / expected_size) * 100)
                        elapsed = max(time.time() - t0, 0.05)
                        speed = (received / (1024 * 1024)) / elapsed
                        self.on_transfer_progress(token, "download", received, expected_size, speed, pct)

            elapsed = max(time.time() - t0, 0.05)
            final_speed = (received / (1024 * 1024)) / elapsed
            if self.on_file_received:
                self.on_file_received(str(dest_path), received, final_speed)
        except Exception as e:
            print(f"[LexiDrop] P2P download failed for {filename}: {e}")

    def send_p2p_to_peer(self, peer_ip: str, filepath: str) -> dict:
        """Sends a direct P2P transfer proposal to another Lexi machine."""
        share_info = self.share_file(filepath)
        token = share_info["token"]
        name = share_info["name"]
        size = share_info["size"]
        url = share_info["url"]

        payload = json.dumps({
            "from_device": socket.gethostname(),
            "from_ip": self.local_ip,
            "token": token,
            "filename": name,
            "size_bytes": size,
            "download_url": url
        }).encode('utf-8')

        req = urllib.request.Request(
            f"http://{peer_ip}:{self.port}/api/peer/incoming",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        try:
            with urllib.request.urlopen(req, timeout=2.5) as resp:
                result = json.loads(resp.read().decode('utf-8'))
                result["share_info"] = share_info
                return result
        except Exception as e:
            return {"error": f"Failed to reach peer at {peer_ip}: {e}", "share_info": share_info}

    async def handle_status(self, request):
        return web.json_response({
            "status": "online",
            "host": socket.gethostname(),
            "local_ip": self.local_ip,
            "port": self.port,
            "connected_peers": self.get_all_peers(),
            "active_shares": len(self.active_shared_files)
        })

    async def handle_share_target(self, request):
        """Handles native Android Web Share Target POST submissions (files, text, urls)."""
        try:
            reader = await request.multipart()
            saved_files = []
            text_parts = []
            while True:
                part = await reader.next()
                if part is None:
                    break
                if part.filename:
                    filename = part.filename
                    import urllib.parse
                    filename = urllib.parse.unquote(filename)
                    dest_path = get_unique_destination_path(DOWNLOADS_DIR, filename)
                    total_bytes = 0
                    t0 = time.time()
                    with open(dest_path, "wb") as f:
                        while True:
                            chunk = await part.read_chunk(4 * 1024 * 1024)
                            if not chunk:
                                break
                            f.write(chunk)
                            total_bytes += len(chunk)
                    elapsed = max(time.time() - t0, 0.001)
                    speed_mbps = (total_bytes / (1024 * 1024)) / elapsed
                    saved_files.append(str(dest_path))
                    if self.on_file_received:
                        self.on_file_received(str(dest_path), total_bytes, speed_mbps)
                else:
                    val = await part.text()
                    if val.strip():
                        text_parts.append(val.strip())

            if text_parts and not saved_files:
                combined_text = "\n".join(text_parts)
                if self.on_clipboard_received:
                    self.on_clipboard_received(combined_text)

            raise web.HTTPFound("/?shared=success")
        except web.HTTPFound:
            raise
        except Exception as e:
            print(f"[LexiDrop] Share target error: {e}")
            raise web.HTTPFound("/?shared=error")

    async def handle_camera_snap(self, request):
        """Handles Continuity Camera snap from mobile and attaches to Lexi's vision tray."""
        try:
            ct = request.content_type
            snap_filename = f"camera_snap_{int(time.time())}.jpg"
            snap_path = Path("/tmp") / snap_filename

            if "multipart" in ct:
                reader = await request.multipart()
                part = await reader.next()
                if part and part.filename:
                    with open(snap_path, "wb") as f:
                        while True:
                            chunk = await part.read_chunk(1024 * 1024)
                            if not chunk:
                                break
                            f.write(chunk)
            else:
                body = await request.read()
                with open(snap_path, "wb") as f:
                    f.write(body)

            if os.path.exists(snap_path) and os.path.getsize(snap_path) > 0:
                if self.on_camera_snap_received:
                    self.on_camera_snap_received(str(snap_path))
                return web.json_response({
                    "success": True,
                    "path": str(snap_path),
                    "size": os.path.getsize(snap_path)
                })
            return web.json_response({"error": "Empty capture"}, status=400)
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)

    async def handle_clipboard_api(self, request):
        """Receives or returns synced clipboard."""
        if request.method == "POST":
            try:
                data = await request.json()
                text = data.get("text", "")
                if text:
                    self.current_clipboard_text = text
                    if self.on_clipboard_received:
                        self.on_clipboard_received(text)
                    self.broadcast_message({"type": "clipboard_sync", "text": text, "source": "mobile"})
                return web.json_response({"success": True})
            except Exception as e:
                return web.json_response({"error": str(e)}, status=400)
        else:
            return web.json_response({"text": self.current_clipboard_text})

    async def handle_mirror_api(self, request):
        """Starts scrcpy screen mirroring on the laptop display."""
        try:
            stealth = request.query.get("stealth", "false").lower() == "true"
            res = subprocess.run(['adb', 'devices'], capture_output=True, text=True, timeout=3)
            lines = [l.strip() for l in res.stdout.strip().split('\n')[1:] if l.strip()]
            attached = [l.split()[0] for l in lines if 'device' in l and 'offline' not in l and 'unauthorized' not in l]
            if not attached:
                # Try auto-connecting over Wi-Fi
                candidate_ips = []
                if request.remote and request.remote != '127.0.0.1':
                    candidate_ips.append(request.remote)
                for p in self.get_all_peers():
                    pip = p.get('ip')
                    if pip and pip not in candidate_ips and pip != '127.0.0.1':
                        candidate_ips.append(pip)
                for cip in candidate_ips:
                    subprocess.run(['adb', 'connect', f'{cip}:5555'], capture_output=True, timeout=2)
                res = subprocess.run(['adb', 'devices'], capture_output=True, text=True, timeout=2)
                lines = [l.strip() for l in res.stdout.strip().split('\n')[1:] if l.strip()]
                attached = [l.split()[0] for l in lines if 'device' in l and 'offline' not in l and 'unauthorized' not in l]

            if not attached:
                return web.json_response({"error": "No authorized phone connected via USB or Wi-Fi (Run 'adb tcpip 5555' once)"}, status=400)

            device_id = attached[0]
            cmd = [
                'scrcpy',
                '-s', device_id,
                '--window-title=📱 Lexi Phone Mirror',
                '--stay-awake',
                '--mouse-bind=++++:bhsn',
                '--no-mouse-hover',
            ]
            if stealth:
                cmd.append('--turn-screen-off')

            env = os.environ.copy()
            if "DISPLAY" not in env:
                env["DISPLAY"] = ":0"
            if "WAYLAND_DISPLAY" not in env:
                env["WAYLAND_DISPLAY"] = "wayland-0"
            if "XDG_RUNTIME_DIR" not in env:
                env["XDG_RUNTIME_DIR"] = f"/run/user/{os.getuid()}"

            subprocess.Popen(cmd, env=env, start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            threading.Thread(target=auto_unlock_phone, args=(device_id,), daemon=True).start()
            return web.json_response({"success": True, "device": device_id, "stealth": stealth})
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)

    def broadcast_clipboard(self, text: str):
        """Broadcasts laptop clipboard text to all connected mobile clients."""
        self.current_clipboard_text = text
        self.broadcast_message({
            "type": "clipboard_sync",
            "text": text,
            "source": "laptop"
        })

    def broadcast_camera_request(self):
        """Requests connected mobile phone to open camera and take a snap."""
        self.broadcast_message({
            "type": "request_camera_snap"
        })

    def share_file(self, filepath: str) -> dict:
        """Registers a local file for fast sharing and notifies all connected peers via WebSocket."""
        if not os.path.exists(filepath):
            raise FileNotFoundError(f"File not found: {filepath}")

        token = uuid.uuid4().hex[:10]
        name = os.path.basename(filepath)
        size = os.path.getsize(filepath)

        record = {
            "token": token,
            "path": filepath,
            "name": name,
            "size": size,
            "created_at": time.time()
        }
        self.active_shared_files[token] = record

        download_url = f"http://{self.local_ip}:{self.port}/api/download/{token}"
        msg = {
            "type": "incoming_file",
            "file": {
                "token": token,
                "name": name,
                "size": size,
                "url": download_url
            }
        }
        self.broadcast_message(msg)

        return {
            "token": token,
            "name": name,
            "size": size,
            "url": download_url,
            "server_url": self.get_server_url()
        }

    def broadcast_message(self, message: dict):
        """Thread-safe WebSocket broadcast to all connected devices."""
        if not self.loop or not self.connected_websockets:
            return

        async def _send():
            dead = set()
            for ws in list(self.connected_websockets):
                try:
                    await ws.send_json(message)
                except Exception:
                    dead.add(ws)
            for ws in dead:
                self.connected_websockets.discard(ws)

        asyncio.run_coroutine_threadsafe(_send(), self.loop)

    def start_background(self):
        """Starts the Lexi Drop server and peer discovery beacon in background daemon threads."""
        if self.thread and self.thread.is_alive():
            return

        # Start peer discovery beacon
        self.beacon.start()

        def _runner():
            self.loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.loop)

            self.app = web.Application(client_max_size=10 * 1024 * 1024 * 1024)
            self.app.router.add_get("/", self.handle_index)
            self.app.router.add_get("/manifest.json", self.handle_manifest)
            self.app.router.add_get("/sw.js", self.handle_sw)
            self.app.router.add_get("/style.css", self.handle_style)
            self.app.router.add_get("/app.js", self.handle_app_js)
            self.app.router.add_get("/icon.png", self.handle_icon)
            self.app.router.add_get("/icon-192.png", self.handle_icon_192)
            self.app.router.add_get("/icon-512.png", self.handle_icon_512)
            self.app.router.add_get("/lexi-connect.apk", self.handle_apk)
            self.app.router.add_get("/ws", self.handle_ws)
            self.app.router.add_post("/api/upload", self.handle_upload)
            self.app.router.add_get("/api/download/{token}", self.handle_download)
            self.app.router.add_post("/api/share", self.handle_share_request)
            self.app.router.add_post("/api/peer/incoming", self.handle_peer_incoming)
            self.app.router.add_post("/api/peer/respond", self.handle_peer_respond)
            self.app.router.add_get("/api/status", self.handle_status)
            self.app.router.add_post("/api/share-target", self.handle_share_target)
            self.app.router.add_post("/api/camera-snap", self.handle_camera_snap)
            self.app.router.add_get("/api/clipboard", self.handle_clipboard_api)
            self.app.router.add_post("/api/clipboard", self.handle_clipboard_api)
            self.app.router.add_post("/api/mirror", self.handle_mirror_api)

            self.runner = web.AppRunner(self.app)
            self.loop.run_until_complete(self.runner.setup())
            self.site = web.TCPSite(self.runner, self.host, self.port)
            self.loop.run_until_complete(self.site.start())
            print(f"[LexiDrop] Server online at http://{self.local_ip}:{self.port}")
            self.loop.run_forever()

        self.thread = threading.Thread(target=_runner, daemon=True, name="LexiDropServer")
        self.thread.start()


# Global Singleton Instance
_instance = None

def get_lexi_drop_instance() -> LexiDropServer:
    global _instance
    if _instance is None:
        _instance = LexiDropServer()
    return _instance


if __name__ == "__main__":
    server = get_lexi_drop_instance()
    server.start_background()
    print(f"Lexi Drop running at: {server.get_server_url()}")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        server.beacon.stop()
        print("Stopping...")
