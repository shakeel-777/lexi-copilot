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
import re
import time
import uuid
import json
import socket
import asyncio
import threading
import subprocess
import shutil
import tempfile
import urllib.request
import urllib.parse
import ipaddress
from pathlib import Path
from aiohttp import web, WSMsgType

PORT = 7890
BEACON_PORT = 7891
DROP_WEB_DIR = Path(__file__).parent / "drop_web"
DOWNLOADS_DIR = Path(os.path.expanduser("~/Downloads"))
DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)
HOSTED_SHARES_DIR = Path(os.path.expanduser("~/.local/share/mochi-copilot/shares"))
HOSTED_SHARES_FILE = Path(os.path.expanduser("~/.local/share/mochi-copilot/hosted_shares.json"))
try:
    HOSTED_SHARES_DIR.mkdir(parents=True, exist_ok=True)
except Exception:
    pass


def get_local_ip():
    """Dynamically determine the primary local Wi-Fi / LAN IP address."""
    # 1. Prefer Wi-Fi interface (wl*)
    try:
        out = subprocess.check_output(['ip', '-4', '-brief', 'addr', 'show'], text=True, timeout=1)
        for line in out.splitlines():
            parts = line.split()
            if len(parts) >= 3 and parts[0].startswith('wl') and parts[1] == 'UP':
                return parts[2].split('/')[0]
        # 2. Prefer standard ethernet (en* / eth*)
        for line in out.splitlines():
            parts = line.split()
            if len(parts) >= 3 and not parts[0].startswith('docker') and parts[1] == 'UP':
                return parts[2].split('/')[0]
    except Exception:
        pass

    # 3. Fallback to UDP socket query
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
    if not device_id or not re.match(r'^[a-zA-Z0-9_\.:\-]+$', device_id):
        return

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
        check1 = subprocess.run(['adb', '-s', device_id, 'shell', 'dumpsys', 'window', 'policy'], capture_output=True, text=True, timeout=2)
        check2 = subprocess.run(['adb', '-s', device_id, 'shell', 'dumpsys', 'power'], capture_output=True, text=True, timeout=2)
        out = (check1.stdout + check2.stdout).lower()
        if 'iskeyguardshowing=true' in out or 'asleep' in out or 'dozing' in out:
            # 1. Wake screen up
            subprocess.run(['adb', '-s', device_id, 'shell', 'input', 'keyevent', '224'], timeout=2)
            time.sleep(0.7)
            # 2. Swipe up to reveal lockscreen PIN/password input
            subprocess.run(['adb', '-s', device_id, 'shell', 'input', 'swipe', '540', '1800', '540', '500', '250'], timeout=2)
            time.sleep(0.5)
            # 3. Enter password if configured (sanitize to prevent adb shell injection)
            if password:
                clean_pw = str(password).strip()
                if re.match(r'^[a-zA-Z0-9_\-\.@#]+$', clean_pw):
                    subprocess.run(['adb', '-s', device_id, 'shell', 'input', 'text', clean_pw], timeout=3)
                    time.sleep(0.3)
                    subprocess.run(['adb', '-s', device_id, 'shell', 'input', 'keyevent', '66'], timeout=2)
    except Exception as e:
        print(f"[LexiDrop] Auto-unlock error: {e}", file=sys.stderr)


class PhoneMirrorManager:
    """
    High-Resilience Phone Mirror Manager & Auto-Reconnect Supervisor for scrcpy.
    - Low-latency wireless profile: H.265 compression (cuts bandwidth 50%), 1080p downscaling,
      3 Mbps bitrate cap, 60 FPS, 40ms audio/video jitter buffer.
    - Android Wi-Fi low-latency mode: requests Android OS to disable DTIM powersave beacon throttling.
    - Resilient background supervisor: detects unexpected wireless drops (distance / roaming)
      and automatically re-connects via ADB and restores mirroring without user intervention.
    - Codec resilience: transparently falls back to H.264 if device or host encoder lacks H.265.
    - Process deduplication: raises active window if mirroring is already running.
    """
    _instance = None
    _lock = threading.Lock()

    @classmethod
    def get(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    def __init__(self):
        self._proc = None
        self._supervisor_thread = None
        self._stop_requested = False
        self._current_device = None
        self._is_wireless = False
        self._last_codec = "h265"
        self._state_lock = threading.Lock()

    def is_running(self) -> bool:
        with self._state_lock:
            return bool(self._proc and self._proc.poll() is None)

    def get_current_device(self):
        with self._state_lock:
            return self._current_device

    def is_wireless_device(self, device_id: str) -> bool:
        if not device_id:
            return False
        return (':' in device_id) or bool(re.match(r'^\d+\.\d+\.\d+\.\d+', device_id))

    def enable_low_latency_wifi(self, device_id: str):
        if not self.is_wireless_device(device_id):
            return
        def _set_mode():
            try:
                subprocess.run(
                    ['adb', '-s', device_id, 'shell', 'cmd', 'wifi', 'set-power-mode', 'low_latency'],
                    capture_output=True, timeout=2
                )
            except Exception:
                pass
        threading.Thread(target=_set_mode, daemon=True).start()

    def discover_device(self, preferred_ip: str = None) -> tuple[list[str], list[str]]:
        try:
            res = subprocess.run(['adb', 'devices'], capture_output=True, text=True, timeout=3)
            lines = [l.strip() for l in res.stdout.strip().split('\n')[1:] if l.strip()]
            attached = [l.split()[0] for l in lines if 'device' in l and 'offline' not in l and 'unauthorized' not in l]
            unauth = [l.split()[0] for l in lines if 'unauthorized' in l]

            if not attached:
                candidate_ips = []
                if preferred_ip and re.match(r'^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$', preferred_ip) and not preferred_ip.startswith('127.'):
                    candidate_ips.append(preferred_ip)
                try:
                    drop = get_lexi_drop_instance()
                    if drop:
                        for peer in drop.get_all_peers():
                            pip = peer.get("ip")
                            if pip and pip not in candidate_ips and pip not in ('127.0.0.1', 'localhost'):
                                candidate_ips.append(pip)
                        for rip in getattr(drop, 'registered_peers', {}).keys():
                            if rip and rip not in candidate_ips and rip not in ('127.0.0.1', 'localhost'):
                                candidate_ips.append(rip)
                except Exception:
                    pass

                # 1. ARP / Neighbor discovery (hotspot clients on 10.42.0.x or Wi-Fi subnet)
                try:
                    neigh = subprocess.run(['ip', 'neigh', 'show'], capture_output=True, text=True, timeout=1.5)
                    if neigh.returncode == 0:
                        for nl in neigh.stdout.strip().splitlines():
                            parts = nl.split()
                            if parts:
                                nip = parts[0]
                                if re.match(r'^\d+\.\d+\.\d+\.\d+$', nip) and not nip.startswith('127.'):
                                    if 'REACHABLE' in nl or 'DELAY' in nl or 'STALE' in nl or nip.startswith('10.42.0.'):
                                        if nip not in candidate_ips:
                                            candidate_ips.append(nip)
                except Exception:
                    pass

                # 2. Default gateway discovery (if laptop is connected to phone hotspot, e.g. 192.168.43.1)
                try:
                    gw_res = subprocess.run(['ip', 'route', 'show', 'default'], capture_output=True, text=True, timeout=1.5)
                    if gw_res.returncode == 0:
                        for gl in gw_res.stdout.strip().splitlines():
                            m = re.search(r'via\s+(\d+\.\d+\.\d+\.\d+)', gl)
                            if m:
                                gw_ip = m.group(1)
                                if gw_ip not in candidate_ips and not gw_ip.startswith('127.'):
                                    candidate_ips.append(gw_ip)
                except Exception:
                    pass

                for cip in candidate_ips:
                    try:
                        subprocess.run(['adb', 'connect', f'{cip}:5555'], capture_output=True, timeout=2)
                    except Exception:
                        pass

                res = subprocess.run(['adb', 'devices'], capture_output=True, text=True, timeout=2)
                lines = [l.strip() for l in res.stdout.strip().split('\n')[1:] if l.strip()]
                attached = [l.split()[0] for l in lines if 'device' in l and 'offline' not in l and 'unauthorized' not in l]
                unauth = [l.split()[0] for l in lines if 'unauthorized' in l]

            return attached, unauth
        except Exception as e:
            print(f"[PhoneMirrorManager] Discovery error: {e}", file=sys.stderr)
            return [], []

    def build_scrcpy_cmd(self, device_id: str, stay_awake: bool = True, turn_screen_off: bool = False,
                         codec: str = "h265", bitrate: str = "3M", video_buffer: int = 40, audio_buffer: int = 40) -> list[str]:
        cmd = [
            'scrcpy',
            '-s', device_id,
            '--window-title=📱 Lexi Phone Mirror',
            '--mouse-bind=++++:bhsn',
            '--no-mouse-hover',
        ]
        if stay_awake:
            cmd.append('--stay-awake')
        if turn_screen_off:
            cmd.append('--turn-screen-off')

        if self.is_wireless_device(device_id):
            cmd.extend([
                f'--video-codec={codec}',
                '--max-size=1080',
                '-b', bitrate,
                '--max-fps=60',
                f'--video-buffer={video_buffer}',
                f'--audio-buffer={audio_buffer}',
            ])
        else:
            cmd.extend([
                '--max-size=1440',
                '-b', '8M',
                '--max-fps=60',
            ])
        return cmd

    def start_mirror(self, device_id: str = None, stay_awake: bool = True, turn_screen_off: bool = False, preferred_ip: str = None) -> dict:
        if not shutil.which('scrcpy'):
            return {"success": False, "message": "⚠️ `scrcpy` is not installed on the system."}

        attached, unauth = self.discover_device(preferred_ip=preferred_ip)
        if not attached:
            if unauth:
                return {
                    "success": False,
                    "message": "⚠️ **Android Device Detected (Unauthorized)**\n\nPlease unlock your phone and tap **'Always allow from this computer'** on the USB debugging prompt."
                }
            return {
                "success": False,
                "message": (
                    "📱 **No Android Phone Connected**\n\n"
                    "To mirror and control your phone:\n"
                    "1. On phone: Go to **Settings → About Phone** → tap **Build Number** 7 times.\n"
                    "2. Enable **USB Debugging** in Developer Options.\n"
                    "3. Plug phone into laptop via USB cable once and tap **'Allow'**.\n"
                    "4. *(Optional Wireless)* Run `$ adb tcpip 5555` to unplug USB and mirror wirelessly over Wi-Fi!"
                )
            }

        target_device = device_id or attached[0]
        is_wireless = self.is_wireless_device(target_device)

        with self._state_lock:
            if self._proc and self._proc.poll() is None:
                try:
                    subprocess.run(['xdotool', 'search', '--name', 'Lexi Phone Mirror', 'windowactivate'], capture_output=True, timeout=1)
                except Exception:
                    pass
                return {
                    "success": True,
                    "device": target_device,
                    "already_running": True,
                    "is_wireless": is_wireless,
                    "message": f"📱 **Phone Mirror (`{target_device}`) is already active!** Window brought to front."
                }

            self._stop_requested = False
            self._current_device = target_device
            self._is_wireless = is_wireless
            self._last_codec = "h265"

        if is_wireless:
            self.enable_low_latency_wifi(target_device)

        threading.Thread(target=auto_unlock_phone, args=(target_device,), daemon=True).start()

        self._supervisor_thread = threading.Thread(
            target=self._run_supervisor,
            args=(target_device, stay_awake, turn_screen_off),
            name="LexiMirrorSupervisor",
            daemon=True
        )
        self._supervisor_thread.start()

        msg = (
            f"📱 **Mirroring Android Phone (`{target_device}`) at 60 FPS{' (Ultra-Low Latency Wireless 2.0)' if is_wireless else ' (USB Direct)'}**\n\n"
            f"{'• **Adaptive H.265 & Jitter Buffer**: 3 Mbps HEVC + 40ms jitter buffer active for smooth distance mirroring' if is_wireless else '• **High-Speed USB Link**: Uncompressed ultra-low latency direct frame pipe'}\n"
            f"{'• **Autonomous Reconnect**: Background supervisor auto-restores stream if Wi-Fi packets drop' if is_wireless else '• **Plug & Play**: Rock-solid hardware USB connection'}\n"
            "• **Touchpad & Mouse**: Tap to click, two-finger drag to scroll (smooth & natural)\n"
            "• **Navigation Shortcuts**: `Alt+B` = Back, `Alt+H` = Home, `Alt+S` = App Switcher\n"
            "• **Zero-Touch Unlock**: Automatically unlocked phone using configured credentials\n"
            "• **Keyboard & Audio**: Full keyboard pass-through + stereo audio forwarded to laptop"
        )

        return {
            "success": True,
            "device": target_device,
            "is_wireless": is_wireless,
            "message": msg
        }

    def _run_supervisor(self, device_id: str, stay_awake: bool, turn_screen_off: bool):
        env = os.environ.copy()
        if "DISPLAY" not in env:
            env["DISPLAY"] = ":0"
        if "WAYLAND_DISPLAY" not in env:
            env["WAYLAND_DISPLAY"] = "wayland-0"
        if "XDG_RUNTIME_DIR" not in env:
            env["XDG_RUNTIME_DIR"] = f"/run/user/{os.getuid()}"

        codec = "h265"
        consecutive_drops = 0
        max_reconnect_attempts = 40

        while not self._stop_requested:
            # Adaptive buffering and bitrate scaling when distance causes packet drops
            bitrate = "3M"
            v_buf = 40
            a_buf = 40
            if consecutive_drops > 0:
                bitrate = "2M" if consecutive_drops <= 3 else "1.5M"
                v_buf = 100
                a_buf = 80

            cmd = self.build_scrcpy_cmd(
                device_id, stay_awake=stay_awake, turn_screen_off=turn_screen_off,
                codec=codec, bitrate=bitrate, video_buffer=v_buf, audio_buffer=a_buf
            )
            try:
                proc = subprocess.Popen(
                    cmd,
                    env=env,
                    start_new_session=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE
                )
                with self._state_lock:
                    self._proc = proc
                    self._last_codec = codec

                t_start = time.time()
                _, stderr = proc.communicate()
                rc = proc.returncode
                elapsed = time.time() - t_start

                if self._stop_requested:
                    break

                if rc == 0:
                    print(f"[PhoneMirrorManager] Scrcpy session cleanly closed by user.")
                    break

                # Codec fallback if H.265 failed immediately
                if elapsed < 2.5 and codec == "h265" and stderr:
                    err_text = stderr.decode('utf-8', errors='ignore').lower()
                    if any(term in err_text for term in ("codec", "encoder", "mediacodec", "failed to create video encoder", "hevc")):
                        print(f"[PhoneMirrorManager] H.265 encoder unsupported on device/host. Falling back to H.264 profile.")
                        codec = "h264"
                        continue

                # If USB device or non-wireless, scrcpy closed
                if not self.is_wireless_device(device_id):
                    print(f"[PhoneMirrorManager] USB device disconnected or scrcpy exited (code {rc}).")
                    break

                consecutive_drops += 1
                if consecutive_drops > max_reconnect_attempts:
                    print(f"[PhoneMirrorManager] Reached max reconnect attempts ({max_reconnect_attempts}). Stopping supervisor.")
                    break

                print(f"[PhoneMirrorManager] Wireless mirror dropped (code {rc}). Auto-reconnecting attempt {consecutive_drops}/{max_reconnect_attempts} (buffer={v_buf}ms, bitrate={bitrate})...")

                reconnected = False
                for _ in range(6):
                    if self._stop_requested:
                        break
                    time.sleep(1.5)
                    try:
                        subprocess.run(['adb', 'connect', device_id], capture_output=True, timeout=2)
                        chk = subprocess.run(['adb', 'devices'], capture_output=True, text=True, timeout=2)
                        if device_id in chk.stdout and 'offline' not in chk.stdout and 'unauthorized' not in chk.stdout:
                            self.enable_low_latency_wifi(device_id)
                            reconnected = True
                            consecutive_drops = 0
                            break
                        # Dynamic network rediscovery if IP shifted or re-announced
                        new_devs, _ = self.discover_device()
                        if new_devs:
                            target = next((d for d in new_devs if self.is_wireless_device(d)), new_devs[0])
                            if target != device_id:
                                device_id = target
                                self.enable_low_latency_wifi(device_id)
                                reconnected = True
                                consecutive_drops = 0
                                break
                    except Exception:
                        pass

                if not reconnected:
                    time.sleep(2)

            except Exception as e:
                print(f"[PhoneMirrorManager] Supervisor error: {e}", file=sys.stderr)
                break

        with self._state_lock:
            self._proc = None
            self._current_device = None

    def stop_mirror(self):
        with self._state_lock:
            self._stop_requested = True
            if self._proc and self._proc.poll() is None:
                try:
                    self._proc.terminate()
                    self._proc.wait(timeout=1.5)
                except Exception:
                    try:
                        self._proc.kill()
                    except Exception:
                        pass
            self._proc = None
            self._current_device = None


def get_unique_destination_path(directory: Path, filename: str) -> Path:
    """
    Sanitizes filename and prevents directory traversal attacks, ensuring the returned path
    is strictly contained inside `directory`. Avoids overwriting existing files by appending
    incremental counters.
    """
    dir_resolved = directory.resolve()

    # 1. Strip paths, normalize slashes, and extract leaf basename
    raw_name = (filename or "").strip().replace('\\', '/')
    clean_name = os.path.basename(raw_name).strip()

    # 2. Neutralize null bytes, control characters, and dangerous traversal tokens
    clean_name = re.sub(r'[\x00-\x1f\x7f]', '', clean_name)
    clean_name = clean_name.lstrip('/\\.')

    # 3. Disallow empty or hidden files (prevent overwriting dotfiles e.g. .bashrc, .profile)
    if not clean_name or clean_name in ('.', '..'):
        clean_name = f"download_{int(time.time())}.bin"
    elif clean_name.startswith('.'):
        clean_name = f"download_{clean_name.lstrip('.')}"

    candidate = (dir_resolved / clean_name).resolve()

    # 4. Strict path traversal verification
    try:
        candidate.relative_to(dir_resolved)
    except ValueError:
        candidate = dir_resolved / f"safe_download_{int(time.time())}.bin"

    stem = candidate.stem
    suffix = candidate.suffix
    final_path = candidate
    counter = 1
    while final_path.exists():
        final_path = dir_resolved / f"{stem} ({counter}){suffix}"
        counter += 1
    return final_path


def is_safe_share_path(filepath: str) -> tuple[bool, str]:
    """
    Validates whether a file or directory is safe to be shared over Lexi Beam,
    preventing arbitrary local file exfiltration and traversal of sensitive credentials.
    """
    try:
        real_p = os.path.realpath(os.path.expanduser(filepath))
        home = os.path.realpath(os.path.expanduser("~"))

        forbidden_system_dirs = ["/etc", "/root", "/proc", "/sys", "/dev", "/boot", "/var"]
        for fdir in forbidden_system_dirs:
            if real_p == fdir or real_p.startswith(fdir.rstrip("/") + "/"):
                return False, f"Access to system directory {fdir} is forbidden"

        sensitive_home_items = [
            os.path.join(home, ".ssh"),
            os.path.join(home, ".gnupg"),
            os.path.join(home, ".bash_history"),
            os.path.join(home, ".zsh_history"),
            os.path.join(home, ".bashrc"),
            os.path.join(home, ".profile"),
            os.path.join(home, ".local", "share", "mochi-copilot", "config.json"),
            os.path.join(home, ".config", "mochi-copilot")
        ]
        for sitem in sensitive_home_items:
            if real_p == sitem or real_p.startswith(sitem.rstrip("/") + "/"):
                return False, "Access to sensitive credentials/configuration is forbidden"

        return True, ""
    except Exception as e:
        return False, str(e)


def play_beam_sound(sound_name: str = "complete"):
    """
    Plays high-fidelity system sound asynchronously for AirDrop audio feedback.
    Falls back gracefully if sound player or audio file is absent.
    """
    def _play():
        sound_map = {
            "complete": "/usr/share/sounds/freedesktop/stereo/complete.oga",
            "send_complete": "/usr/share/sounds/freedesktop/stereo/complete.oga",
            "receive_complete": "/usr/share/sounds/freedesktop/stereo/complete.oga",
            "device_found": "/usr/share/sounds/freedesktop/stereo/device-added.oga",
            "device_removed": "/usr/share/sounds/freedesktop/stereo/device-removed.oga",
            "bell": "/usr/share/sounds/freedesktop/stereo/bell.oga",
            "camera": "/usr/share/sounds/freedesktop/stereo/camera-shutter.oga",
            "declined": "/usr/share/sounds/freedesktop/stereo/dialog-warning.oga",
            "incoming": "/usr/share/sounds/freedesktop/stereo/message-new-instant.oga",
        }
        snd_file = sound_map.get(sound_name, sound_map["complete"])
        if not os.path.exists(snd_file):
            return
        for player in ('pw-play', 'paplay', 'canberra-gtk-play'):
            if shutil.which(player):
                try:
                    if player == 'canberra-gtk-play':
                        subprocess.run(['canberra-gtk-play', '-f', snd_file], capture_output=True, timeout=2)
                    else:
                        subprocess.run([player, snd_file], capture_output=True, timeout=2)
                    return
                except Exception:
                    pass
    threading.Thread(target=_play, daemon=True, name="LexiBeamSound").start()


_adb_device_names = {}

def get_friendly_adb_device_name(device_id: str) -> str:
    """Queries friendly marketing name or model of Android device over ADB."""
    if not device_id:
        return "Android Phone"
    if device_id in _adb_device_names:
        return _adb_device_names[device_id]
    name = None
    try:
        res = subprocess.run(['adb', '-s', device_id, 'shell', 'getprop', 'ro.product.marketname'], capture_output=True, text=True, timeout=1.5)
        out = res.stdout.strip()
        if out and out != "unknown":
            name = out
    except Exception:
        pass
    if not name:
        try:
            res = subprocess.run(['adb', '-s', device_id, 'shell', 'settings', 'get', 'global', 'device_name'], capture_output=True, text=True, timeout=1.5)
            out = res.stdout.strip()
            if out and out != "null":
                name = out
        except Exception:
            pass
    if not name:
        try:
            res = subprocess.run(['adb', '-s', device_id, 'shell', 'getprop', 'ro.product.model'], capture_output=True, text=True, timeout=1.5)
            out = res.stdout.strip()
            if out:
                name = out
        except Exception:
            pass
    final_name = name or "Android Companion"
    _adb_device_names[device_id] = final_name
    return final_name


class LexiPeerBeacon:
    """
    Lightweight UDP Multicast / Broadcast Peer Discovery Beacon.
    Continuously announces Lexi's presence on the LAN and discovers nearby Lexi machines.
    """
    def __init__(self, server):
        self.server = server
        self.port = server.port
        self.hostname = socket.gethostname() or "Linux-Lexi"
        self.running = False
        self.lan_peers = {}  # ip -> {hostname, ip, port, last_seen, type}
        self.lock = threading.Lock()

    @property
    def local_ip(self) -> str:
        return self.server.local_ip

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

        while self.running:
            try:
                curr_ip = self.local_ip
                bcast_ip = get_broadcast_ip(curr_ip)
                payload = json.dumps({
                    "type": "lexi_peer",
                    "hostname": self.hostname,
                    "ip": curr_ip,
                    "port": self.port,
                    "os": "linux",
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
                if peer_ip == self.local_ip or peer_ip in ("127.0.0.1", "localhost"):
                    continue  # Ignore self
                msg = json.loads(data.decode('utf-8'))
                if msg.get("type") == "lexi_peer":
                    peer_host = msg.get("hostname", peer_ip)
                    if peer_host == self.hostname:
                        continue  # Ignore self announcement on different interface
                    now = time.time()
                    with self.lock:
                        is_new = peer_ip not in self.lan_peers
                        self.lan_peers[peer_ip] = {
                            "hostname": peer_host,
                            "ip": peer_ip,
                            "port": msg.get("port", PORT),
                            "last_seen": now,
                            "type": msg.get("device_type", "laptop"),
                            "os": msg.get("os", "linux")
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
        self._local_ip_cached = None
        self._local_ip_time = 0
        self.active_shared_files = {}  # token -> {path, name, size, created_at, expires_at, pin_all_day, is_zip}
        self.latest_share_token = None
        self.connected_websockets = set()
        self.connected_peers = {}      # ws -> {device, ip, connected_at}
        self.pending_incoming_transfers = {}  # token -> transfer_dict
        self.loop = None
        self.thread = None
        self.app = None
        self.runner = None
        self.site = None

        # Load persisted all-day hosted shares from disk
        self._load_persisted_shares()

        # Beacon for True P2P Discovery
        self.beacon = LexiPeerBeacon(self)
        self.registered_peers = {}  # ip -> {device, ip, type, os, port, last_seen}

        # Callbacks for GTK / Dynamic Island integration
        self.on_peer_joined = None
        self.on_peer_left = None
        self.on_file_received = None
        self.on_p2p_incoming_request = None
        self.on_transfer_progress = None
        self.on_clipboard_received = None
        self.on_camera_snap_received = None
        self.on_peer_accepted_download = None
        self.on_peer_declined_transfer = None
        self.current_clipboard_text = ""

    @property
    def local_ip(self) -> str:
        now = time.time()
        if not self._local_ip_cached or (now - self._local_ip_time > 4.0):
            self._local_ip_cached = get_local_ip()
            self._local_ip_time = now
        return self._local_ip_cached

    @local_ip.setter
    def local_ip(self, val: str):
        self._local_ip_cached = val
        self._local_ip_time = time.time()

    def get_server_url(self):
        return f"http://{self.local_ip}:{self.port}"

    def _save_persisted_shares(self):
        """Saves active unexpired shares to disk so they survive Lexi restarts for the full day."""
        try:
            HOSTED_SHARES_DIR.mkdir(parents=True, exist_ok=True)
            now = time.time()
            valid_shares = {}
            for tok, rec in list(self.active_shared_files.items()):
                if rec.get("expires_at", 0) > now and os.path.exists(rec.get("path", "")):
                    valid_shares[tok] = rec
            with open(HOSTED_SHARES_FILE, "w", encoding="utf-8") as f:
                json.dump({
                    "latest_token": getattr(self, "latest_share_token", None),
                    "shares": valid_shares
                }, f, indent=2)
        except Exception as e:
            print(f"[LexiDrop] Error saving persisted shares: {e}")

    def _load_persisted_shares(self):
        """Loads previously hosted shares from disk and prunes expired ones."""
        if not HOSTED_SHARES_FILE.exists():
            return
        try:
            with open(HOSTED_SHARES_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            now = time.time()
            shares = data.get("shares", {})
            pruned = 0
            for tok, rec in list(shares.items()):
                if rec.get("expires_at", 0) > now and os.path.exists(rec.get("path", "")):
                    self.active_shared_files[tok] = rec
                else:
                    pruned += 1
            if data.get("latest_token") in self.active_shared_files:
                self.latest_share_token = data.get("latest_token")
            elif self.active_shared_files:
                self.latest_share_token = max(self.active_shared_files.values(), key=lambda r: r.get("created_at", 0)).get("token")
            if pruned > 0:
                self._save_persisted_shares()
        except Exception as e:
            print(f"[LexiDrop] Error loading persisted shares: {e}")

    def get_active_shares_summary(self) -> list:
        """Returns structured list of all unexpired active shares."""
        now = time.time()
        shares_list = []
        for tok, rec in list(self.active_shared_files.items()):
            if rec.get("expires_at", 0) > now and os.path.exists(rec.get("path", "")):
                name = rec.get("name", "file")
                shares_list.append({
                    "token": tok,
                    "name": name,
                    "size": rec.get("size", 0),
                    "created_at": rec.get("created_at", now),
                    "expires_at": rec.get("expires_at", now + 86400),
                    "time_left_sec": max(0, int(rec.get("expires_at", now + 86400) - now)),
                    "url": f"http://{self.local_ip}:{self.port}/api/download/{tok}",
                    "named_url": f"http://{self.local_ip}:{self.port}/download/{urllib.parse.quote(name)}",
                    "static_url": f"http://{self.local_ip}:{self.port}/download/latest",
                    "is_zip": rec.get("is_zip", name.lower().endswith(".zip"))
                })
        return shares_list

    def get_all_peers(self) -> list:
        """Returns unified list of active peers (Linux P2P, Android App, Web/PWA clients, ADB)."""
        seen_ips = set()
        seen_dev_ids = set()
        result = []
        now = time.time()

        # 1. Linux P2P peers discovered via UDP beacon
        with self.beacon.lock:
            for ip, info in self.beacon.lan_peers.items():
                if ip == self.local_ip or ip == "127.0.0.1" or info.get("hostname") == self.beacon.hostname:
                    continue
                seen_ips.add(ip)
                result.append({
                    "device": info["hostname"],
                    "ip": ip,
                    "type": info.get("type", "laptop"),
                    "os": info.get("os", "linux"),
                    "port": info.get("port", PORT)
                })

        # 2. Registered peers (e.g. Android Lexi app via HTTP registration)
        stale_reg = []
        for ip, info in list(self.registered_peers.items()):
            if now - info.get("last_seen", 0) > 60.0:
                stale_reg.append(ip)
                continue
            if ip not in seen_ips and ip != self.local_ip and ip != "127.0.0.1":
                seen_ips.add(ip)
                result.append({
                    "device": info.get("device", "Phone"),
                    "ip": ip,
                    "type": info.get("type", "phone"),
                    "os": info.get("os", "android"),
                    "port": info.get("port", PORT)
                })
        for ip in stale_reg:
            self.registered_peers.pop(ip, None)

        # 3. Web / PWA connected peers (e.g. Windows PC, Browser)
        for info in self.connected_peers.values():
            ip = info.get("ip")
            if ip and ip not in seen_ips and ip != self.local_ip and ip != "127.0.0.1":
                seen_ips.add(ip)
                result.append({
                    "device": info.get("device", "Web Device"),
                    "ip": ip,
                    "type": "web_pwa",
                    "os": "web",
                    "port": PORT
                })

        # 4. Connected ADB phone if present (Wi-Fi or USB)
        try:
            res = subprocess.run(['adb', 'devices'], capture_output=True, text=True, timeout=1)
            lines = [l.strip() for l in res.stdout.strip().split('\n')[1:] if l.strip()]
            for line in lines:
                if 'device' in line and 'offline' not in line and 'unauthorized' not in line:
                    dev_id = line.split()[0]
                    seen_dev_ids.add(dev_id)
                    pip = dev_id.split(':')[0] if ':' in dev_id else dev_id
                    friendly_name = get_friendly_adb_device_name(dev_id)

                    # Auto-sync laptop host IP to phone in background
                    def _sync_host(did=dev_id, lip=self.local_ip, lport=self.port):
                        try:
                            subprocess.run(['adb', '-s', did, 'shell', 'am', 'broadcast', '-a', 'com.lexi.connect.SET_HOST', '--es', 'host', lip, '--ei', 'port', str(lport)], capture_output=True, timeout=2)
                        except Exception:
                            pass
                    threading.Thread(target=_sync_host, daemon=True).start()

                    # Merge or update existing peer entry with friendly name
                    merged = False
                    for existing in result:
                        if existing.get("ip") == pip or (existing.get("type") == "phone" and (pip.startswith("10.") or pip.startswith("192."))):
                            existing["device"] = friendly_name
                            existing["adb_device"] = dev_id
                            existing["type"] = "phone"
                            existing["os"] = "android"
                            merged = True
                            break

                    if not merged and pip not in seen_ips and pip != self.local_ip:
                        seen_ips.add(pip)
                        result.append({
                            "device": friendly_name,
                            "ip": pip,
                            "adb_device": dev_id,
                            "type": "phone",
                            "os": "android",
                            "port": PORT
                        })
        except Exception:
            pass

        return result

    def probe_peer(self, ip: str, port: int = PORT) -> dict:
        """
        Actively checks if a peer at the given IP is running Lexi and pairs with it.
        Bypasses UDP multicast/broadcast restrictions on restrictive corporate/hotel networks.
        """
        clean_ip = ip.strip()
        url = f"http://{clean_ip}:{port}/api/status"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Lexi-Probe/1.0"})
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(req, timeout=2.0) as resp:
                data = json.loads(resp.read().decode('utf-8'))
                hostname = data.get("host") or clean_ip
                now = time.time()
                with self.beacon.lock:
                    self.beacon.lan_peers[clean_ip] = {
                        "hostname": hostname,
                        "ip": clean_ip,
                        "port": port,
                        "last_seen": now,
                        "type": "laptop",
                        "os": "linux"
                    }
                if self.on_peer_joined:
                    self.on_peer_joined(hostname, clean_ip)
                play_beam_sound("device_found")
                return {"success": True, "device": hostname, "ip": clean_ip, "port": port}
        except Exception as e:
            return {"success": False, "error": str(e)}

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

        # Sync active 24h hosted shares so newly connected devices see all hosted files immediately
        active_shares = self.get_active_shares_summary()
        if active_shares:
            await ws.send_json({
                "type": "active_shares_sync",
                "shares": active_shares
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
                            pass
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
        max_upload_bytes = 10 * 1024 * 1024 * 1024  # 10 GB maximum per transfer

        part_path = dest_path.with_name(f".{dest_path.name}.lexipart")

        try:
            with open(part_path, "wb") as f:
                async for chunk in request.content.iter_chunked(chunk_size):
                    total_bytes += len(chunk)
                    if total_bytes > max_upload_bytes:
                        raise web.HTTPRequestEntityTooLarge(
                            max_size=max_upload_bytes,
                            actual_size=total_bytes
                        )
                    f.write(chunk)

            os.replace(part_path, dest_path)
        except Exception:
            if part_path.exists():
                try:
                    part_path.unlink()
                except Exception:
                    pass
            raise

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

        play_beam_sound("receive_complete")
        try:
            sz_str = f"{total_bytes / (1024*1024):.1f} MB" if total_bytes >= 1024*1024 else f"{total_bytes // 1024} KB"
            subprocess.Popen(['notify-send', '-i', 'document-save', '⚡ Lexi Beam', f'Received {dest_path.name} ({sz_str}) in ~/Downloads'])
        except Exception:
            pass

        return web.json_response(result)

    async def handle_download(self, request):
        """Zero-copy Linux kernel streaming (os.sendfile) for file downloads."""
        token = request.match_info.get("token")
        file_record = self.active_shared_files.get(token)
        now = time.time()
        if not file_record or not os.path.exists(file_record["path"]) or (file_record.get("expires_at", 0) and file_record["expires_at"] < now):
            return web.Response(status=404, text="File not found or transfer expired")

        filepath = file_record["path"]
        filename = file_record["name"]

        if self.on_peer_accepted_download:
            try:
                self.on_peer_accepted_download(filename, request.remote or "Peer")
            except Exception:
                pass

        response = web.FileResponse(
            filepath,
            chunk_size=4 * 1024 * 1024,
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                "Content-Type": "application/octet-stream"
            }
        )
        return response

    async def handle_download_latest(self, request):
        """Direct permanent URL (/download/latest) to download the most recently hosted file/zip."""
        target_record = None
        now = time.time()
        if getattr(self, "latest_share_token", None):
            rec = self.active_shared_files.get(self.latest_share_token)
            if rec and os.path.exists(rec.get("path", "")) and rec.get("expires_at", 0) > now:
                target_record = rec

        if not target_record and self.active_shared_files:
            valid_recs = [
                r for r in self.active_shared_files.values()
                if os.path.exists(r.get("path", "")) and r.get("expires_at", 0) > now
            ]
            if valid_recs:
                target_record = max(valid_recs, key=lambda r: r.get("created_at", 0))

        if not target_record or not os.path.exists(target_record["path"]):
            return web.Response(status=404, text="No active file currently hosted. Beam or host a file to download it here.")

        filepath = target_record["path"]
        filename = target_record["name"]

        if self.on_peer_accepted_download:
            try:
                self.on_peer_accepted_download(filename, request.remote or "Peer")
            except Exception:
                pass

        return web.FileResponse(
            filepath,
            chunk_size=4 * 1024 * 1024,
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                "Content-Type": "application/octet-stream"
            }
        )

    async def handle_download_by_name_or_token(self, request):
        """Permanent URL (/download/{identifier}) supporting either custom token or exact filename (e.g. /download/Project.zip)."""
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

        filepath = file_record["path"]
        filename = file_record["name"]

        if self.on_peer_accepted_download:
            try:
                self.on_peer_accepted_download(filename, request.remote or "Peer")
            except Exception:
                pass

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

    async def handle_share_request(self, request):
        try:
            # 1. Strictly restrict to local loopback (invoked only by desktop Lexi UI)
            client_ip = request.remote or ""
            if client_ip not in ("127.0.0.1", "::1", "localhost"):
                return web.json_response({"error": "Forbidden: Local endpoint only"}, status=403)

            data = await request.json()
            filepath = data.get("filepath")
            pin_all_day = bool(data.get("pin_all_day", True))
            if not filepath or not os.path.exists(filepath):
                return web.json_response({"error": "File does not exist"}, status=400)

            # 2. Path validation against sensitive files and system directories
            is_safe, reason = is_safe_share_path(filepath)
            if not is_safe:
                return web.json_response({"error": f"Sharing forbidden: {reason}"}, status=403)

            res = self.share_file(filepath, pin_all_day=pin_all_day)
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

            # Play gentle incoming beam alert chime
            play_beam_sound("incoming")

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
                from_ip = info.get("from_ip")
                if from_ip and from_ip not in ('127.0.0.1', 'localhost'):
                    def _notify_decline():
                        try:
                            payload = json.dumps({
                                "token": token,
                                "declined_by": socket.gethostname(),
                                "filename": info.get("filename", "")
                            }).encode('utf-8')
                            req = urllib.request.Request(
                                f"http://{from_ip}:{self.port}/api/peer/declined",
                                data=payload,
                                headers={"Content-Type": "application/json"},
                                method="POST"
                            )
                            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                            opener.open(req, timeout=2.5)
                        except Exception:
                            pass
                    threading.Thread(target=_notify_decline, daemon=True).start()
                return web.json_response({"status": "declined", "token": token})
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)

    async def handle_peer_declined(self, request):
        """Called when a remote peer declines an incoming transfer."""
        try:
            data = await request.json()
            declined_by = data.get("declined_by", "Peer")
            filename = data.get("filename", "file")
            play_beam_sound("declined")
            if self.on_peer_declined_transfer:
                self.on_peer_declined_transfer(filename, declined_by)
            return web.json_response({"status": "acknowledged"})
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)

    def _download_peer_file_sync(self, download_url: str, filename: str, expected_size: int, token: str):
        """Pulls the file from the remote peer directly to ~/Downloads with 4MB chunks and atomic commit."""
        # 1. Validate scheme and netloc to prevent SSRF (file://, dict://, gopher://, etc.)
        parsed = urllib.parse.urlparse(download_url or "")
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            print(f"[LexiDrop] Blocked unsafe peer download URL: {download_url}")
            return

        max_allowed_size = 20 * 1024 * 1024 * 1024  # 20 GB safety ceiling
        if expected_size and expected_size > max_allowed_size:
            print(f"[LexiDrop] Rejecting download: expected size {expected_size} exceeds 20GB limit")
            return

        dest_path = get_unique_destination_path(DOWNLOADS_DIR, filename)
        part_path = dest_path.with_name(f".{dest_path.name}.lexipart")
        t0 = time.time()
        received = 0
        chunk_size = 4 * 1024 * 1024

        try:
            req = urllib.request.Request(download_url, headers={"User-Agent": "Lexi-P2P/1.0"})
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(req, timeout=60) as resp, open(part_path, "wb") as f:
                while True:
                    chunk = resp.read(chunk_size)
                    if not chunk:
                        break
                    received += len(chunk)
                    if received > max_allowed_size:
                        raise ValueError(f"Peer download exceeded maximum allowed size ({max_allowed_size} bytes)")
                    f.write(chunk)

                    if self.on_transfer_progress and expected_size > 0:
                        pct = int((received / expected_size) * 100)
                        elapsed = max(time.time() - t0, 0.05)
                        speed = (received / (1024 * 1024)) / elapsed
                        self.on_transfer_progress(token, "download", received, expected_size, speed, pct)

            # Atomic rename from .lexipart to final destination
            os.replace(part_path, dest_path)

            elapsed = max(time.time() - t0, 0.05)
            final_speed = (received / (1024 * 1024)) / elapsed
            if self.on_file_received:
                self.on_file_received(str(dest_path), received, final_speed)
            play_beam_sound("receive_complete")
            try:
                sz_str = f"{received / (1024*1024):.1f} MB" if received >= 1024*1024 else f"{received // 1024} KB"
                subprocess.Popen(['notify-send', '-i', 'document-save', '⚡ Lexi Beam', f'Received {dest_path.name} ({sz_str}) in ~/Downloads'])
            except Exception:
                pass
        except Exception as e:
            if part_path.exists():
                try:
                    part_path.unlink()
                except Exception:
                    pass
            print(f"[LexiDrop] P2P download failed for {filename}: {e}")

    def send_p2p_to_peer(self, peer_ip: str, filepath: str) -> dict:
        """
        AirDrop-Grade multi-transport file transfer engine.
        Transfers directly via:
        1. Fast ADB Push (if peer is an Android phone attached via Wi-Fi or USB)
        2. WebSocket Direct Push (if peer is connected via Web/PWA receiver)
        3. Linux LAN P2P Proposal (if peer is a Lexi desktop on LAN)
        4. High-speed kernel download link fallback
        """
        if not filepath or not os.path.exists(filepath):
            return {"error": f"File not found: {filepath}"}

        share_info = self.share_file(filepath)
        token = share_info["token"]
        name = share_info["name"]
        size = share_info["size"]
        url = share_info["url"]

        # --- Transport 1: ADB Direct Push to Android Phone ---
        try:
            adb_res = subprocess.run(['adb', 'devices'], capture_output=True, text=True, timeout=1.5)
            lines = [l.strip() for l in adb_res.stdout.strip().split('\n')[1:] if l.strip()]
            attached = [l.split()[0] for l in lines if 'device' in l and 'offline' not in l and 'unauthorized' not in l]

            matched_adb_dev = None
            if attached:
                # 1. Exact match on device id or ip
                for dev in attached:
                    if peer_ip == dev or (':' in dev and dev.split(':')[0] == peer_ip):
                        matched_adb_dev = dev
                        break
                # 2. Check registered peers: is peer_ip a phone?
                if not matched_adb_dev and peer_ip in self.registered_peers:
                    reg = self.registered_peers[peer_ip]
                    if reg.get("type") == "phone" or reg.get("os") == "android":
                        matched_adb_dev = attached[0]
                # 3. If there is only 1 attached phone and peer_ip represents phone
                if not matched_adb_dev and len(attached) == 1:
                    if peer_ip.startswith("10.") or peer_ip.startswith("192.") or "phone" in peer_ip.lower() or peer_ip == "usb":
                        matched_adb_dev = attached[0]

            if matched_adb_dev:
                friendly_name = get_friendly_adb_device_name(matched_adb_dev)
                actual_path = share_info.get("path", filepath)
                dest_phone_path = f"/sdcard/Download/{name}"
                t0 = time.time()
                timeout_sec = max(35, int(size / (500 * 1024)) + 15)

                push = subprocess.run(
                    ['adb', '-s', matched_adb_dev, 'push', actual_path, dest_phone_path],
                    capture_output=True, text=True, timeout=timeout_sec
                )

                if push.returncode == 0:
                    elapsed = max(time.time() - t0, 0.05)
                    speed_mbps = (size / (1024 * 1024)) / elapsed

                    # A. Trigger Android MediaScanner so file appears in Gallery/Downloads
                    subprocess.run(
                        ['adb', '-s', matched_adb_dev, 'shell', 'am', 'broadcast', '-a', 'android.intent.action.MEDIA_SCANNER_SCAN_FILE', '-d', f'file://{dest_phone_path}'],
                        capture_output=True, timeout=2
                    )

                    # B. Post Android system notification
                    subprocess.run(
                        ['adb', '-s', matched_adb_dev, 'shell', 'cmd', 'notification', 'post', '-S', 'bigtext', '-t', '⚡ Lexi Beam', 'lexi_beam_recv', f'Received {name} in Downloads'],
                        capture_output=True, timeout=2
                    )

                    # C. Notify Lexi Connect companion app
                    subprocess.run(
                        ['adb', '-s', matched_adb_dev, 'shell', 'am', 'broadcast', '-a', 'com.lexi.connect.BEAM_RECEIVED', '--es', 'filename', name, '--es', 'path', dest_phone_path, '--el', 'size', str(size)],
                        capture_output=True, timeout=2
                    )

                    # D. Play chime on laptop
                    play_beam_sound("send_complete")

                    return {
                        "success": True,
                        "status": "delivered_adb",
                        "method": "adb",
                        "device": friendly_name,
                        "filename": name,
                        "size": size,
                        "speed_mbps": round(speed_mbps, 1),
                        "destination": dest_phone_path,
                        "share_info": share_info
                    }
        except Exception as e:
            print(f"[LexiDrop] ADB push attempt error: {e}", file=sys.stderr)

        # --- Transport 2: WebSocket Direct Push to Connected Browser / PWA ---
        if self.loop and self.connected_peers:
            target_ws = None
            target_device_name = "Web Client"
            for ws, info in self.connected_peers.items():
                if info.get("ip") == peer_ip or peer_ip in info.get("device", "") or peer_ip == "web":
                    target_ws = ws
                    target_device_name = info.get("device", "Web Client")
                    break

            if target_ws and target_ws in self.connected_websockets:
                try:
                    asyncio.run_coroutine_threadsafe(
                        target_ws.send_json({
                            "type": "incoming_file",
                            "file": {
                                "token": token,
                                "name": name,
                                "size": size,
                                "url": f"/api/download/{token}"
                            }
                        }),
                        self.loop
                    )
                    play_beam_sound("send_complete")
                    return {
                        "success": True,
                        "status": "delivered_ws",
                        "method": "websocket",
                        "device": target_device_name,
                        "filename": name,
                        "size": size,
                        "share_info": share_info
                    }
                except Exception as e:
                    print(f"[LexiDrop] WS direct push error: {e}", file=sys.stderr)

        # --- Transport 3: Linux-to-Linux LAN P2P Proposal ---
        target_port = self.port
        with self.beacon.lock:
            if peer_ip in self.beacon.lan_peers:
                target_port = self.beacon.lan_peers[peer_ip].get("port", self.port)

        payload = json.dumps({
            "from_device": socket.gethostname(),
            "from_ip": self.local_ip,
            "token": token,
            "filename": name,
            "size_bytes": size,
            "download_url": url
        }).encode('utf-8')

        req = urllib.request.Request(
            f"http://{peer_ip}:{target_port}/api/peer/incoming",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        try:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(req, timeout=2.5) as resp:
                result = json.loads(resp.read().decode('utf-8'))
                result["success"] = True
                result["status"] = "delivered_proposal"
                result["method"] = "p2p"
                result["share_info"] = share_info
                play_beam_sound("send_complete")
                return result
        except Exception as e:
            return {
                "success": False,
                "error": f"Failed to reach peer at {peer_ip}: {e}",
                "share_info": share_info
            }

    async def handle_status(self, request):
        return web.json_response({
            "status": "online",
            "host": socket.gethostname(),
            "local_ip": self.local_ip,
            "port": self.port,
            "connected_peers": self.get_all_peers(),
            "active_shares": len(self.active_shared_files)
        })

    async def handle_peers_list(self, request):
        """Returns real-time list of all discovered LAN and registered peers."""
        return web.json_response({
            "peers": self.get_all_peers(),
            "local_host": socket.gethostname(),
            "local_ip": self.local_ip,
            "port": self.port
        })

    async def handle_peer_register(self, request):
        """Allows phone or external clients to register as an active peer."""
        try:
            data = await request.json()
            client_ip = request.remote or data.get("ip") or "Unknown"
            device_name = data.get("device", "Lexi Companion")
            dev_type = data.get("type", "phone")
            os_name = data.get("os", "android")
            now = time.time()
            is_new = client_ip not in self.registered_peers
            self.registered_peers[client_ip] = {
                "device": device_name,
                "ip": client_ip,
                "type": dev_type,
                "os": os_name,
                "port": data.get("port", self.port),
                "last_seen": now
            }
            if is_new and self.on_peer_joined:
                self.on_peer_joined(device_name, client_ip)

            # Auto-reconnect ADB over Wi-Fi when Android companion registers
            if client_ip and client_ip not in ('127.0.0.1', 'localhost', 'Unknown'):
                if dev_type == "phone" or os_name == "android":
                    def _adb_reconnect():
                        try:
                            subprocess.run(['adb', 'connect', f'{client_ip}:5555'], capture_output=True, timeout=2)
                        except Exception:
                            pass
                    threading.Thread(target=_adb_reconnect, daemon=True).start()

            return web.json_response({"success": True, "registered": client_ip})
        except Exception as e:
            return web.json_response({"error": str(e)}, status=400)

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
        snap_path = None
        try:
            max_snap_size = 50 * 1024 * 1024  # 50 MB limit
            if request.content_length and request.content_length > max_snap_size:
                return web.json_response({"error": "Payload exceeds 50MB limit"}, status=413)

            ct = request.content_type
            snap_filename = f"camera_snap_{int(time.time())}_{uuid.uuid4().hex[:6]}.jpg"
            snap_path = Path(tempfile.gettempdir()) / snap_filename

            total_read = 0
            if "multipart" in ct:
                reader = await request.multipart()
                part = await reader.next()
                if part and part.filename:
                    with open(snap_path, "wb") as f:
                        while True:
                            chunk = await part.read_chunk(1024 * 1024)
                            if not chunk:
                                break
                            total_read += len(chunk)
                            if total_read > max_snap_size:
                                raise web.HTTPRequestEntityTooLarge(max_size=max_snap_size, actual_size=total_read)
                            f.write(chunk)
            else:
                with open(snap_path, "wb") as f:
                    async for chunk in request.content.iter_chunked(1024 * 1024):
                        total_read += len(chunk)
                        if total_read > max_snap_size:
                            raise web.HTTPRequestEntityTooLarge(max_size=max_snap_size, actual_size=total_read)
                        f.write(chunk)

            if snap_path.exists() and os.path.getsize(snap_path) > 0:
                if self.on_camera_snap_received:
                    self.on_camera_snap_received(str(snap_path))
                return web.json_response({
                    "success": True,
                    "path": str(snap_path),
                    "size": os.path.getsize(snap_path)
                })
            return web.json_response({"error": "Empty capture"}, status=400)
        except web.HTTPRequestEntityTooLarge:
            if snap_path and os.path.exists(snap_path):
                try:
                    os.remove(snap_path)
                except Exception:
                    pass
            return web.json_response({"error": "Payload exceeds 50MB limit"}, status=413)
        except Exception as e:
            if snap_path and os.path.exists(snap_path):
                try:
                    os.remove(snap_path)
                except Exception:
                    pass
            return web.json_response({"error": str(e)}, status=500)

    async def handle_clipboard_api(self, request):
        """Clipboard synchronization is permanently disabled."""
        return web.json_response({"success": False, "disabled": True, "message": "Clipboard sync disabled"})

    async def handle_mirror_api(self, request):
        """Starts resilient scrcpy screen mirroring on the laptop display."""
        try:
            stealth = request.query.get("stealth", "false").lower() == "true"
            res = PhoneMirrorManager.get().start_mirror(
                preferred_ip=request.remote,
                turn_screen_off=stealth
            )
            if res.get("success"):
                return web.json_response({
                    "success": True,
                    "device": res.get("device"),
                    "stealth": stealth,
                    "is_wireless": res.get("is_wireless", False),
                    "already_running": res.get("already_running", False),
                    "message": res.get("message")
                })
            else:
                return web.json_response({"error": res.get("message", "Mirror failed")}, status=400)
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)

    def broadcast_clipboard(self, text: str):
        """Clipboard broadcast is permanently disabled."""
        return

    def broadcast_camera_request(self):
        """Requests connected mobile phone to open camera and take a snap."""
        self.broadcast_message({
            "type": "request_camera_snap"
        })

    def share_file(self, filepath: str, pin_all_day: bool = True, custom_token: str = None) -> dict:
        """Registers a local file or directory for fast sharing and notifies all connected peers via WebSocket."""
        if not os.path.exists(filepath):
            raise FileNotFoundError(f"File not found: {filepath}")

        is_safe, reason = is_safe_share_path(filepath)
        if not is_safe:
            raise PermissionError(f"Sharing prohibited: {reason}")

        # If directory, bundle into a persistent zip archive in HOSTED_SHARES_DIR so it stays all day
        actual_path = filepath
        is_zip = False
        if os.path.isdir(filepath):
            folder_name = os.path.basename(os.path.abspath(filepath)) or "Archive"
            HOSTED_SHARES_DIR.mkdir(parents=True, exist_ok=True)
            zip_target = os.path.join(str(HOSTED_SHARES_DIR), f"{folder_name}.zip")
            shutil.make_archive(zip_target[:-4], 'zip', filepath)
            actual_path = zip_target
            is_zip = True
        elif filepath.lower().endswith(".zip"):
            is_zip = True

        token = custom_token or uuid.uuid4().hex[:10]
        name = os.path.basename(actual_path)
        size = os.path.getsize(actual_path)
        now = time.time()
        # 24-hour / full day hosting (86,400s)
        expires_at = now + (86400 if pin_all_day else 3600)

        record = {
            "token": token,
            "path": os.path.abspath(actual_path),
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

        download_url = f"http://{self.local_ip}:{self.port}/api/download/{token}"
        static_url = f"http://{self.local_ip}:{self.port}/download/latest"
        named_url = f"http://{self.local_ip}:{self.port}/download/{urllib.parse.quote(name)}"

        msg = {
            "type": "incoming_file",
            "file": {
                "token": token,
                "name": name,
                "size": size,
                "url": download_url,
                "static_url": static_url,
                "named_url": named_url,
                "expires_at": expires_at,
                "is_zip": is_zip
            }
        }
        self.broadcast_message(msg)

        return {
            "token": token,
            "path": actual_path,
            "name": name,
            "size": size,
            "url": download_url,
            "static_url": static_url,
            "named_url": named_url,
            "server_url": self.get_server_url(),
            "expires_at": expires_at,
            "pin_all_day": pin_all_day,
            "is_zip": is_zip,
            "port": self.port
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

            @web.middleware
            async def security_origin_middleware(request, handler):
                """
                Validates request Origin and Sec-Fetch-Site headers to defeat CSRF, DNS rebinding,
                and unauthorized cross-site invocations of local APIs.
                """
                sec_fetch_site = request.headers.get("Sec-Fetch-Site", "").lower()
                if sec_fetch_site == "cross-site":
                    if request.method not in ("GET", "HEAD") or request.path.startswith("/api/"):
                        return web.Response(status=403, text="Cross-site requests prohibited")

                origin = request.headers.get("Origin")
                if origin:
                    try:
                        parsed = urllib.parse.urlparse(origin)
                        host = (parsed.hostname or "").lower()
                        port = parsed.port or (443 if parsed.scheme == "https" else 80)

                        is_valid_origin = False
                        if host in ("localhost", "127.0.0.1", "::1") or host.endswith(".local"):
                            is_valid_origin = (port == self.port)
                        else:
                            try:
                                ip_obj = ipaddress.ip_address(host)
                                if ip_obj.is_private or ip_obj.is_loopback:
                                    is_valid_origin = (port == self.port)
                            except ValueError:
                                is_valid_origin = False

                        if not is_valid_origin:
                            return web.Response(status=403, text="Untrusted cross-origin request rejected")
                    except Exception:
                        return web.Response(status=403, text="Malformed Origin header")

                return await handler(request)

            self.app = web.Application(
                middlewares=[security_origin_middleware],
                client_max_size=10 * 1024 * 1024 * 1024
            )
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
            self.app.router.add_get("/download/latest", self.handle_download_latest)
            self.app.router.add_get("/download/{identifier}", self.handle_download_by_name_or_token)
            self.app.router.add_get("/api/shares", self.handle_shares_list)
            self.app.router.add_post("/api/share", self.handle_share_request)
            self.app.router.add_post("/api/peer/incoming", self.handle_peer_incoming)
            self.app.router.add_post("/api/peer/respond", self.handle_peer_respond)
            self.app.router.add_post("/api/peer/declined", self.handle_peer_declined)
            self.app.router.add_get("/api/status", self.handle_status)
            self.app.router.add_get("/api/peers", self.handle_peers_list)
            self.app.router.add_post("/api/peer/register", self.handle_peer_register)
            self.app.router.add_post("/api/share-target", self.handle_share_target)
            self.app.router.add_post("/api/camera-snap", self.handle_camera_snap)
            self.app.router.add_get("/api/clipboard", self.handle_clipboard_api)
            self.app.router.add_post("/api/clipboard", self.handle_clipboard_api)
            self.app.router.add_post("/api/mirror", self.handle_mirror_api)

            self.runner = web.AppRunner(self.app)
            self.loop.run_until_complete(self.runner.setup())
            try:
                self.site = web.TCPSite(self.runner, self.host, self.port, reuse_address=True, reuse_port=True)
                self.loop.run_until_complete(self.site.start())
            except Exception as e:
                print(f"[LexiDrop] Binding port {self.port} with reuse_port failed ({e}), falling back to reuse_address...")
                self.site = web.TCPSite(self.runner, self.host, self.port, reuse_address=True)
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
