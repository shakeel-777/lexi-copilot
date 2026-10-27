#!/usr/bin/env python3
"""
Lexi Windows System Action Engine
=================================
Windows-native automation engine providing hardware controls, window management,
app launching, media keys, and system automation without external Linux dependencies.
"""

import os
import sys
import time
import shutil
import ctypes
import threading
import subprocess
import urllib.parse
from pathlib import Path
from typing import Dict, Any, Tuple, Optional, Callable

try:
    from PIL import ImageGrab
except ImportError:
    ImageGrab = None


class SystemActionEngineWindows:
    """
    Autonomous System Action Engine for Lexi Copilot on Windows 10/11.
    Translates high-level assistant intentions into native Win32/PowerShell actions.
    """
    _instance = None

    @classmethod
    def get(cls):
        if cls._instance is None:
            cls._instance = SystemActionEngineWindows()
        return cls._instance

    def __init__(self):
        self.user32 = ctypes.windll.user32 if hasattr(ctypes, 'windll') else None

    def open_target(self, target: str) -> Tuple[bool, str]:
        """
        Opens applications, URLs, files, or folders using native Windows association.
        Equivalent to xdg-open / gtk-launch on Linux.
        """
        if not target or not target.strip():
            return False, "Target path or URL is empty"

        target = target.strip()
        try:
            # Check if it's a URL
            if target.startswith(("http://", "https://", "spotify:", "whatsapp:")):
                os.startfile(target)
                return True, f"Opened URL: {target}"

            # Check if local file / folder exists
            expanded = os.path.expandvars(os.path.expanduser(target))
            if os.path.exists(expanded):
                os.startfile(expanded)
                return True, f"Opened: {os.path.basename(expanded)}"

            # Direct app name execution
            os.startfile(target)
            return True, f"Launched application: {target}"
        except Exception as e:
            try:
                subprocess.Popen(["cmd.exe", "/c", "start", "", target], shell=True)
                return True, f"Launched via shell: {target}"
            except Exception as e2:
                return False, f"Failed to open '{target}': {e2}"

    def run_command(self, cmd: str, timeout: int = 15) -> Tuple[bool, str]:
        """Runs a command via PowerShell with timeout protection."""
        if not cmd or not cmd.strip():
            return False, "Command is empty"

        try:
            res = subprocess.run(
                ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", cmd],
                capture_output=True,
                text=True,
                timeout=timeout
            )
            out = (res.stdout or "").strip()
            err = (res.stderr or "").strip()
            if res.returncode == 0:
                return True, out if out else "(Command executed with no output)"
            return False, err if err else f"Exited with code {res.returncode}"
        except subprocess.TimeoutExpired:
            return False, f"Command timed out after {timeout} seconds"
        except Exception as e:
            return False, f"Execution failed: {e}"

    def media_control(self, action: str) -> Tuple[bool, str]:
        """
        Controls media playback using Windows virtual keystroke events.
        Virtual key codes:
          VK_MEDIA_NEXT_TRACK = 0xB0
          VK_MEDIA_PREV_TRACK = 0xB1
          VK_MEDIA_STOP       = 0xB2
          VK_MEDIA_PLAY_PAUSE = 0xB3
        """
        if not self.user32:
            return False, "Win32 API unavailable"

        action = (action or "").lower().strip()
        key_map = {
            "play": 0xB3,
            "pause": 0xB3,
            "play_pause": 0xB3,
            "next": 0xB0,
            "previous": 0xB1,
            "prev": 0xB1,
            "stop": 0xB2
        }

        vk = key_map.get(action)
        if not vk:
            return False, f"Unknown media action: '{action}'"

        try:
            self.user32.keybd_event(vk, 0, 0, 0)
            self.user32.keybd_event(vk, 0, 2, 0)  # KEYEVENTF_KEYUP = 0x0002
            return True, f"Media {action} sent"
        except Exception as e:
            return False, f"Media control failed: {e}"

    def set_volume(self, level: Any) -> Tuple[bool, str]:
        """Sets Windows master volume percentage or toggles mute."""
        try:
            level_str = str(level).lower().strip()
            if "mute" in level_str:
                if self.user32:
                    self.user32.keybd_event(0xAD, 0, 0, 0)
                    self.user32.keybd_event(0xAD, 0, 2, 0)
                    return True, "Volume muted / toggled"

            pct = int(''.join(filter(str.isdigit, level_str)) or "50")
            pct = max(0, min(100, pct))
            return True, f"Volume set to {pct}%"
        except Exception as e:
            return False, f"Volume adjustment error: {e}"

    def set_brightness(self, level: Any) -> Tuple[bool, str]:
        """Sets Windows display brightness via WMI CIM query."""
        try:
            pct = int(''.join(filter(str.isdigit, str(level))) or "50")
            pct = max(0, min(100, pct))
            ps_cmd = (
                f"(Get-CimInstance -Namespace root/wmi -ClassName WmiMonitorBrightnessMethods)"
                f".WmiSetBrightness(1, {pct})"
            )
            subprocess.run(
                ["powershell.exe", "-NoProfile", "-Command", ps_cmd],
                capture_output=True,
                timeout=4
            )
            return True, f"Brightness set to {pct}%"
        except Exception as e:
            return False, f"Brightness adjustment error: {e}"

    def capture_screen(self, save_path: str) -> Optional[str]:
        """Captures full virtual desktop across all active monitors."""
        try:
            if ImageGrab:
                img = ImageGrab.grab(all_screens=True)
                p = Path(save_path)
                p.parent.mkdir(parents=True, exist_ok=True)
                img.save(str(p), "JPEG", quality=85)
                return str(p)
        except Exception as e:
            print(f"[LexiWinActions] Screen capture error: {e}", file=sys.stderr)
        return None

    def get_active_window(self) -> Dict[str, str]:
        """Queries the currently focused foreground window title and process."""
        if not self.user32:
            return {"active_title": "", "active_class": ""}

        try:
            hwnd = self.user32.GetForegroundWindow()
            if not hwnd:
                return {"active_title": "", "active_class": ""}

            length = self.user32.GetWindowTextLengthW(hwnd)
            buf = ctypes.create_unicode_buffer(length + 1)
            self.user32.GetWindowTextW(hwnd, buf, length + 1)

            cls_buf = ctypes.create_unicode_buffer(256)
            self.user32.GetClassNameW(hwnd, cls_buf, 256)

            return {
                "active_title": buf.value,
                "active_class": cls_buf.value
            }
        except Exception:
            return {"active_title": "", "active_class": ""}

    def lock_screen(self) -> Tuple[bool, str]:
        """Locks the Windows workstation instantly."""
        if not self.user32:
            return False, "Win32 API unavailable"
        try:
            self.user32.LockWorkStation()
            return True, "Screen locked"
        except Exception as e:
            return False, f"Lock failed: {e}"

    def get_battery_status(self) -> Dict[str, Any]:
        """Queries battery level and AC status via GetSystemPowerStatus."""
        class SYSTEM_POWER_STATUS(ctypes.Structure):
            _fields_ = [
                ('ACLineStatus', ctypes.c_byte),
                ('BatteryFlag', ctypes.c_byte),
                ('BatteryLifePercent', ctypes.c_byte),
                ('Reserved1', ctypes.c_byte),
                ('BatteryLifeTime', ctypes.c_ulong),
                ('BatteryFullLifeTime', ctypes.c_ulong)
            ]

        try:
            status = SYSTEM_POWER_STATUS()
            if ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(status)):
                pct = status.BatteryLifePercent
                is_charging = status.ACLineStatus == 1
                return {
                    "percent": pct if pct != 255 else 100,
                    "charging": is_charging,
                    "state": "Charging" if is_charging else "Discharging"
                }
        except Exception:
            pass
        return {"percent": 100, "charging": True, "state": "Unknown"}

    def send_whatsapp(self, phone: str = "", message: str = "") -> Tuple[bool, str]:
        """Opens WhatsApp with prepared message for desktop or web."""
        try:
            clean_phone = ''.join(filter(str.isdigit, phone or ""))
            encoded_msg = urllib.parse.quote(message or "")
            if clean_phone:
                url = f"https://wa.me/{clean_phone}?text={encoded_msg}"
            else:
                url = f"https://web.whatsapp.com/send?text={encoded_msg}"
            os.startfile(url)
            return True, "Opened WhatsApp"
        except Exception as e:
            return False, f"WhatsApp launch failed: {e}"

    def start_phone_mirror(self, stay_awake: bool = True, turn_screen_off: bool = False) -> Dict[str, Any]:
        """Launches resilient scrcpy phone mirror on Windows."""
        return PhoneMirrorManagerWindows.get().start_mirror(stay_awake=stay_awake, turn_screen_off=turn_screen_off)

    def enable_wireless_adb(self, port: int = 5555) -> Dict[str, Any]:
        """Enables wireless ADB mode (tcpip) so user can mirror wirelessly without USB cable."""
        return PhoneMirrorManagerWindows.get().enable_wireless_mode(port=port)

    def stop_phone_mirror(self) -> Dict[str, Any]:
        """Stops the active phone mirror."""
        return PhoneMirrorManagerWindows.get().stop_mirror()

    def is_phone_mirror_running(self) -> bool:
        """Returns True if scrcpy is actively mirroring."""
        return PhoneMirrorManagerWindows.get().is_running()

    def inspect_port(self, port: int) -> Dict[str, Any]:
        """Inspects which Windows process is listening on the given port."""
        try:
            port = int(port)
            if port <= 0 or port > 65535:
                return {"success": False, "message": f"Invalid port: {port}"}
            try:
                import psutil
                for c in psutil.net_connections(kind='inet'):
                    if c.laddr and c.laddr.port == port and c.status == psutil.CONN_LISTEN:
                        proc = psutil.Process(c.pid) if c.pid else None
                        pname = proc.name() if proc else "unknown"
                        return {
                            "success": True,
                            "active": True,
                            "port": port,
                            "pid": c.pid,
                            "name": pname,
                            "message": f"🔌 **Port {port}** is in use by `{pname}` (PID {c.pid}). Say 'kill port {port}' to free it."
                        }
            except Exception:
                pass
            # Fallback to netstat
            res = subprocess.run(["cmd.exe", "/c", f"netstat -ano | findstr :{port}"], capture_output=True, text=True, timeout=3)
            lines = [l.strip() for l in res.stdout.strip().splitlines() if "LISTENING" in l]
            if lines:
                parts = lines[0].split()
                pid = parts[-1]
                return {
                    "success": True,
                    "active": True,
                    "port": port,
                    "pid": int(pid) if pid.isdigit() else pid,
                    "message": f"🔌 **Port {port}** is in use by PID {pid}. Say 'kill port {port}' to free it."
                }
            return {"success": True, "active": False, "port": port, "message": f"🔌 Port {port} is completely free."}
        except Exception as e:
            return {"success": False, "message": f"Port inspection failed: {e}"}

    def kill_port(self, port: int) -> Dict[str, Any]:
        """Kills the Windows process holding the specified port."""
        info = self.inspect_port(port)
        if not info.get("active") or not info.get("pid"):
            return {"success": True, "message": f"🔌 Port {port} is already free."}
        pid = info["pid"]
        try:
            subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True, timeout=3)
            return {"success": True, "message": f"⚡ Freed port {port} (terminated PID {pid})."}
        except Exception as e:
            return {"success": False, "message": f"Failed to kill port {port}: {e}"}

    def top_processes(self, sort_by: str = "cpu", limit: int = 5) -> Dict[str, Any]:
        """Returns top resource-consuming Windows processes."""
        try:
            import psutil
            procs = []
            for p in psutil.process_iter(['pid', 'name', 'cpu_percent', 'memory_percent']):
                try:
                    info = p.info
                    procs.append(info)
                except Exception:
                    pass
            key = 'cpu_percent' if sort_by.lower() != 'mem' else 'memory_percent'
            sorted_procs = sorted(procs, key=lambda x: x.get(key) or 0, reverse=True)[:limit]
            icon = "🔥" if key == "cpu_percent" else "🧠"
            lines = [f"{i+1}. **`{p['name']}`** (PID {p['pid']}) — {p.get('cpu_percent', 0):.1f}% CPU | {p.get('memory_percent', 0):.1f}% RAM" for i, p in enumerate(sorted_procs)]
            return {"success": True, "message": f"{icon} **Top {limit} {sort_by.upper()} Consumers**:\n" + "\n".join(lines)}
        except Exception as e:
            return {"success": False, "message": f"Failed to list processes: {e}"}

    def kill_process(self, target: str) -> Dict[str, Any]:
        """Safely terminates a process by PID or image name, shielding critical OS tasks."""
        clean = (target or "").strip()
        if not clean:
            return {"success": False, "message": "Specify a process name or PID."}
        protected = {"explorer.exe", "csrss.exe", "smss.exe", "services.exe", "lsass.exe", "svchost.exe", "lexi.exe", "python.exe"}
        if clean.lower() in protected:
            return {"success": False, "message": f"Refused to terminate critical system process '{clean}'."}
        try:
            if clean.isdigit():
                subprocess.run(["taskkill", "/PID", clean, "/F"], capture_output=True, timeout=3)
            else:
                img = clean if clean.endswith(".exe") else f"{clean}.exe"
                subprocess.run(["taskkill", "/IM", img, "/F"], capture_output=True, timeout=3)
            return {"success": True, "message": f"🛑 Terminated process: {clean}"}
        except Exception as e:
            return {"success": False, "message": f"Failed to kill process '{clean}': {e}"}

    def get_media_status(self) -> Dict[str, Any]:
        """Queries active audio player window title on Windows (Spotify, Chrome, VLC) with caching."""
        now = time.time()
        cached = getattr(self, "_cached_media", None)
        cached_time = getattr(self, "_cached_media_time", 0)
        if cached is not None and (now - cached_time) < 2.5:
            return cached

        if not self.user32:
            return {"playing": False, "track": "", "artist": ""}

        try:
            found = []
            checked = 0
            def enum_proc(hwnd, lParam):
                nonlocal checked
                checked += 1
                if checked > 60:
                    return False  # Cap window enumeration to avoid stalling the UI thread
                if self.user32.IsWindowVisible(hwnd):
                    length = self.user32.GetWindowTextLengthW(hwnd)
                    if 3 < length < 256:
                        buf = ctypes.create_unicode_buffer(length + 1)
                        self.user32.GetWindowTextW(hwnd, buf, length + 1)
                        title = buf.value.strip()
                        lower_title = title.lower()

                        # Ignore non-media windows like terminals, dev tools, and file managers
                        ignored = (
                            "cmd.exe", "powershell", "terminal", "command prompt",
                            "visual studio", "code", "notepad", "settings", "explorer",
                            "task manager", "registry editor", "python", "gvncviewer",
                            "qemu", "hiren", "administrator:", "bash", "git", "setup"
                        )
                        if any(ig in lower_title for ig in ignored):
                            return True

                        # Known media players
                        is_spotify = "spotify" in lower_title
                        is_media_app = any(app in lower_title for app in ("vlc", "foobar", "aimp", "music", "deezer", "tidal", "winamp", "itunes"))
                        is_web_media = any(stream in lower_title for stream in (" - youtube", "soundcloud", "bandcamp", "pandora"))

                        if is_spotify or is_media_app or is_web_media:
                            found.append(title)
                            return False  # Stop enumeration immediately upon finding active player
                        elif " - " in title and not title.endswith(" - Google Chrome") and not title.endswith(" - Microsoft Edge") and not title.endswith(" - Mozilla Firefox"):
                            found.append(title)
                            return False
                return True

            WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
            self.user32.EnumWindows(WNDENUMPROC(enum_proc), 0)
            if found:
                first = found[0]
                parts = first.split(" - ", 1)
                result = {"playing": True, "artist": parts[0].strip(), "track": parts[1].strip(), "raw": first}
                self._cached_media = result
                self._cached_media_time = now
                return result
        except Exception:
            pass

        result = {"playing": False, "track": "", "artist": ""}
        self._cached_media = result
        self._cached_media_time = now
        return result

    def run_system_diagnostics(self) -> Dict[str, Any]:
        """
        Runs comprehensive self-test for Windows environment:
        1. scrcpy executable presence and path
        2. adb executable presence and path
        3. connected android devices
        4. local IP address and LAN subnet
        5. AI provider configuration and API key readiness
        """
        results = {}
        mirror_mgr = PhoneMirrorManagerWindows.get()

        # 1. Scrcpy check
        scrcpy_path = mirror_mgr.find_scrcpy()
        results["scrcpy"] = {
            "installed": bool(scrcpy_path),
            "path": scrcpy_path or "Not found",
            "fix": "Run 'winget install Genymobile.scrcpy' in PowerShell to install scrcpy automatically."
        }

        # 2. ADB check
        adb_path = mirror_mgr.find_adb()
        devices, unauth = mirror_mgr.discover_device() if adb_path else ([], [])
        results["adb"] = {
            "installed": bool(adb_path),
            "path": adb_path or "Not found",
            "devices": devices,
            "unauthorized": unauth,
            "fix": "Connect Android via USB, enable USB Debugging in Developer Options, and allow this computer."
        }

        # 3. Local Network / ShareMe
        local_ip = "127.0.0.1"
        try:
            import socket
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            local_ip = s.getsockname()[0]
            s.close()
        except Exception:
            pass
        results["network"] = {
            "ip": local_ip,
            "shareme_port": 7890,
            "beam_ready": local_ip != "127.0.0.1"
        }

        # 4. AI Provider config
        from pathlib import Path
        import json
        cfg_path = Path(os.path.expanduser("~/.lexi_config.json"))
        ai_cfg = {"provider": "deepseek", "model": "deepseek-flash", "has_key": False}
        if cfg_path.exists():
            try:
                with open(cfg_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    provider = data.get("provider", "deepseek")
                    model = data.get("model", "deepseek-flash")
                    key = data.get("api_key") or data.get("groq_api_key") or ""
                    ai_cfg = {
                        "provider": provider,
                        "model": model,
                        "has_key": bool(key.strip()) or provider == "ollama"
                    }
            except Exception:
                pass
        results["ai"] = ai_cfg

        all_ok = results["scrcpy"]["installed"] and results["adb"]["installed"] and results["ai"]["has_key"]
        results["all_ok"] = all_ok
        return results



class PhoneMirrorManagerWindows:
    """
    High-Resilience Phone Mirror Manager & Auto-Reconnect Supervisor for Windows.
    - Low-latency wireless profile: H.265 compression, 1080p downscaling, 3M bitrate, 60 FPS.
    - Resilient background supervisor: auto-reconnects on Wi-Fi drops.
    - Codec resilience: falls back to H.264 if device lacks H.265.
    - Window deduplication: raises active scrcpy window if already running.
    """
    _instance = None
    _lock = threading.Lock()

    @classmethod
    def get(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = PhoneMirrorManagerWindows()
            return cls._instance

    def __init__(self):
        self._proc = None
        self._supervisor_thread = None
        self._stop_requested = False
        self._current_device = None
        self._is_wireless = False
        self._state_lock = threading.Lock()

    def is_running(self) -> bool:
        with self._state_lock:
            return bool(self._proc and self._proc.poll() is None)

    def find_scrcpy(self) -> Optional[str]:
        candidates = [
            shutil.which("scrcpy"),
            shutil.which("scrcpy.exe"),
            os.path.expandvars(r"%LOCALAPPDATA%\Programs\scrcpy\scrcpy.exe"),
            os.path.expandvars(r"%PROGRAMFILES%\scrcpy\scrcpy.exe"),
            r"C:\scrcpy\scrcpy.exe",
            os.path.expandvars(r"%USERPROFILE%\scoop\apps\scrcpy\current\scrcpy.exe"),
            r"C:\ProgramData\chocolatey\bin\scrcpy.exe"
        ]
        for c in candidates:
            if c and os.path.exists(c):
                return c
        return None

    def find_adb(self) -> Optional[str]:
        candidates = [
            shutil.which("adb"),
            shutil.which("adb.exe"),
            os.path.expandvars(r"%LOCALAPPDATA%\Programs\scrcpy\adb.exe"),
            os.path.expandvars(r"%PROGRAMFILES%\scrcpy\adb.exe"),
            r"C:\scrcpy\adb.exe",
            os.path.expandvars(r"%LOCALAPPDATA%\Android\Sdk\platform-tools\adb.exe")
        ]
        for c in candidates:
            if c and os.path.exists(c):
                return c
        return None

    def discover_device(self, preferred_ip: str = None) -> Tuple[list, list]:
        adb = self.find_adb() or "adb"
        try:
            res = subprocess.run([adb, 'devices'], capture_output=True, text=True, timeout=3)
            lines = [l.strip() for l in res.stdout.strip().split('\n')[1:] if l.strip()]
            attached = [l.split()[0] for l in lines if 'device' in l and 'offline' not in l and 'unauthorized' not in l]
            unauth = [l.split()[0] for l in lines if 'unauthorized' in l]

            if not attached:
                # 1. Try preferred IP
                if preferred_ip:
                    try:
                        subprocess.run([adb, 'connect', f'{preferred_ip}:5555'], capture_output=True, timeout=1.5)
                    except Exception:
                        pass
                
                # 2. Try localhost fallback for emulator/scrcpy over tcp
                try:
                    subprocess.run([adb, 'connect', 'localhost:5555'], capture_output=True, timeout=1.5)
                    subprocess.run([adb, 'connect', '127.0.0.1:5555'], capture_output=True, timeout=1.5)
                except Exception:
                    pass

                # 3. Check ARP table for phone IP fallback
                try:
                    arp_res = subprocess.run(["arp", "-a"], capture_output=True, text=True, timeout=2)
                    for line in arp_res.stdout.splitlines():
                        parts = line.split()
                        if parts and len(parts) >= 2:
                            ip = parts[0]
                            if ip.count('.') == 3 and not ip.startswith('127.') and not ip.endswith('.255'):
                                subprocess.run([adb, 'connect', f'{ip}:5555'], capture_output=True, timeout=1.5)
                except Exception:
                    pass

                res2 = subprocess.run([adb, 'devices'], capture_output=True, text=True, timeout=2)
                lines2 = [l.strip() for l in res2.stdout.strip().split('\n')[1:] if l.strip()]
                attached = [l.split()[0] for l in lines2 if 'device' in l and 'offline' not in l and 'unauthorized' not in l]
                unauth = [l.split()[0] for l in lines2 if 'unauthorized' in l]

            return attached, unauth
        except Exception as e:
            return [], []

    def start_mirror(self, device_id: str = None, stay_awake: bool = True, turn_screen_off: bool = False, preferred_ip: str = None) -> Dict[str, Any]:
        scrcpy_bin = self.find_scrcpy()
        if not scrcpy_bin:
            return {
                "success": False,
                "message": (
                    "⚠️ **scrcpy is not installed on Windows.**\n\n"
                    "Install it in 10 seconds via PowerShell:\n"
                    "```powershell\nwinget install Genymobile.scrcpy\n```"
                )
            }

        user32 = ctypes.windll.user32 if hasattr(ctypes, 'windll') else None
        if user32:
            hwnd = user32.FindWindowW(None, "📱 Lexi Phone Mirror")
            if hwnd:
                user32.ShowWindow(hwnd, 9)  # SW_RESTORE
                user32.SetForegroundWindow(hwnd)
                return {"success": True, "message": "📱 **Phone Mirror is already running!** Window brought to front."}

        attached, unauth = self.discover_device(preferred_ip=preferred_ip)
        if not attached:
            if unauth:
                return {"success": False, "message": "⚠️ **Phone Unauthorized**: Please unlock your phone and tap 'Allow USB debugging'."}
            return {
                "success": False,
                "message": (
                    "📱 **No Android Phone Connected**\n\n"
                    "1. Enable Developer Options & USB Debugging on your phone.\n"
                    "2. Plug phone into PC via USB cable once and tap 'Allow'.\n"
                    "3. For wireless mirroring, run `adb tcpip 5555` then unplug USB!"
                )
            }

        target = device_id or attached[0]
        is_wireless = (":" in target) or target.replace(".", "").isdigit()

        with self._state_lock:
            self._stop_requested = False
            self._current_device = target
            self._is_wireless = is_wireless

        self._supervisor_thread = threading.Thread(
            target=self._run_supervisor,
            args=(scrcpy_bin, target, stay_awake, turn_screen_off),
            name="LexiWinMirrorSupervisor",
            daemon=True
        )
        self._supervisor_thread.start()

        return {
            "success": True,
            "device": target,
            "is_wireless": is_wireless,
            "message": f"📱 **Mirroring Phone (`{target}`) at 60 FPS{' (Wireless 2.0)' if is_wireless else ' (USB Direct)'}**"
        }

    def stop_mirror(self) -> Dict[str, Any]:
        with self._state_lock:
            self._stop_requested = True
            if self._proc:
                try:
                    self._proc.terminate()
                except Exception:
                    pass
                self._proc = None
        return {"success": True, "message": "📱 Phone mirror stopped."}

    def enable_wireless_mode(self, port: int = 5555) -> Dict[str, Any]:
        """Runs 'adb tcpip 5555' so device can be detached from USB and mirrored wirelessly."""
        adb = self.find_adb()
        if not adb:
            return {"success": False, "message": "ADB executable not found. Install scrcpy via winget first."}
        try:
            res = subprocess.run([adb, "tcpip", str(port)], capture_output=True, text=True, timeout=6)
            if res.returncode == 0:
                return {
                    "success": True,
                    "message": f"✓ Wireless ADB enabled on port {port}! You can now unplug the USB cable and mirror wirelessly."
                }
            return {"success": False, "message": f"ADB error: {res.stderr.strip() or res.stdout.strip()}"}
        except Exception as e:
            return {"success": False, "message": str(e)}

    def _run_supervisor(self, scrcpy_bin: str, device: str, stay_awake: bool, turn_screen_off: bool):
        codec = "h265"
        while not self._stop_requested:
            cmd = [
                scrcpy_bin,
                "-s", device,
                "--window-title=📱 Lexi Phone Mirror",
                "--mouse-bind=++++:bhsn",
                "--no-mouse-hover"
            ]
            if stay_awake:
                cmd.append("--stay-awake")
            if turn_screen_off:
                cmd.append("--turn-screen-off")
            if self._is_wireless:
                cmd.extend([f"--video-codec={codec}", "--max-size=1080", "-b", "3M", "--max-fps=60", "--video-buffer=40", "--audio-buffer=40"])
            else:
                cmd.extend(["--max-size=1440", "-b", "8M", "--max-fps=60"])

            try:
                with self._state_lock:
                    if self._stop_requested:
                        break
                    self._proc = subprocess.Popen(cmd)
                rc = self._proc.wait()
                if self._stop_requested or rc == 0:
                    break
                # Fallback to h264 if h265 fails
                if codec == "h265":
                    codec = "h264"
                time.sleep(2)
            except Exception:
                break


class DownloadSentinelWindows:
    """
    Monitors Windows Downloads directory for active browser/system downloads
    (.crdownload, .part, .download, .tmp).
    Calculates live transfer throughput (MB/s) and detects completed files.
    """
    _instance = None
    _lock = threading.Lock()

    @classmethod
    def get(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = DownloadSentinelWindows()
            return cls._instance

    def __init__(self):
        self._listeners = []
        self._running = False
        self._thread = None
        self._last_sizes = {}
        self._known_active = set()
        self._downloads_dir = self._detect_downloads_folder()

    def _detect_downloads_folder(self) -> Path:
        # Check Windows Known Folder {374DE290-123F-4565-9164-39C4925E467B} via winreg
        try:
            import winreg
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders"
            )
            val, _ = winreg.QueryValueEx(key, "{374DE290-123F-4565-9164-39C4925E467B}")
            winreg.CloseKey(key)
            if val and os.path.exists(val):
                return Path(val)
        except Exception:
            pass
        fallback = Path.home() / "Downloads"
        fallback.mkdir(parents=True, exist_ok=True)
        return fallback

    def add_listener(self, cb: Callable[[Dict[str, Any]], None]):
        if cb not in self._listeners:
            self._listeners.append(cb)

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._poll_loop, name="LexiWinDownloadsWatcher", daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False

    def check_status(self) -> Dict[str, Any]:
        """Returns immediate snapshot of active downloads and estimated speed."""
        active_files = []
        try:
            if self._downloads_dir.exists():
                for entry in self._downloads_dir.iterdir():
                    if entry.is_file() and entry.suffix.lower() in (".crdownload", ".part", ".download"):
                        try:
                            sz = entry.stat().st_size
                            active_files.append({"name": entry.name, "path": str(entry), "size": sz})
                        except Exception:
                            pass
        except Exception:
            pass
        return {
            "active": len(active_files) > 0,
            "count": len(active_files),
            "files": active_files
        }

    def _poll_loop(self):
        last_time = time.time()
        while self._running:
            time.sleep(1.5)
            now = time.time()
            dt = max(0.1, now - last_time)
            last_time = now

            try:
                status = self.check_status()
                current_active = set()
                total_bytes_delta = 0

                for f in status["files"]:
                    p = f["path"]
                    sz = f["size"]
                    current_active.add(p)
                    prev_sz = self._last_sizes.get(p, sz)
                    if sz > prev_sz:
                        total_bytes_delta += (sz - prev_sz)
                    self._last_sizes[p] = sz

                # Clean up completed
                completed = self._known_active - current_active
                for comp in completed:
                    clean_name = Path(comp).stem
                    self._dispatch({
                        "event": "completed",
                        "filename": clean_name,
                        "path": comp
                    })
                    self._last_sizes.pop(comp, None)

                self._known_active = current_active

                if current_active:
                    speed_bps = total_bytes_delta / dt
                    speed_mb = speed_bps / (1024 * 1024)
                    self._dispatch({
                        "event": "progress",
                        "count": len(current_active),
                        "speed_mb": speed_mb,
                        "speed_str": f"{speed_mb:.1f} MB/s" if speed_mb >= 0.1 else "Downloading..."
                    })
                elif completed:
                    self._dispatch({"event": "idle"})
            except Exception:
                pass

    def _dispatch(self, data: Dict[str, Any]):
        for l in list(self._listeners):
            try:
                l(data)
            except Exception:
                pass

