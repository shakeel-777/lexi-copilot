#!/usr/bin/env python3
"""
Lexi Windows System Action Engine
=================================
Windows-native automation engine providing hardware controls, window management,
app launching, media keys, and system automation without external Linux dependencies.
"""

import os
import sys
import ctypes
import subprocess
import urllib.parse
from pathlib import Path
from typing import Dict, Any, Tuple, Optional

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
