#!/usr/bin/env python3
"""
Lexi - Intelligent & Beautiful Desktop Screen Companion for Arch Linux (GNOME/Wayland).
Captures up to 5 screen pages or interactive snips and answers questions with a multimodal vision LLM.
Features:
1. Multi-Page Support: Attach up to 5 pages / snips to compare or analyze together.
2. Top-Island Floating Window (Dynamic Island): Anchored cleanly at the top of your screen.
3. Production-Grade Window Controls: Draggable island header, Minimize button, Esc to close.
4. Rich Markdown & Syntax-highlighted code blocks
5. Region Snip Mode (Super+Shift+C or --snip) & Full Screen (Super+C)
6. Multi-Turn Chat History with context memory & instant clean copy
"""

import sys
import os
import json
import time
import base64
import io
import re
import random
import threading
import subprocess
import urllib.request
import urllib.error
import urllib.parse
import math
import hashlib
from pathlib import Path
import cairo
import warnings
warnings.filterwarnings('ignore', category=DeprecationWarning)

# Ensure direct bypass for proxies
DIRECT_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

# Prefer X11 backend when available to allow top-island window positioning (Dynamic Island)
if 'GDK_BACKEND' not in os.environ and os.environ.get('DISPLAY'):
    os.environ['GDK_BACKEND'] = 'x11,wayland'

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('Xdp', '1.0')
try:
    gi.require_version('GdkX11', '4.0')
    from gi.repository import GdkX11
except Exception:
    pass
from gi.repository import Gtk, Adw, GLib, Gdk, Pango, Xdp, Gio, GObject

from PIL import Image, ImageDraw
import shutil
import converter

try:
    import lexi_drop
except Exception as _e:
    lexi_drop = None

APP_DIR = os.path.dirname(os.path.realpath(__file__))
CONFIG_FILE = os.path.join(APP_DIR, 'config.json')
STYLES_FILE = os.path.join(APP_DIR, 'styles.css')
LOG_FILE = '/tmp/lexi.log'

def log(msg):
    try:
        with open(LOG_FILE, 'a', encoding='utf-8') as f:
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")
    except Exception:
        pass

import ctypes

class _XClientMessageEvent(ctypes.Structure):
    _fields_ = [
        ('type', ctypes.c_int),
        ('serial', ctypes.c_ulong),
        ('send_event', ctypes.c_int),
        ('display', ctypes.c_void_p),
        ('window', ctypes.c_ulong),
        ('message_type', ctypes.c_ulong),
        ('format', ctypes.c_int),
        ('data_l', ctypes.c_long * 5)
    ]

class _XEvent(ctypes.Union):
    _fields_ = [
        ('type', ctypes.c_int),
        ('xclient', _XClientMessageEvent),
        ('pad', ctypes.c_byte * 192)
    ]

class X11Helper:
    """
    Zero-overhead direct X11 interface via ctypes libX11.
    Eliminates all subprocess/xdotool forking latency (<0.01ms vs 30ms).
    Provides buttery smooth 60-120fps window dragging and pointer tracking.
    """
    _instance = None

    def __init__(self):
        self.dpy = None
        self.root = None
        try:
            self.x11 = ctypes.CDLL('libX11.so.6')
            self.x11.XOpenDisplay.restype = ctypes.c_void_p
            self.x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
            self.dpy = self.x11.XOpenDisplay(None)
            if self.dpy:
                self.x11.XDefaultRootWindow.restype = ctypes.c_ulong
                self.x11.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
                self.root = self.x11.XDefaultRootWindow(self.dpy)

                # Ignore BadWindow and async X11 errors on destroyed surfaces
                self._x_err_proto = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p)
                self._x_err_cb = self._x_err_proto(lambda d, e: 0)
                self.x11.XSetErrorHandler.argtypes = [self._x_err_proto]
                self.x11.XSetErrorHandler.restype = ctypes.c_void_p
                self.x11.XSetErrorHandler(self._x_err_cb)

                self.x11.XMoveWindow.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int, ctypes.c_int]
                self.x11.XMoveWindow.restype = ctypes.c_int

                self.x11.XMoveResizeWindow.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int, ctypes.c_int, ctypes.c_uint, ctypes.c_uint]
                self.x11.XMoveResizeWindow.restype = ctypes.c_int

                self.x11.XUnmapWindow.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
                self.x11.XUnmapWindow.restype = ctypes.c_int

                self.x11.XFlush.argtypes = [ctypes.c_void_p]
                self.x11.XFlush.restype = ctypes.c_int

                self.x11.XInternAtom.restype = ctypes.c_ulong
                self.x11.XInternAtom.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int]

                self.x11.XSendEvent.restype = ctypes.c_int
                self.x11.XSendEvent.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int, ctypes.c_long, ctypes.c_void_p]

                self.x11.XRaiseWindow.restype = ctypes.c_int
                self.x11.XRaiseWindow.argtypes = [ctypes.c_void_p, ctypes.c_ulong]

                self.x11.XChangeProperty.restype = ctypes.c_int
                self.x11.XChangeProperty.argtypes = [
                    ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong,
                    ctypes.c_int, ctypes.c_int, ctypes.c_char_p, ctypes.c_int
                ]

                # Intern EWMH atoms
                self.atom_net_wm_state = self.x11.XInternAtom(self.dpy, b'_NET_WM_STATE', 0)
                self.atom_state_above = self.x11.XInternAtom(self.dpy, b'_NET_WM_STATE_ABOVE', 0)
                self.atom_state_sticky = self.x11.XInternAtom(self.dpy, b'_NET_WM_STATE_STICKY', 0)
                self.atom_state_skip_tb = self.x11.XInternAtom(self.dpy, b'_NET_WM_STATE_SKIP_TASKBAR', 0)
                self.atom_state_skip_pager = self.x11.XInternAtom(self.dpy, b'_NET_WM_STATE_SKIP_PAGER', 0)
                self.atom_net_wm_desktop = self.x11.XInternAtom(self.dpy, b'_NET_WM_DESKTOP', 0)
                self.atom_cardinal = self.x11.XInternAtom(self.dpy, b'CARDINAL', 0)
                self.atom_net_wm_window_type = self.x11.XInternAtom(self.dpy, b'_NET_WM_WINDOW_TYPE', 0)
                self.atom_type_dock = self.x11.XInternAtom(self.dpy, b'_NET_WM_WINDOW_TYPE_DOCK', 0)
                self.atom_type_dialog = self.x11.XInternAtom(self.dpy, b'_NET_WM_WINDOW_TYPE_DIALOG', 0)
                self.atom_type_normal = self.x11.XInternAtom(self.dpy, b'_NET_WM_WINDOW_TYPE_NORMAL', 0)
                self.atom_active_window = self.x11.XInternAtom(self.dpy, b'_NET_ACTIVE_WINDOW', 0)
                self.atom_atom = self.x11.XInternAtom(self.dpy, b'ATOM', 0)

                self.x11.XQueryPointer.restype = ctypes.c_int
                self.x11.XQueryPointer.argtypes = [
                    ctypes.c_void_p, ctypes.c_ulong,
                    ctypes.POINTER(ctypes.c_ulong), ctypes.POINTER(ctypes.c_ulong),
                    ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int),
                    ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int),
                    ctypes.POINTER(ctypes.c_uint)
                ]
                self.r_ret = ctypes.c_ulong()
                self.c_ret = ctypes.c_ulong()
                self.rx = ctypes.c_int()
                self.ry = ctypes.c_int()
                self.wx = ctypes.c_int()
                self.wy = ctypes.c_int()
                self.mask = ctypes.c_uint()
        except Exception as e:
            log(f"X11Helper init error: {e}")
            self.dpy = None

    @classmethod
    def get(cls):
        if cls._instance is None:
            cls._instance = X11Helper()
        return cls._instance

    def move_window(self, xid, x, y):
        if self.dpy and xid:
            try:
                self.x11.XMoveWindow(self.dpy, int(xid), int(x), int(y))
                self.x11.XFlush(self.dpy)
                return True
            except Exception:
                pass
        # Fallback to xdotool if X11 handle is not active
        try:
            subprocess.run(['xdotool', 'windowmove', str(xid), str(int(x)), str(int(y))], check=False)
            return True
        except Exception:
            return False

    def move_resize_window(self, xid, x, y, width, height):
        if self.dpy and xid:
            try:
                # 1. Direct X11 move and resize
                self.x11.XMoveResizeWindow(self.dpy, int(xid), int(x), int(y), int(width), int(height))
                self.x11.XMoveWindow(self.dpy, int(xid), int(x), int(y))

                # 2. EWMH _NET_MOVERESIZE_WINDOW client message to root (commands Mutter to reposition frame)
                atom_mr = self.x11.XInternAtom(self.dpy, b'_NET_MOVERESIZE_WINDOW', 0)
                if atom_mr and self.root:
                    ev = _XEvent()
                    ev.type = 33  # ClientMessage
                    ev.xclient.type = 33
                    ev.xclient.serial = 0
                    ev.xclient.send_event = 1
                    ev.xclient.display = self.dpy
                    ev.xclient.window = int(xid)
                    ev.xclient.message_type = atom_mr
                    ev.xclient.format = 32
                    ev.xclient.data_l[0] = 0x0F01  # bits 8..11 (x, y, w, h), gravity=NorthWest
                    ev.xclient.data_l[1] = int(x)
                    ev.xclient.data_l[2] = int(y)
                    ev.xclient.data_l[3] = int(width)
                    ev.xclient.data_l[4] = int(height)
                    mask = (1 << 20) | (1 << 19)
                    self.x11.XSendEvent(self.dpy, self.root, 0, mask, ctypes.byref(ev))

                self.x11.XFlush(self.dpy)
                return True
            except Exception as e:
                log(f"move_resize_window error: {e}")
        try:
            subprocess.run(['xdotool', 'windowmove', str(xid), str(int(x)), str(int(y))], check=False)
            subprocess.run(['xdotool', 'windowsize', str(xid), str(int(width)), str(int(height))], check=False)
            return True
        except Exception:
            return False

    def unmap_window(self, xid):
        if self.dpy and xid:
            try:
                self.x11.XUnmapWindow(self.dpy, int(xid))
                self.x11.XFlush(self.dpy)
                return True
            except Exception:
                pass
        try:
            subprocess.run(['xdotool', 'windowunmap', str(xid)], check=False)
            return True
        except Exception:
            return False

    def get_pointer(self):
        if self.dpy:
            try:
                ret = self.x11.XQueryPointer(
                    self.dpy, self.root,
                    ctypes.byref(self.r_ret), ctypes.byref(self.c_ret),
                    ctypes.byref(self.rx), ctypes.byref(self.ry),
                    ctypes.byref(self.wx), ctypes.byref(self.wy),
                    ctypes.byref(self.mask)
                )
                if ret:
                    return self.rx.value, self.ry.value
            except Exception:
                pass
        try:
            out = subprocess.check_output(['xdotool', 'getmouselocation'], stderr=subprocess.DEVNULL).decode()
            m = re.search(r'x:(\d+)\s+y:(\d+)', out)
            if m:
                return int(m.group(1)), int(m.group(2))
        except Exception:
            pass
        return None

    def raise_window(self, xid):
        if self.dpy and xid:
            try:
                self.x11.XRaiseWindow(self.dpy, int(xid))
                self.x11.XFlush(self.dpy)
                return True
            except Exception:
                pass
        try:
            subprocess.run(['xdotool', 'windowraise', str(xid)], check=False)
            return True
        except Exception:
            return False

    def activate_window(self, xid):
        """Transfers active keyboard focus to window via EWMH and xdotool."""
        if not xid:
            return False
        if self.dpy and self.root:
            try:
                ixid = int(xid)
                mask = (1 << 20) | (1 << 19)
                ev = _XEvent()
                ev.xclient.type = 33
                ev.xclient.window = ixid
                ev.xclient.message_type = getattr(self, 'atom_active_window', 0)
                ev.xclient.format = 32
                ev.xclient.data_l[0] = 2  # source indication: direct user action
                ev.xclient.data_l[1] = 0
                ev.xclient.data_l[2] = 0
                self.x11.XSendEvent(self.dpy, self.root, 0, mask, ctypes.byref(ev))
                self.x11.XFlush(self.dpy)
            except Exception:
                pass
        try:
            subprocess.run(['xdotool', 'windowactivate', str(xid)], check=False)
            return True
        except Exception:
            return False

    def set_window_type(self, xid, win_type="normal"):
        """Set EWMH window type: 'dock', 'dialog', or 'normal'."""
        if not xid:
            return False
        atom_val_target = getattr(self, f"atom_type_{win_type}", getattr(self, 'atom_type_normal', 0))
        if self.dpy and atom_val_target:
            try:
                ixid = int(xid)
                atom_val = (ctypes.c_ulong * 1)(atom_val_target)
                self.x11.XChangeProperty(
                    self.dpy, ixid, self.atom_net_wm_window_type,
                    self.atom_atom, 32, 0,
                    ctypes.cast(atom_val, ctypes.c_char_p), 1
                )
                self.x11.XFlush(self.dpy)
                return True
            except Exception:
                pass
        try:
            xprop_name = f"_NET_WM_WINDOW_TYPE_{win_type.upper()}"
            subprocess.run(['xprop', '-id', str(xid), '-f', '_NET_WM_WINDOW_TYPE', '32a', '-set', '_NET_WM_WINDOW_TYPE', xprop_name], check=False)
            return True
        except Exception:
            return False

    def set_dock_type(self, xid):
        """Set EWMH window type to DOCK so Mutter/GNOME treats window as a permanent top panel/notch."""
        return self.set_window_type(xid, "dock")

    def make_always_on_top_and_sticky(self, xid, is_dock=False):
        """
        Permanently elevates window into EWMH top layer across ALL virtual desktops and workspaces.
        If is_dock is True, sets _NET_WM_WINDOW_TYPE_DOCK (for permanent top bar / notch).
        If is_dock is False, sets _NET_WM_WINDOW_TYPE_NORMAL so it accepts keyboard focus and typing!
        """
        if not xid:
            return False
        if is_dock:
            self.set_dock_type(xid)
        else:
            self.set_window_type(xid, "normal")
        if self.dpy and self.root:
            try:
                ixid = int(xid)
                mask = (1 << 20) | (1 << 19)  # SubstructureRedirectMask | SubstructureNotifyMask

                # 1. EWMH ClientMessage: Add _NET_WM_STATE_ABOVE and _NET_WM_STATE_STICKY
                ev1 = _XEvent()
                ev1.xclient.type = 33  # ClientMessage
                ev1.xclient.window = ixid
                ev1.xclient.message_type = self.atom_net_wm_state
                ev1.xclient.format = 32
                ev1.xclient.data_l[0] = 1  # _NET_WM_STATE_ADD
                ev1.xclient.data_l[1] = self.atom_state_above
                ev1.xclient.data_l[2] = self.atom_state_sticky
                ev1.xclient.data_l[3] = 1
                self.x11.XSendEvent(self.dpy, self.root, 0, mask, ctypes.byref(ev1))

                # 2. EWMH ClientMessage: Add _NET_WM_STATE_SKIP_TASKBAR and _NET_WM_STATE_SKIP_PAGER
                ev2 = _XEvent()
                ev2.xclient.type = 33
                ev2.xclient.window = ixid
                ev2.xclient.message_type = self.atom_net_wm_state
                ev2.xclient.format = 32
                ev2.xclient.data_l[0] = 1  # _NET_WM_STATE_ADD
                ev2.xclient.data_l[1] = self.atom_state_skip_tb
                ev2.xclient.data_l[2] = self.atom_state_skip_pager
                ev2.xclient.data_l[3] = 1
                self.x11.XSendEvent(self.dpy, self.root, 0, mask, ctypes.byref(ev2))

                # 3. EWMH ClientMessage: Set _NET_WM_DESKTOP = 0xFFFFFFFF (Sticky on all workspaces)
                ev3 = _XEvent()
                ev3.xclient.type = 33
                ev3.xclient.window = ixid
                ev3.xclient.message_type = self.atom_net_wm_desktop
                ev3.xclient.format = 32
                ev3.xclient.data_l[0] = 0xFFFFFFFF
                ev3.xclient.data_l[1] = 1
                self.x11.XSendEvent(self.dpy, self.root, 0, mask, ctypes.byref(ev3))

                # 4. Direct property write for _NET_WM_DESKTOP = 0xFFFFFFFF
                desktop_val = (ctypes.c_ulong * 1)(0xFFFFFFFF)
                self.x11.XChangeProperty(
                    self.dpy, ixid, self.atom_net_wm_desktop,
                    self.atom_cardinal, 32, 0,
                    ctypes.cast(desktop_val, ctypes.c_char_p), 1
                )

                # 5. Immediate raise to topmost z-order
                self.x11.XRaiseWindow(self.dpy, ixid)
                self.x11.XFlush(self.dpy)
                return True
            except Exception as e:
                log(f"make_always_on_top_and_sticky error: {e}")
        return False


DEFAULT_CONFIG = {
    "api_base": "https://openrouter.ai/api/v1",
    "api_key": "",
    "deepseek_api_base": "https://api.deepseek.com",
    "deepseek_api_key": "",
    "deepseek_model": "deepseek-chat",
    "models": [
        "deepseek/deepseek-v4.1-flash",
        "google/gemini-2.5-flash-lite",
        "nvidia/nemotron-3.5-super-vl:free"
    ],
    "system_prompt": "You are Lexi, an intelligent autonomous desktop AI companion and assistant on Arch Linux with FULL SYSTEM ACCESS.\\nYou can directly control the computer, run terminal commands, open applications/files/URLs, control media & hardware, send WhatsApp messages, and analyze screens and documents.\\n\\nCRITICAL ACTION SAFETY RULE:\\n- ONLY emit executable <action> tags when the user gives you a direct, imperative command to perform an action on their system right now (e.g. 'open spotify', 'send hi to mom on whatsapp', 'turn volume up').\\n- If the user is having a casual conversation, discussing features, asking questions, reflecting on past actions, or asking 'why' / 'how' (e.g. 'can you see the last activity', 'we did a great job', 'what do you think?'), you MUST NOT emit any <action> tags. Simply reply in conversational text.\\n- Never emit actions when talking ABOUT an app or topic hypothetically.\\n\\nWhen an action is explicitly commanded, use these <action> tags:\\n1. Shell commands:\\n   <action type=\"command\">ls -la /home/shakeel</action>\\n2. Opening apps, URLs, or files:\\n   <action type=\"open\">google-chrome</action>\\n   <action type=\"open\">https://youtube.com</action>\\n   <action type=\"open\">/home/shakeel/Downloads</action>\\n3. Music & Media Playback:\\n   To play a specific song on Spotify (auto-plays first match):\\n   <action type=\"open\">spotify:search:Artist Song Name</action>\\n   To play a specific song on YouTube:\\n   <action type=\"open\">https://www.youtube.com/results?search_query=Artist+Song+Name</action>\\n   Controls (play/pause/skip current track):\\n   <action type=\"media\" command=\"play\"></action>\\n   <action type=\"media\" command=\"pause\"></action>\\n   <action type=\"media\" command=\"next\"></action>\\n   <action type=\"media\" command=\"previous\"></action>\\n4. WhatsApp automation (only when explicitly asked to send a message):\\n   <action type=\"whatsapp\" contact=\"Mom\">Hi Mom! Hope you are having a wonderful day.</action>\\n   <action type=\"whatsapp\" phone=\"+1234567890\">Hello!</action>\\n   <action type=\"whatsapp\">Hi!</action>\\n5. Volume & Audio:\\n   <action type=\"volume\" level=\"70%\"></action>\\n   <action type=\"volume\" level=\"mute\"></action>\\n6. Screen Brightness:\\n   <action type=\"brightness\" level=\"60%\"></action>\\n7. Keystrokes & Typing:\\n   <action type=\"key\" key=\"Return\"></action>\\n   <action type=\"type\" text=\"hello world\"></action>\\n8. Lock Screen:\\n   <action type=\"lock\"></action>\\n\\nMULTI-STEP / CHAIN COMMANDS:\\nWhen the user explicitly commands multiple actions in one sentence (e.g. 'open spotify and play believer', 'open whatsapp and send hi to John', 'mute volume and lock screen'), emit all requested action tags in order. The desktop engine synchronizes and executes them in sequence.\\n\\nAlways accompany actions with a polite, enthusiastic, concise response explaining what you did. If the user asks a question or asks to analyze attached pages, answer clearly with markdown formatting.",
    "max_image_dimension": 800,
    "image_quality": 65,
    "max_tokens": 2048,
    "request_timeout": 35,
    "character_type": "cat",
    "custom_avatar_path": ""
}

def load_config():
    cfg = dict(DEFAULT_CONFIG)
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                file_cfg = json.load(f)
                cfg.update(file_cfg)
        except Exception as e:
            log(f"Warning loading config: {e}")
    # Allow environment variables as seamless zero-config overrides
    env_or = os.environ.get('OPENROUTER_API_KEY', '').strip()
    if env_or and not cfg.get('api_key'):
        cfg['api_key'] = env_or
    env_ds = os.environ.get('DEEPSEEK_API_KEY', '').strip()
    if env_ds and not cfg.get('deepseek_api_key'):
        cfg['deepseek_api_key'] = env_ds
    return cfg

def save_config(cfg):
    try:
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(cfg, f, indent=4)
        return True
    except Exception as e:
        log(f"Error saving config: {e}")
        return False

def find_top_ledge_target(default_x, default_y=35.0):
    """
    Finds the titlebar or top ledge of an active browser or window.
    Falls back gracefully to the top screen bar (y=35).
    """
    try:
        browser_classes = ['google-chrome', 'chromium', 'firefox', 'brave-browser', 'microsoft-edge', 'zen-alpha', 'zen', 'opera', 'vivaldi']
        for b in browser_classes:
            out = subprocess.check_output(['xdotool', 'search', '--onlyvisible', '--class', b], stderr=subprocess.DEVNULL).decode().strip()
            if out:
                wid = out.split()[-1]
                geom = subprocess.check_output(['xdotool', 'getwindowgeometry', wid], stderr=subprocess.DEVNULL).decode()
                m_pos = re.search(r'Position:\s*(-?\d+),(-?\d+)', geom)
                m_geo = re.search(r'Geometry:\s*(\d+)x(\d+)', geom)
                if m_pos and m_geo:
                    wx, wy = int(m_pos.group(1)), int(m_pos.group(2))
                    ww, wh = int(m_geo.group(1)), int(m_geo.group(2))
                    anchor_x = max(50.0, min(1800.0, float(wx + ww // 2)))
                    anchor_y = max(35.0, float(wy))
                    return anchor_x, anchor_y
    except Exception:
        pass
    return float(default_x), float(default_y)


class SystemActionEngine:
    """
    Autonomous System Action Engine for Lexi Copilot on Linux/Arch Linux.
    Executes real system actions:
    1. Bash / Shell commands with stdout/stderr capture & timeout protection.
    2. Open Applications, URLs, Files, Directories via xdg-open or direct launch.
    3. WhatsApp Automation: Direct chat link for phone numbers, or active window keystroke injection via xdotool for contact names.
    4. Hardware & Media Controls: wpctl / pactl audio volume & mute, brightnessctl display brightness, playerctl media controls.
    5. Window & Desktop Controls: Lock screen, window focus, minimize, keystroke automation.
    """
    _instance = None

    @classmethod
    def get(cls):
        if cls._instance is None:
            cls._instance = SystemActionEngine()
        return cls._instance

    @staticmethod
    def keep_spotify_minimized_async(duration_sec=2.5, interval_sec=0.15):
        """Ensures Spotify remains quietly minimized in the background during automated playback."""
        def _worker():
            iterations = max(1, int(duration_sec / interval_sec))
            for _ in range(iterations):
                time.sleep(interval_sec)
                try:
                    res = subprocess.run(["xdotool", "search", "--class", "spotify"], capture_output=True, text=True)
                    for wid in res.stdout.strip().split():
                        subprocess.run(["xdotool", "windowminimize", wid], capture_output=True)
                except Exception:
                    pass
        threading.Thread(target=_worker, daemon=True).start()

    def __init__(self):
        pass

    def parse_actions(self, text):
        pattern = re.compile(r'<action(?:\s+([^>]*?))?(?:>(.*?)</action>|/>)', re.DOTALL | re.IGNORECASE)
        actions = []
        for m in pattern.finditer(text):
            attrs_str = m.group(1) or ""
            body = (m.group(2) or "").strip()
            attrs = dict(re.findall(r'(\w+)=["\']([^"\']*)["\']', attrs_str))
            act_type = attrs.get('type', '').lower().strip()
            if not act_type and body:
                act_type = 'command'
            actions.append({
                'type': act_type,
                'attrs': attrs,
                'body': body,
                'raw': m.group(0)
            })
        return actions

    def strip_actions(self, text):
        pattern = re.compile(r'<action(?:\s+[^>]*?)?(?:>.*?</action>|/>)', re.DOTALL | re.IGNORECASE)
        cleaned = pattern.sub('', text)
        return re.sub(r'\n{3,}', '\n\n', cleaned).strip()

    def run_command(self, cmd, timeout=15):
        cmd = cmd.strip()
        if not cmd:
            return {"success": False, "returncode": -1, "output": "No command provided"}
        try:
            res = subprocess.run(
                cmd,
                shell=True,
                executable='/bin/bash',
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=timeout
            )
            out_str = (res.stdout + (("\n" + res.stderr) if res.stderr else "")).strip() or "(Executed with no output)"
            return {
                "success": res.returncode == 0,
                "returncode": res.returncode,
                "stdout": res.stdout,
                "stderr": res.stderr,
                "output": out_str
            }
        except subprocess.TimeoutExpired:
            return {
                "success": False,
                "returncode": -1,
                "stdout": "",
                "stderr": f"Command timed out after {timeout} seconds",
                "output": f"⚠️ Command timed out after {timeout}s"
            }
        except Exception as e:
            return {
                "success": False,
                "returncode": -1,
                "stdout": "",
                "stderr": str(e),
                "output": f"⚠️ Execution error: {e}"
            }

    def open_target(self, target):
        target = target.strip()
        if not target:
            return {"success": False, "message": "No target specified"}

        # Spotify native playback via MPRIS OpenUri
        if target.startswith("spotify:") or ("open.spotify.com/" in target):
            spotify_uri = target
            if "open.spotify.com/" in target:
                m = re.search(r'open\.spotify\.com/(track|album|artist|playlist)/([a-zA-Z0-9]+)', target)
                if m:
                    spotify_uri = f"spotify:{m.group(1)}:{m.group(2)}"

            # Ensure Spotify client process is running
            chk = subprocess.run(["playerctl", "-p", "spotify", "status"], capture_output=True, text=True)
            if chk.returncode != 0:
                p = subprocess.run(["pgrep", "-f", "/opt/spotify/spotify"], capture_output=True, text=True)
                if not p.stdout.strip():
                    subprocess.Popen(["spotify"], start_new_session=True)
                for _ in range(25):
                    time.sleep(0.2)
                    if subprocess.run(["playerctl", "-p", "spotify", "status"], capture_output=True, text=True).returncode == 0:
                        break
                time.sleep(0.5)

            # Prevent Spotify from stealing workspace focus or popping up GUI window
            self.keep_spotify_minimized_async(duration_sec=3.0, interval_sec=0.15)

            # Send OpenUri via DBus to load and play directly
            res = subprocess.run([
                "dbus-send", "--print-reply",
                "--dest=org.mpris.MediaPlayer2.spotify",
                "/org/mpris/MediaPlayer2",
                "org.mpris.MediaPlayer2.Player.OpenUri",
                f"string:{spotify_uri}"
            ], capture_output=True, text=True)

            if res.returncode == 0:
                time.sleep(0.3)
                st = subprocess.run(["playerctl", "-p", "spotify", "status"], capture_output=True, text=True).stdout.strip()
                if st != "Playing":
                    subprocess.run(["dbus-send", "--dest=org.mpris.MediaPlayer2.spotify", "/org/mpris/MediaPlayer2", "org.mpris.MediaPlayer2.Player.Play"], capture_output=True)
                track_info = ""
                try:
                    track_info = subprocess.check_output(
                        ["playerctl", "-p", "spotify", "metadata", "--format", "{{ artist }} - {{ title }}"],
                        stderr=subprocess.DEVNULL
                    ).decode().strip()
                except Exception:
                    pass
                msg = f"Playing {track_info}" if track_info else f"Playing on Spotify: {spotify_uri}"
                self.notify("Spotify", msg)
                return {"success": True, "message": msg, "spotify_playing": True}
            else:
                subprocess.Popen(['xdg-open', target], start_new_session=True)
                return {"success": True, "message": f"Opened {target}"}

        # Web URL
        if re.match(r'^(https?://|www\.)', target, re.IGNORECASE):
            url = target if target.startswith('http') else f"https://{target}"
            subprocess.Popen(['xdg-open', url], start_new_session=True)
            self.notify("Lexi Launcher", f"Opened URL: {url}")
            return {"success": True, "message": f"Opened {url}"}

        # Local Path
        expanded = os.path.expanduser(os.path.expandvars(target))
        if os.path.exists(expanded):
            subprocess.Popen(['xdg-open', expanded], start_new_session=True)
            self.notify("Lexi Launcher", f"Opened {os.path.basename(expanded)}")
            return {"success": True, "message": f"Opened path: {expanded}"}

        # App mapping
        APP_ALIASES = {
            'chrome': 'google-chrome',
            'browser': 'google-chrome',
            'google chrome': 'google-chrome',
            'google-chrome': 'google-chrome',
            'chromium': 'chromium',
            'firefox': 'firefox',
            'terminal': 'gnome-terminal',
            'term': 'gnome-terminal',
            'console': 'kgx',
            'files': 'nautilus',
            'nautilus': 'nautilus',
            'thunar': 'thunar',
            'file manager': 'nautilus',
            'code': 'code',
            'vscode': 'code',
            'vs code': 'code',
            'editor': 'gnome-text-editor',
            'text editor': 'gnome-text-editor',
            'spotify': 'spotify',
            'whatsapp': 'https://web.whatsapp.com',
            'whatsapp-for-linux': 'https://web.whatsapp.com',
            'whatsapp web': 'https://web.whatsapp.com',
            'settings': 'gnome-control-center',
            'calculator': 'gnome-calculator',
            'calc': 'gnome-calculator',
            'system monitor': 'gnome-system-monitor',
            'mpv': 'mpv'
        }
        clean = target.lower()
        binary = APP_ALIASES.get(clean, target)

        # Check binary in PATH
        resolved = shutil.which(binary)
        if not resolved:
            # Check desktop files via gtk-launch
            for cand in [clean, f"{clean}.desktop", f"org.gnome.{clean.capitalize()}", f"com.google.{clean.capitalize()}"]:
                desktop_name = cand if cand.endswith('.desktop') else f"{cand}.desktop"
                if os.path.exists(f"/usr/share/applications/{desktop_name}") or os.path.exists(os.path.expanduser(f"~/.local/share/applications/{desktop_name}")):
                    try:
                        subprocess.Popen(['gtk-launch', desktop_name], start_new_session=True)
                        self.notify("Lexi Launcher", f"Launched {clean.capitalize()}")
                        return {"success": True, "message": f"Launched {desktop_name}"}
                    except Exception:
                        pass

        if resolved:
            subprocess.Popen([resolved], start_new_session=True)
            self.notify("Lexi Launcher", f"Launched {os.path.basename(resolved)}")
            return {"success": True, "message": f"Launched {os.path.basename(resolved)}"}

        try:
            subprocess.Popen(['xdg-open', target], start_new_session=True)
            self.notify("Lexi Launcher", f"Opened {target}")
            return {"success": True, "message": f"Opened {target}"}
        except Exception as e:
            return {"success": False, "message": f"Could not launch '{target}': {e}"}

    def send_whatsapp(self, contact="", phone="", message=""):
        clean_contact = (contact or "").strip()
        clean_phone = re.sub(r'[\s\-\(\)]', '', (phone or "").strip())
        msg = (message or "").strip() or "Hi!"
        ydo_env = {**os.environ, "YDOTOOL_SOCKET": "/run/user/1000/.ydotool_socket"}

        # Check local address book if contact name is configured with a phone number
        contacts_file = os.path.join(APP_DIR, 'contacts.json')
        if not clean_phone and os.path.exists(contacts_file):
            try:
                with open(contacts_file, 'r', encoding='utf-8') as f:
                    cdata = json.load(f)
                    if clean_contact.lower() in cdata:
                        clean_phone = cdata[clean_contact.lower()]
            except Exception:
                pass

        # 1. Direct Phone Number URL Mode (Instant direct chat open with pre-filled message)
        if clean_phone or re.match(r'^\+?\d{7,15}$', clean_contact):
            num = clean_phone if clean_phone else clean_contact
            encoded_msg = urllib.parse.quote(msg)
            url = f"https://web.whatsapp.com/send?phone={num}&text={encoded_msg}"
            subprocess.Popen(['google-chrome', url], start_new_session=True)
            self.notify("WhatsApp Automation", f"Opening chat with {num}")

            # Wait for WhatsApp Web to render the chat & message input
            time.sleep(5.0)
            subprocess.Popen(['gtk-launch', 'google-chrome'], start_new_session=True)
            time.sleep(0.6)

            # Send Enter via kernel input (ydotool) and X11 (xdotool)
            subprocess.run(["ydotool", "key", "28:1", "28:0"], env=ydo_env, capture_output=True)
            subprocess.run(["xdotool", "key", "Return"], capture_output=True)

            self.notify("WhatsApp Automation", f"Sent \"{msg}\" to {num}")
            return {"success": True, "message": f"Sent \"{msg}\" to {clean_contact or num} ({num}) on WhatsApp Web"}

        # 2. Contact Name or Active Chat Mode
        # Ensure Chrome with WhatsApp Web is open & brought to front
        subprocess.Popen(['google-chrome', 'https://web.whatsapp.com'], start_new_session=True)
        time.sleep(3.8)
        subprocess.Popen(['gtk-launch', 'google-chrome'], start_new_session=True)
        time.sleep(0.6)

        if clean_contact:
            # Focus search bar (Ctrl+Alt+/)
            subprocess.run(["ydotool", "key", "29:1", "56:1", "53:1", "53:0", "56:0", "29:0"], env=ydo_env, capture_output=True)
            subprocess.run(["xdotool", "key", "ctrl+alt+slash"], capture_output=True)
            time.sleep(0.5)

            # Type contact name
            subprocess.run(["ydotool", "type", "-d", "20", "-D", "20", clean_contact], env=ydo_env, capture_output=True)
            subprocess.run(["xdotool", "type", "--delay", "40", clean_contact], capture_output=True)
            time.sleep(1.2)  # Wait for contact search results to populate

            # Select contact (Enter)
            subprocess.run(["ydotool", "key", "28:1", "28:0"], env=ydo_env, capture_output=True)
            subprocess.run(["xdotool", "key", "Return"], capture_output=True)
            time.sleep(1.0)  # Wait for chat pane to open and message box to gain focus

        # Type message
        subprocess.run(["ydotool", "type", "-d", "20", "-D", "20", msg], env=ydo_env, capture_output=True)
        subprocess.run(["xdotool", "type", "--delay", "30", msg], capture_output=True)
        time.sleep(0.4)

        # Send message (Enter)
        subprocess.run(["ydotool", "key", "28:1", "28:0"], env=ydo_env, capture_output=True)
        subprocess.run(["xdotool", "key", "Return"], capture_output=True)

        dest_info = f"'{clean_contact}'" if clean_contact else "active chat"
        self.notify("WhatsApp Automation", f"Sent \"{msg}\" to {dest_info}")
        return {"success": True, "message": f"Sent \"{msg}\" to {dest_info} on WhatsApp Web"}

    def set_volume(self, level):
        level = (level or "").strip().lower()
        if level in ["mute", "unmute", "toggle"]:
            res = subprocess.run(["wpctl", "set-mute", "@DEFAULT_AUDIO_SINK@", "toggle"], capture_output=True)
            if res.returncode != 0:
                subprocess.run(["pactl", "set-sink-mute", "@DEFAULT_SINK@", "toggle"], capture_output=True)
        else:
            pct_match = re.search(r'([+\-]?\d+)\s*%?', level)
            if pct_match:
                val = int(pct_match.group(1))
                if level.startswith('+') or level.startswith('-'):
                    arg = f"{abs(val)}%{'+' if val > 0 else '-'}"
                    subprocess.run(["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", arg], capture_output=True)
                else:
                    target_val = max(0, min(150, val))
                    subprocess.run(["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", f"{target_val}%"], capture_output=True)
        try:
            cur = subprocess.check_output(["wpctl", "get-volume", "@DEFAULT_AUDIO_SINK@"], stderr=subprocess.DEVNULL).decode().strip()
            cur = cur.replace("Volume: ", "")
        except Exception:
            cur = "Updated"
        self.notify("Audio Volume", f"Volume: {cur}")
        return {"success": True, "message": f"Volume: {cur}"}

    def set_brightness(self, level):
        level = (level or "").strip()
        pct_match = re.search(r'([+\-]?\d+)\s*%?', level)
        if pct_match:
            val = int(pct_match.group(1))
            if level.startswith('+'):
                arg = f"+{val}%"
            elif level.startswith('-'):
                arg = f"{abs(val)}%-"
            else:
                arg = f"{max(5, min(100, val))}%"
            subprocess.run(["brightnessctl", "set", arg], capture_output=True)
        try:
            cur = subprocess.check_output(["brightnessctl", "get"], stderr=subprocess.DEVNULL).decode().strip()
            max_b = subprocess.check_output(["brightnessctl", "max"], stderr=subprocess.DEVNULL).decode().strip()
            pct = int(round(float(cur) / float(max_b) * 100))
            status = f"{pct}%"
        except Exception:
            status = "Updated"
        self.notify("Screen Brightness", f"Brightness: {status}")
        return {"success": True, "message": f"Brightness: {status}"}

    def media_control(self, cmd):
        raw_str = (cmd or "play-pause").strip()

        # If user/LLM requested a specific song via media action, route to Spotify search & play
        play_match = re.match(r'^(?:play|listen to)\s+(.+)$', raw_str, re.IGNORECASE)
        if play_match:
            song_query = play_match.group(1).strip()
            clean_query = re.sub(r'\b(on spotify|in spotify|song|music|track)\b', '', song_query, flags=re.IGNORECASE).strip()
            if clean_query and len(clean_query) > 1 and clean_query.lower() not in ('pause', 'next', 'previous', 'it', 'again', 'now', 'liked songs'):
                return self.open_target(f"spotify:search:{clean_query}")

        raw_cmd = raw_str.lower()

        # Normalize hallucinated commands from LLM (e.g. "play-spotify-liked-songs", "resume", "play music")
        if any(w in raw_cmd for w in ["play", "resume", "start", "unpause", "song", "music"]):
            action = "play"
        elif any(w in raw_cmd for w in ["pause", "hold"]):
            action = "pause"
        elif any(w in raw_cmd for w in ["toggle"]):
            action = "play-pause"
        elif any(w in raw_cmd for w in ["next", "skip"]):
            action = "next"
        elif any(w in raw_cmd for w in ["prev", "back", "previous"]):
            action = "previous"
        elif any(w in raw_cmd for w in ["stop"]):
            action = "stop"
        else:
            action = "play-pause"

        # If action is play or resume, ensure Spotify is running & awakened
        if action in ("play", "play-pause"):
            p = subprocess.run(["pgrep", "-f", "/opt/spotify/spotify"], capture_output=True, text=True)
            if not p.stdout.strip():
                subprocess.Popen(["spotify"], start_new_session=True)
                for _ in range(25):
                    time.sleep(0.2)
                    chk = subprocess.run(["playerctl", "-p", "spotify", "status"], capture_output=True, text=True)
                    if chk.returncode == 0:
                        break
                time.sleep(0.6)

            # Keep Spotify quietly in background
            self.keep_spotify_minimized_async(duration_sec=2.5, interval_sec=0.15)

            # Dismiss promo modal if blocking Spotify
            try:
                w_out = subprocess.run(["xdotool", "search", "--class", "spotify"], capture_output=True, text=True)
                if w_out.stdout.strip():
                    wid = w_out.stdout.strip().split()[-1]
                    subprocess.run(["xdotool", "key", "--window", wid, "Escape"], capture_output=True)
            except Exception:
                pass

            # Send DBus Play directly to Spotify
            subprocess.run(["dbus-send", "--dest=org.mpris.MediaPlayer2.spotify", "/org/mpris/MediaPlayer2", "org.mpris.MediaPlayer2.Player.Play"], capture_output=True)
            time.sleep(0.4)

            # Check status
            chk = subprocess.run(["playerctl", "-p", "spotify", "status"], capture_output=True, text=True)
            if chk.stdout.strip() != "Playing":
                subprocess.run(["dbus-send", "--dest=org.mpris.MediaPlayer2.spotify", "/org/mpris/MediaPlayer2", "org.mpris.MediaPlayer2.Player.PlayPause"], capture_output=True)
                time.sleep(0.4)
                chk = subprocess.run(["playerctl", "-p", "spotify", "status"], capture_output=True, text=True)

            if chk.stdout.strip() != "Playing":
                subprocess.run(["playerctl", "play"], capture_output=True)

        elif action == "pause":
            subprocess.run(["dbus-send", "--dest=org.mpris.MediaPlayer2.spotify", "/org/mpris/MediaPlayer2", "org.mpris.MediaPlayer2.Player.Pause"], capture_output=True)
            subprocess.run(["playerctl", "pause"], capture_output=True)

        elif action == "next":
            subprocess.run(["dbus-send", "--dest=org.mpris.MediaPlayer2.spotify", "/org/mpris/MediaPlayer2", "org.mpris.MediaPlayer2.Player.Next"], capture_output=True)
            subprocess.run(["playerctl", "next"], capture_output=True)

        elif action == "previous":
            subprocess.run(["dbus-send", "--dest=org.mpris.MediaPlayer2.spotify", "/org/mpris/MediaPlayer2", "org.mpris.MediaPlayer2.Player.Previous"], capture_output=True)
            subprocess.run(["playerctl", "previous"], capture_output=True)

        elif action == "stop":
            subprocess.run(["playerctl", "stop"], capture_output=True)

        # Retrieve current playing track info
        track_info = ""
        try:
            track = subprocess.check_output(["playerctl", "metadata", "--format", "{{ artist }} - {{ title }}"], stderr=subprocess.DEVNULL).decode().strip()
            if track:
                track_info = f" ({track})"
        except Exception:
            pass

        try:
            st = subprocess.check_output(["playerctl", "status"], stderr=subprocess.DEVNULL).decode().strip()
        except Exception:
            st = "Active"

        msg = f"Media {action.capitalize()}: {st}{track_info}"
        self.notify("Media Player", msg)
        return {"success": True, "message": msg}

    def send_key(self, key_combo):
        ydo_env = {**os.environ, "YDOTOOL_SOCKET": "/run/user/1000/.ydotool_socket"}
        k_clean = (key_combo or "").lower().strip()
        KEY_MAP = {
            'return': ['28:1', '28:0'],
            'enter': ['28:1', '28:0'],
            'esc': ['1:1', '1:0'],
            'escape': ['1:1', '1:0'],
            'ctrl+alt+slash': ['29:1', '56:1', '53:1', '53:0', '56:0', '29:0'],
            'ctrl+alt+/': ['29:1', '56:1', '53:1', '53:0', '56:0', '29:0'],
            'tab': ['15:1', '15:0'],
            'backspace': ['14:1', '14:0'],
            'space': ['57:1', '57:0'],
        }
        if k_clean in KEY_MAP:
            subprocess.run(["ydotool", "key"] + KEY_MAP[k_clean], env=ydo_env, capture_output=True)
        subprocess.run(["xdotool", "key", key_combo], capture_output=True)
        return {"success": True, "message": f"Pressed: {key_combo}"}

    def send_type(self, text):
        ydo_env = {**os.environ, "YDOTOOL_SOCKET": "/run/user/1000/.ydotool_socket"}
        subprocess.run(["ydotool", "type", "-d", "15", "-D", "15", text], env=ydo_env, capture_output=True)
        subprocess.run(["xdotool", "type", "--delay", "25", text], capture_output=True)
        return {"success": True, "message": f"Typed text ({len(text)} chars)"}

    def lock_screen(self):
        for cmd in [["loginctl", "lock-session"], ["xdg-screensaver", "lock"]]:
            if shutil.which(cmd[0]):
                subprocess.Popen(cmd, start_new_session=True)
                return {"success": True, "message": "Screen locked successfully"}
        return {"success": False, "message": "No screen locker tool found"}

    def notify(self, title, msg):
        try:
            subprocess.Popen(['notify-send', title, msg, '-a', 'Lexi', '--urgency=low'], start_new_session=True)
        except Exception:
            pass

    def execute_action(self, action):
        act_type = action.get('type', 'command').lower().strip()
        attrs = action.get('attrs', {})
        body = action.get('body', '')

        if act_type in ('command', 'bash', 'sh', 'shell', 'terminal'):
            cmd = body or attrs.get('cmd', '')
            return self.run_command(cmd)

        elif act_type in ('open', 'launch', 'run'):
            target = body or attrs.get('target', '') or attrs.get('app', '') or attrs.get('url', '')
            return self.open_target(target)

        elif act_type in ('whatsapp', 'wa'):
            contact = attrs.get('contact') or attrs.get('to') or ''
            phone = attrs.get('phone') or ''
            msg = body or attrs.get('message', '') or attrs.get('text', '')
            return self.send_whatsapp(contact=contact, phone=phone, message=msg)

        elif act_type in ('volume', 'vol', 'audio'):
            level = attrs.get('level') or body or ''
            return self.set_volume(level)

        elif act_type in ('brightness', 'bright'):
            level = attrs.get('level') or body or ''
            return self.set_brightness(level)

        elif act_type in ('media', 'player'):
            cmd = attrs.get('command') or attrs.get('cmd') or body or 'play-pause'
            return self.media_control(cmd)

        elif act_type in ('key', 'hotkey'):
            k = attrs.get('key') or body or ''
            return self.send_key(k)

        elif act_type in ('type', 'write'):
            t = attrs.get('text') or body or ''
            return self.send_type(t)

        elif act_type in ('lock', 'lockscreen'):
            return self.lock_screen()

        else:
            return {"success": False, "message": f"Unknown action type: {act_type}"}

    def execute_action_sequence(self, actions, user_question=""):
        """
        Executes a sequence of actions with intelligent chaining, deduplication,
        intent validation, and process-await synchronization between steps.
        """
        results = []
        if not actions:
            return results

        # Intent Guardrail: prevent hallucinated actions when the user didn't ask for them
        if user_question:
            q_lower = user_question.lower().strip()
            is_question = bool(re.search(r'\b(why|how|explain|what|did|does|is|are|check|see|talk|tell me|can you|can u)\b', q_lower)) or q_lower.endswith('?')
            has_send = bool(re.search(r'\b(send|text|ping|msg)\b', q_lower))
            has_open_app = bool(re.search(r'\b(open|launch)\s+(whatsapp|wa)\b', q_lower))
            is_valid_wa_command = (has_send or has_open_app) and not is_question

            validated = []
            for act in actions:
                act_type = act.get('type', '').lower().strip()
                if act_type in ('whatsapp', 'wa') and not is_valid_wa_command:
                    log(f"Suppressed hallucinated WhatsApp action for conversational/question query: '{user_question}'")
                    continue
                validated.append(act)
            actions = validated
            if not actions:
                return results

        has_whatsapp_action = any(a.get('type') in ('whatsapp', 'wa') for a in actions)

        # Pre-process: detect chained "open whatsapp" + "type" + "key" and synthesize into whatsapp action
        filtered_actions = []
        skip_indices = set()
        for i, act in enumerate(actions):
            if i in skip_indices:
                continue
            act_type = act.get('type', '').lower().strip()
            attrs = act.get('attrs', {})
            body = act.get('body', '').strip()
            target = (attrs.get('target', '') or attrs.get('app', '') or attrs.get('url', '') or body).lower()

            if act_type in ('open', 'launch') and any(w in target for w in ('whatsapp', 'web.whatsapp.com')):
                if not has_whatsapp_action:
                    # Look ahead for a 'type' action
                    type_act = next((a for a in actions[i+1:] if a.get('type') in ('type', 'write')), None)
                    if type_act:
                        msg_to_send = type_act.get('attrs', {}).get('text') or type_act.get('body') or ''
                        filtered_actions.append({'type': 'whatsapp', 'attrs': {}, 'body': msg_to_send})
                        # Skip subsequent type/key actions that were chained to this
                        for j in range(i+1, len(actions)):
                            if actions[j].get('type') in ('type', 'write', 'key'):
                                skip_indices.add(j)
                        continue
                else:
                    # An explicit whatsapp action already exists; skip redundant open
                    continue

            filtered_actions.append(act)

        spotify_already_playing = False
        for i, act in enumerate(filtered_actions):
            act_type = act.get('type', '').lower().strip()
            attrs = act.get('attrs', {})
            body = act.get('body', '').strip()
            target = (attrs.get('target', '') or attrs.get('app', '') or attrs.get('url', '') or body).strip()

            # If a Spotify track was already commanded to play in this sequence, skip redundant generic 'media play'
            if spotify_already_playing and act_type in ('media', 'player'):
                cmd = (attrs.get('command') or attrs.get('cmd') or body or 'play').lower().strip()
                if cmd in ('play', 'resume', 'start', 'play-pause'):
                    results.append((act, {"success": True, "message": "Spotify already playing requested track"}))
                    continue

            # Chained Spotify: if this is plain 'open spotify' (launching app) and there is a subsequent 'media' action,
            # launch Spotify and wait for its MPRIS interface before running the media command
            if act_type in ('open', 'launch') and target.lower() == 'spotify':
                res = self.open_target(target)
                results.append((act, res))
                has_next_media = any(a.get('type') in ('media', 'player') for a in filtered_actions[i+1:])
                if has_next_media:
                    for _ in range(25):
                        chk = subprocess.run(["playerctl", "-p", "spotify", "status"], capture_output=True, text=True)
                        if chk.returncode == 0:
                            break
                        time.sleep(0.2)
                    time.sleep(0.5)
                continue

            # Standard execution
            res = self.execute_action(act)
            if isinstance(res, dict) and res.get('spotify_playing'):
                spotify_already_playing = True
            results.append((act, res))

            # Inter-step delay for desktop compositor and window stabilization
            if i < len(filtered_actions) - 1:
                time.sleep(0.4)

        return results


class AvatarManager:
    """
    Manages character skin selection (Celestial Cat vs Custom User Photo/Avatar)
    and caches anti-aliased Cairo ImageSurfaces for all active mascot widgets.
    """
    _instance = None

    @classmethod
    def get(cls):
        if cls._instance is None:
            cls._instance = AvatarManager()
        return cls._instance

    def __init__(self):
        self.config = load_config()
        self.character_type = self.config.get("character_type", "cat")
        self.custom_avatar_path = self.config.get("custom_avatar_path", "")
        self.registered_mascots = []
        self._surfaces = {}
        self.walk_surfaces = []
        self.is_cutout = False
        if self.custom_avatar_path and os.path.exists(self.custom_avatar_path):
            self._load_surfaces()

    def register(self, mascot):
        if mascot not in self.registered_mascots:
            self.registered_mascots.append(mascot)
            mascot.character_type = self.character_type

    def unregister(self, mascot):
        if mascot in self.registered_mascots:
            self.registered_mascots.remove(mascot)

    def _render_surface_for_size(self, image_path, size):
        img = Image.open(image_path)
        is_transparent = False
        if img.mode in ('RGBA', 'LA') or ('transparency' in img.info):
            rgba = img.convert('RGBA')
            alpha = rgba.split()[-1]
            if alpha.getextrema()[0] < 220:
                is_transparent = True

        if is_transparent:
            if size <= 48:
                w, h = img.size
                head_box = (int(w * 0.15), 0, int(w * 0.85), int(h * 0.50))
                head = img.crop(head_box)
                hw, hh = head.size
                min_d = min(hw, hh)
                head_sq = head.crop(((hw - min_d) // 2, 0, (hw - min_d) // 2 + min_d, min_d))
                head_sq = head_sq.resize((size, size), Image.Resampling.LANCZOS)
                circle_bg = Image.new('RGBA', (size, size), (15, 23, 42, 255))
                mask = Image.new('L', (size * 2, size * 2), 0)
                d = ImageDraw.Draw(mask)
                d.ellipse((0, 0, size * 2, size * 2), fill=255)
                mask = mask.resize((size, size), Image.Resampling.LANCZOS)
                circle_bg.putalpha(mask)
                circle_bg.paste(head_sq, (0, 0), head_sq)
                out = circle_bg
            else:
                rgba = img.convert('RGBA')
                w, h = rgba.size
                aspect = w / float(h)
                if aspect > 1.0:
                    tw = size
                    th = int(size / aspect)
                else:
                    th = size
                    tw = int(size * aspect)
                scaled = rgba.resize((tw, th), Image.Resampling.LANCZOS)
                out = Image.new('RGBA', (size, size), (0, 0, 0, 0))
                offset = ((size - tw) // 2, (size - th) // 2)
                out.paste(scaled, offset, scaled)
        else:
            rgb = img.convert('RGB')
            w, h = rgb.size
            min_dim = min(w, h)
            left = (w - min_dim) // 2
            top = (h - min_dim) // 2
            cropped = rgb.crop((left, top, left + min_dim, top + min_dim))
            cropped = cropped.resize((size, size), Image.Resampling.LANCZOS).convert('RGBA')

            mask = Image.new('L', (size * 2, size * 2), 0)
            draw = ImageDraw.Draw(mask)
            draw.ellipse((0, 0, size * 2, size * 2), fill=255)
            mask = mask.resize((size, size), Image.Resampling.LANCZOS)
            cropped.putalpha(mask)
            out = cropped

        bio = io.BytesIO()
        out.save(bio, format='PNG')
        bio.seek(0)
        return cairo.ImageSurface.create_from_png(bio)

    def _load_surfaces(self):
        self._surfaces.clear()
        self.walk_surfaces.clear()
        if not self.custom_avatar_path or not os.path.exists(self.custom_avatar_path):
            return
        try:
            with Image.open(self.custom_avatar_path) as img:
                if img.mode in ('RGBA', 'LA') or ('transparency' in img.info):
                    alpha = img.convert('RGBA').split()[-1]
                    self.is_cutout = bool(alpha.getextrema()[0] < 220)
                else:
                    self.is_cutout = False
            for sz in (28, 48, 120, 160):
                self._surfaces[sz] = self._render_surface_for_size(self.custom_avatar_path, sz)

            # Load multi-frame walk cycle if available
            frames_dir = os.path.join(APP_DIR, "frames")
            if os.path.exists(frames_dir):
                for fi in range(6):
                    fp = os.path.join(frames_dir, f"walk_{fi}.png")
                    if os.path.exists(fp):
                        try:
                            self.walk_surfaces.append(cairo.ImageSurface.create_from_png(fp))
                        except Exception:
                            pass
        except Exception as e:
            log(f"Error rendering avatar surfaces: {e}")

    def get_surface(self, is_mini=False, frame_index=0, is_walking=False):
        if self.character_type != "custom":
            return None
        if is_mini:
            return self._surfaces.get(28)
        if is_walking and self.walk_surfaces:
            return self.walk_surfaces[frame_index % len(self.walk_surfaces)]
        target_sz = 160
        if target_sz in self._surfaces:
            return self._surfaces[target_sz]
        if self.custom_avatar_path and os.path.exists(self.custom_avatar_path):
            surf = self._render_surface_for_size(self.custom_avatar_path, target_sz)
            self._surfaces[target_sz] = surf
            return surf
        return None

    def set_character(self, character_type, avatar_path=None):
        self.character_type = character_type
        if avatar_path is not None and os.path.exists(avatar_path):
            ext = os.path.splitext(avatar_path)[1]
            dest = os.path.join(APP_DIR, f"custom_avatar{ext}")
            try:
                if os.path.abspath(avatar_path) != os.path.abspath(dest):
                    shutil.copy2(avatar_path, dest)
                self.custom_avatar_path = dest
            except Exception:
                self.custom_avatar_path = avatar_path
            self._load_surfaces()

        self.config["character_type"] = self.character_type
        self.config["custom_avatar_path"] = self.custom_avatar_path
        save_config(self.config)

        for mascot in list(self.registered_mascots):
            try:
                mascot.character_type = self.character_type
                mascot.queue_draw()
            except Exception:
                pass

SYNTAX_KEYWORDS = {
    'def', 'class', 'import', 'from', 'return', 'if', 'else', 'elif', 'for', 'while',
    'try', 'except', 'finally', 'with', 'as', 'lambda', 'yield', 'async', 'await',
    'function', 'const', 'let', 'var', 'fn', 'pub', 'struct', 'enum', 'impl',
    'sudo', 'pacman', 'systemctl', 'git', 'echo', 'cat', 'grep', 'curl', 'python'
}

def create_markdown_tag_table():
    table = Gtk.TextTagTable()
    tags = [
        ('h1', {'foreground': '#ffffff', 'weight': Pango.Weight.BOLD, 'scale': 1.25}),
        ('h2', {'foreground': '#f4f4f5', 'weight': Pango.Weight.BOLD, 'scale': 1.12}),
        ('h3', {'foreground': '#e4e4e7', 'weight': Pango.Weight.BOLD, 'scale': 1.05}),
        ('bold', {'weight': Pango.Weight.BOLD, 'foreground': '#ffffff'}),
        ('italic', {'style': Pango.Style.ITALIC, 'foreground': '#d4d4d8'}),
        ('bold_italic', {'weight': Pango.Weight.BOLD, 'style': Pango.Style.ITALIC, 'foreground': '#ffffff'}),
        ('code_inline', {'family': 'monospace', 'background': '#1c1d22', 'foreground': '#e2e8f0'}),
        ('code_block', {'family': 'monospace', 'background': '#111215', 'foreground': '#e4e4e7', 'left-margin': 16, 'right-margin': 16}),
        ('code_keyword', {'family': 'monospace', 'foreground': '#93c5fd', 'weight': Pango.Weight.BOLD}),
        ('code_string', {'family': 'monospace', 'foreground': '#86efac'}),
        ('code_comment', {'family': 'monospace', 'foreground': '#71717a', 'style': Pango.Style.ITALIC}),
        ('code_number', {'family': 'monospace', 'foreground': '#fde047'}),
        ('code_header', {'family': 'monospace', 'foreground': '#71717a', 'scale': 0.82, 'background': '#0c0d10', 'left-margin': 16, 'right-margin': 16}),
        ('bullet_symbol', {'foreground': '#a1a1aa', 'weight': Pango.Weight.BOLD}),
        ('bullet_text', {'left-margin': 14}),
        ('user_header', {'foreground': '#ffffff', 'weight': Pango.Weight.BOLD, 'scale': 1.05}),
        ('lexi_header', {'foreground': '#34d399', 'weight': Pango.Weight.BOLD, 'scale': 1.05}),
        ('divider', {'foreground': '#27272a'}),
        ('quote', {'style': Pango.Style.ITALIC, 'foreground': '#a1a1aa', 'left-margin': 18}),
        ('action_title', {'family': 'monospace', 'foreground': '#38bdf8', 'weight': Pango.Weight.BOLD, 'scale': 1.05}),
        ('action_cmd', {'family': 'monospace', 'foreground': '#fcd34d', 'weight': Pango.Weight.BOLD, 'left-margin': 12}),
        ('action_body', {'family': 'monospace', 'background': '#0d0e11', 'foreground': '#e4e4e7', 'left-margin': 14, 'right-margin': 14}),
        ('action_success', {'family': 'monospace', 'foreground': '#34d399', 'weight': Pango.Weight.BOLD, 'left-margin': 12}),
        ('action_error', {'family': 'monospace', 'foreground': '#f87171', 'weight': Pango.Weight.BOLD, 'left-margin': 12})
    ]
    for name, props in tags:
        tag = Gtk.TextTag.new(name)
        for k, v in props.items():
            tag.set_property(k, v)
        table.add(tag)
    return table

def parse_inline(text):
    pattern = re.compile(r'(`[^`]+`|\*\*\*[^*]+\*\*\*|\*\*[^*]+\*\*|\*[^*]+\*|__[^_]+__|(?<!\w)_[^_]+_(?!\w))')
    pos = 0
    tokens = []
    for m in pattern.finditer(text):
        start, end = m.span()
        if start > pos:
            tokens.append(('plain', text[pos:start]))
        match_str = m.group(0)
        if match_str.startswith('`') and match_str.endswith('`'):
            tokens.append(('code_inline', match_str[1:-1]))
        elif match_str.startswith('***') and match_str.endswith('***'):
            tokens.append(('bold_italic', match_str[3:-3]))
        elif (match_str.startswith('**') and match_str.endswith('**')) or (match_str.startswith('__') and match_str.endswith('__')):
            tokens.append(('bold', match_str[2:-2]))
        elif (match_str.startswith('*') and match_str.endswith('*')) or (match_str.startswith('_') and match_str.endswith('_')):
            tokens.append(('italic', match_str[1:-1]))
        pos = end
    if pos < len(text):
        tokens.append(('plain', text[pos:]))
    return tokens

def highlight_code_line(line):
    comment_idx = -1
    for c_marker in ('#', '//'):
        idx = line.find(c_marker)
        if idx != -1:
            if comment_idx == -1 or idx < comment_idx:
                comment_idx = idx
    if comment_idx != -1:
        before_comment = line[:comment_idx]
        comment_text = line[comment_idx:]
    else:
        before_comment = line
        comment_text = None

    tokens = []
    pattern = re.compile(r'("(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|\b[A-Za-z_]\w*\b|\b\d+\b)')
    pos = 0
    for m in pattern.finditer(before_comment):
        start, end = m.span()
        if start > pos:
            tokens.append(('code_block', before_comment[pos:start]))
        word = m.group(0)
        if (word.startswith('"') and word.endswith('"')) or (word.startswith("'") and word.endswith("'")):
            tokens.append(('code_string', word))
        elif word in SYNTAX_KEYWORDS:
            tokens.append(('code_keyword', word))
        elif word.isdigit():
            tokens.append(('code_number', word))
        else:
            tokens.append(('code_block', word))
        pos = end
    if pos < len(before_comment):
        tokens.append(('code_block', before_comment[pos:]))

    if comment_text:
        tokens.append(('code_comment', comment_text))

    return tokens

def render_markdown_to_buffer(buffer, text):
    lines = text.split('\n')
    in_code_block = False
    code_lang = ""
    code_lines = []

    for line in lines:
        if line.strip().startswith('```'):
            if not in_code_block:
                in_code_block = True
                code_lang = line.strip()[3:].strip()
                code_lines = []
            else:
                in_code_block = False
                if code_lang:
                    buffer.insert_with_tags_by_name(buffer.get_end_iter(), f"  [{code_lang}]\n", 'code_header')
                for cline in code_lines:
                    hl_tokens = highlight_code_line(cline)
                    for tag_name, text_chunk in hl_tokens:
                        if tag_name == 'code_block':
                            buffer.insert_with_tags_by_name(buffer.get_end_iter(), text_chunk, 'code_block')
                        else:
                            buffer.insert_with_tags_by_name(buffer.get_end_iter(), text_chunk, 'code_block', tag_name)
                    buffer.insert_with_tags_by_name(buffer.get_end_iter(), '\n', 'code_block')
                buffer.insert(buffer.get_end_iter(), '\n')
            continue

        if in_code_block:
            code_lines.append(line)
            continue

        stripped = line.strip()
        if stripped.startswith('### '):
            buffer.insert_with_tags_by_name(buffer.get_end_iter(), f"{stripped[4:]}\n", 'h3')
        elif stripped.startswith('## '):
            buffer.insert_with_tags_by_name(buffer.get_end_iter(), f"{stripped[3:]}\n", 'h2')
        elif stripped.startswith('# '):
            buffer.insert_with_tags_by_name(buffer.get_end_iter(), f"{stripped[2:]}\n", 'h1')
        elif stripped.startswith('> '):
            buffer.insert_with_tags_by_name(buffer.get_end_iter(), f"{stripped[2:]}\n", 'quote')
        elif stripped.startswith('- ') or stripped.startswith('* ') or stripped.startswith('+ '):
            buffer.insert_with_tags_by_name(buffer.get_end_iter(), "  • ", 'bullet_symbol')
            for tag_type, chunk in parse_inline(stripped[2:]):
                if tag_type == 'plain':
                    buffer.insert_with_tags_by_name(buffer.get_end_iter(), chunk, 'bullet_text')
                else:
                    buffer.insert_with_tags_by_name(buffer.get_end_iter(), chunk, tag_type, 'bullet_text')
            buffer.insert(buffer.get_end_iter(), '\n')
        elif re.match(r'^\d+\.\s', stripped):
            m = re.match(r'^(\d+\.\s)(.*)', stripped)
            prefix, content = m.group(1), m.group(2)
            buffer.insert_with_tags_by_name(buffer.get_end_iter(), f"  {prefix}", 'bold')
            for tag_type, chunk in parse_inline(content):
                if tag_type == 'plain':
                    buffer.insert(buffer.get_end_iter(), chunk)
                else:
                    buffer.insert_with_tags_by_name(buffer.get_end_iter(), chunk, tag_type)
            buffer.insert(buffer.get_end_iter(), '\n')
        elif stripped in ('---', '***'):
            buffer.insert_with_tags_by_name(buffer.get_end_iter(), "────────────────────────────────────────\n", 'divider')
        elif stripped == '':
            buffer.insert(buffer.get_end_iter(), '\n')
        else:
            for tag_type, chunk in parse_inline(line):
                if tag_type == 'plain':
                    buffer.insert(buffer.get_end_iter(), chunk)
                else:
                    buffer.insert_with_tags_by_name(buffer.get_end_iter(), chunk, tag_type)
            buffer.insert(buffer.get_end_iter(), '\n')


def render_action_result_to_buffer(buffer, action, result):
    act_type = action.get('type', 'command').lower().strip()
    attrs = action.get('attrs', {})
    body = action.get('body', '')
    success = result.get('success', True)

    buffer.insert(buffer.get_end_iter(), "\n")
    buffer.insert_with_tags_by_name(buffer.get_end_iter(), f"⚡ System Action: {act_type.upper()}\n", "action_title")

    if act_type in ('command', 'bash', 'sh', 'shell', 'terminal'):
        cmd_text = body or attrs.get('cmd', '')
        buffer.insert_with_tags_by_name(buffer.get_end_iter(), f"$ {cmd_text}\n", "action_cmd")
        output = result.get('output', '')
        if output:
            if len(output) > 3000:
                output = output[:3000] + "\n... (output truncated)"
            buffer.insert_with_tags_by_name(buffer.get_end_iter(), f"{output}\n", "action_body")
        if success:
            buffer.insert_with_tags_by_name(buffer.get_end_iter(), f"✅ Exit code {result.get('returncode', 0)}\n", "action_success")
        else:
            buffer.insert_with_tags_by_name(buffer.get_end_iter(), f"❌ Failed (exit code {result.get('returncode', 1)})\n", "action_error")

    elif act_type in ('open', 'launch', 'run'):
        target = body or attrs.get('target', '') or attrs.get('app', '') or attrs.get('url', '')
        buffer.insert_with_tags_by_name(buffer.get_end_iter(), f"🚀 Target: {target}\n", "action_cmd")
        msg = result.get('message', 'Target opened')
        if success:
            buffer.insert_with_tags_by_name(buffer.get_end_iter(), f"✅ {msg}\n", "action_success")
        else:
            buffer.insert_with_tags_by_name(buffer.get_end_iter(), f"❌ {msg}\n", "action_error")

    elif act_type in ('whatsapp', 'wa'):
        contact = attrs.get('contact') or attrs.get('to') or ''
        phone = attrs.get('phone') or ''
        msg_text = body or attrs.get('message', '') or attrs.get('text', '')
        target_info = f"Contact: {contact}" if contact else (f"Phone: {phone}" if phone else "WhatsApp Web")
        buffer.insert_with_tags_by_name(buffer.get_end_iter(), f"💬 {target_info}\n", "action_cmd")
        if msg_text:
            buffer.insert_with_tags_by_name(buffer.get_end_iter(), f"📝 \"{msg_text}\"\n", "action_body")
        res_msg = result.get('message', 'WhatsApp action initiated')
        buffer.insert_with_tags_by_name(buffer.get_end_iter(), f"✅ {res_msg}\n", "action_success")

    elif act_type in ('volume', 'vol', 'audio'):
        lvl = attrs.get('level') or body or ''
        buffer.insert_with_tags_by_name(buffer.get_end_iter(), f"🔊 Level: {lvl or 'toggle'}\n", "action_cmd")
        buffer.insert_with_tags_by_name(buffer.get_end_iter(), f"✅ {result.get('message', 'Volume updated')}\n", "action_success")

    elif act_type in ('brightness', 'bright'):
        lvl = attrs.get('level') or body or ''
        buffer.insert_with_tags_by_name(buffer.get_end_iter(), f"☀️ Brightness: {lvl}\n", "action_cmd")
        buffer.insert_with_tags_by_name(buffer.get_end_iter(), f"✅ {result.get('message', 'Brightness updated')}\n", "action_success")

    elif act_type in ('media', 'player'):
        cmd = attrs.get('command') or attrs.get('cmd') or body or 'play-pause'
        buffer.insert_with_tags_by_name(buffer.get_end_iter(), f"⏯️ Command: {cmd}\n", "action_cmd")
        buffer.insert_with_tags_by_name(buffer.get_end_iter(), f"✅ {result.get('message', 'Media command sent')}\n", "action_success")

    elif act_type in ('key', 'hotkey', 'type', 'write'):
        k = attrs.get('key') or attrs.get('text') or body
        buffer.insert_with_tags_by_name(buffer.get_end_iter(), f"⌨️ {act_type.title()}: {k}\n", "action_cmd")
        buffer.insert_with_tags_by_name(buffer.get_end_iter(), f"✅ {result.get('message', 'Keystroke sent')}\n", "action_success")

    elif act_type in ('lock', 'lockscreen'):
        buffer.insert_with_tags_by_name(buffer.get_end_iter(), "🔒 Locking session...\n", "action_cmd")
        buffer.insert_with_tags_by_name(buffer.get_end_iter(), f"✅ {result.get('message', 'Locked')}\n", "action_success")

    else:
        buffer.insert_with_tags_by_name(buffer.get_end_iter(), f"⚙️ Action: {act_type}\n", "action_cmd")
        buffer.insert_with_tags_by_name(buffer.get_end_iter(), f"✅ {result.get('message', 'Action completed')}\n", "action_success")

    buffer.insert(buffer.get_end_iter(), "\n")


def draw_star(cr, cx, cy, r_outer, r_inner, points=4):
    cr.save()
    cr.move_to(cx, cy - r_outer)
    for i in range(points * 2):
        angle = i * math.pi / points - math.pi / 2
        r = r_outer if i % 2 == 0 else r_inner
        cr.line_to(cx + r * math.cos(angle), cy + r * math.sin(angle))
    cr.close_path()
    cr.restore()

def draw_heart(cr, cx, cy, size):
    cr.save()
    cr.translate(cx, cy)
    s = size / 10.0
    cr.move_to(0, 3 * s)
    cr.curve_to(-5 * s, -3 * s, -10 * s, 1 * s, 0, 9 * s)
    cr.curve_to(10 * s, 1 * s, 5 * s, -3 * s, 0, 3 * s)
    cr.close_path()
    cr.restore()

def draw_z(cr, cx, cy, size):
    cr.save()
    cr.set_line_width(max(1.0, size * 0.22))
    cr.move_to(cx - size * 0.4, cy - size * 0.4)
    cr.line_to(cx + size * 0.4, cy - size * 0.4)
    cr.line_to(cx - size * 0.4, cy + size * 0.4)
    cr.line_to(cx + size * 0.4, cy + size * 0.4)
    cr.stroke()
    cr.restore()


class LexiMascotWidget(Gtk.DrawingArea):
    """
    Handcrafted Celestial Screen Spirit (Lexi) - Full-body Anime Chibi Kitten/Fox
    with expressive anime eyes, perky ears, working hands/paws, sitting feet with
    pink toe beans, swaying fluffy tail, dynamic mouse gaze tracking, and active working animations.
    """
    def __init__(self, width=130, height=145, is_mini=False):
        super().__init__()
        self.is_mini = is_mini
        self.mascot_w = width
        self.mascot_h = height
        self.set_content_width(width)
        self.set_content_height(height)
        if not is_mini:
            self.add_css_class('lexi-mascot-area')
        else:
            self.add_css_class('dynamic-island-mascot')
        self.set_draw_func(self.on_draw)

        # Gaze & Eye position (-1.0 to 1.0)
        self.look_x = 0.0
        self.look_y = 0.0
        self.target_look_x = 0.0
        self.target_look_y = 0.0

        # Animation states
        self.mood = "normal"  # "normal", "hungry", "eating", "happy", "working", "thinking"
        self.is_blinking = False
        self.squish_x = 1.0
        self.squish_y = 1.0
        self.ear_wiggle = 0.0
        self.target_ear_wiggle = 0.0
        self.think_angle = 0.0
        self.paw_anim = 0.0
        self.tail_phase = 0.0
        self.eat_phase = 0.0
        self.eat_progress = 0.0

        # Autonomous Walking & Stride physics
        self.walk_phase = 0.0
        self.walk_x = 0.0
        self.facing = 1.0
        self.is_walking = True
        self.walk_pause_timer = 0

        # Character customization skin (Cat vs Custom Photo)
        self.character_type = "cat"
        AvatarManager.get().register(self)

        # Stunt & Acrobatics system
        self.flip_angle = 0.0          # 0 to 2*pi for somersault backflip / upside-down hang
        self.perch_swing = 0.0         # playful leg swing while sitting on ledge/titlebar
        self.web_active = False        # whether spider web silk tether is active
        self.web_anchor_x = 0.0        # screen/world coordinates of web anchor
        self.web_anchor_y = 35.0
        self.web_local_anchor_x = 0.0  # widget-relative coordinates for cairo drawing
        self.web_local_anchor_y = 0.0

        # Playful Activity & Workout system
        self.activity = "walk"  # "walk", "spiderweb", "perch", "hang_web", "fly", "backflip", etc.
        self.is_pet_mode = False
        self.activity_timer = 0
        self.jump_y = 0.0
        self.jump_vy = 0.0
        self.arm_spread = 0.0
        self.feet_spread = 0.0
        self.stretch_amount = 0.0
        self.punch_l = 0.0
        self.punch_r = 0.0
        self.particles = []
        self.star_orb_active = False
        self.star_orb_x = 0.0
        self.star_orb_y = 0.0
        self.star_orb_t = 0.0
        self.activity_callback = None
        self.play_cycle_index = 0

        # Direct click gesture to pet Lexi
        click = Gtk.GestureClick()
        click.connect('pressed', lambda *_: self.pet())
        self.add_controller(click)

        # Periodic timers for blinking and frame updates
        self.blink_timer = GLib.timeout_add(3800, self.trigger_blink)
        self.anim_timer = GLib.timeout_add(30, self.on_anim_frame) if not self.is_mini else None

    def spawn_particle(self, x, y, ptype='star', count=1, color=None):
        for _ in range(count):
            angle = random.uniform(0, 2 * math.pi)
            speed = random.uniform(1.2, 3.8)
            col = color or random.choice([
                (1.0, 0.84, 0.0),    # gold
                (0.75, 0.52, 0.98),  # purple
                (0.38, 0.85, 0.98),  # cyan
                (1.0, 0.45, 0.75),   # pink
            ])
            self.particles.append({
                'x': x + random.uniform(-4, 4),
                'y': y + random.uniform(-4, 4),
                'vx': math.cos(angle) * speed,
                'vy': math.sin(angle) * speed - 1.2,
                'size': random.uniform(4.0, 8.5),
                'color': col,
                'alpha': 1.0,
                'type': ptype
            })

    def trigger_spiderweb(self, target_x=None, target_y=None):
        self.activity = "spiderweb"
        self.activity_timer = 0
        self.star_orb_active = False
        self.web_active = True
        self.flip_angle = 0.0
        if target_x is not None:
            self.web_anchor_x = float(target_x)
        if target_y is not None:
            self.web_anchor_y = float(target_y)
        if not self.is_pet_mode:
            self.web_local_anchor_x = self.mascot_w / 2.0
            self.web_local_anchor_y = 6.0
        self.spawn_particle(self.walk_x, -10, 'sparkle', 6, color=(0.4, 0.9, 1.0))
        if self.activity_callback:
            self.activity_callback("🕸️ *Shooting spider-web!* Grappling to top ledge!")

    def trigger_perch(self):
        self.activity = "perch"
        self.activity_timer = 0
        self.star_orb_active = False
        self.web_active = False
        self.flip_angle = 0.0
        self.jump_y = 0.0
        self.squish_y = 0.94
        self.spawn_particle(self.walk_x, 15, 'sparkle', 4, color=(1.0, 0.85, 0.4))
        if self.activity_callback:
            self.activity_callback("🧘 *Perched on top ledge!* Watching desktop...")

    def trigger_hang_web(self):
        self.activity = "hang_web"
        self.activity_timer = 0
        self.star_orb_active = False
        self.web_active = True
        self.flip_angle = math.pi
        self.spawn_particle(self.walk_x, -15, 'star', 4, color=(0.85, 0.55, 1.0))
        if self.activity_callback:
            self.activity_callback("🤸 *Dangling upside-down on silk web!* ( ˙꒳​˙ )")

    def trigger_flight(self):
        self.activity = "fly"
        self.activity_timer = 0
        self.star_orb_active = False
        self.web_active = False
        self.flip_angle = self.facing * 0.42
        self.spawn_particle(self.walk_x, 0, 'star', 6, color=(0.4, 0.85, 1.0))
        if self.activity_callback:
            self.activity_callback("🚀 *Superhero flight!* Swooping across screen!")

    def trigger_backflip(self):
        self.activity = "backflip"
        self.activity_timer = 0
        self.star_orb_active = False
        self.web_active = False
        self.spawn_particle(self.walk_x, 10, 'star', 6, color=(1.0, 0.8, 0.3))
        if self.activity_callback:
            self.activity_callback("🔄 *Acrobatic 360° Backflip!*")

    def trigger_walk(self):
        self.activity = "walk"
        self.activity_timer = 0
        self.star_orb_active = False
        self.web_active = False
        self.flip_angle = 0.0
        self.jump_y = 0.0
        self.is_walking = True
        if self.activity_callback:
            self.activity_callback("🐾 *Strolling happily...*")

    def trigger_jump(self):
        self.activity = "jump"
        self.activity_timer = 0
        self.star_orb_active = False
        self.jump_vy = 0.0
        self.jump_y = 0.0
        if self.activity_callback:
            self.activity_callback("🐾 *Preparing to LEAP!*")

    def trigger_exercise(self):
        self.activity = "jacks"
        self.activity_timer = 0
        self.star_orb_active = False
        if self.activity_callback:
            self.activity_callback("💪 Starting jumping jacks!")

    def trigger_stretch(self):
        self.activity = "stretch"
        self.activity_timer = 0
        self.star_orb_active = False
        if self.activity_callback:
            self.activity_callback("🐱 Big morning stretch...")

    def trigger_boxing(self):
        self.activity = "punch"
        self.activity_timer = 0
        self.star_orb_active = False
        if self.activity_callback:
            self.activity_callback("🥊 Karate paws workout!")

    def trigger_stardust(self):
        self.activity = "stardust"
        self.activity_timer = 0
        self.star_orb_active = True
        self.star_orb_t = 0.0
        if self.activity_callback:
            self.activity_callback("✨ Chasing celestial stardust!")

    def trigger_loaf(self):
        self.activity = "loaf"
        self.activity_timer = 0
        self.star_orb_active = False
        if self.activity_callback:
            self.activity_callback("💤 Cozy nap... zZ")

    def cycle_next_activity(self):
        modes = ["spiderweb", "perch", "hang_web", "fly", "backflip", "jump", "jacks", "stretch", "punch", "stardust", "loaf"]
        next_mode = modes[self.play_cycle_index % len(modes)]
        self.play_cycle_index += 1
        if next_mode == "spiderweb": self.trigger_spiderweb()
        elif next_mode == "perch": self.trigger_perch()
        elif next_mode == "hang_web": self.trigger_hang_web()
        elif next_mode == "fly": self.trigger_flight()
        elif next_mode == "backflip": self.trigger_backflip()
        elif next_mode == "jump": self.trigger_jump()
        elif next_mode == "jacks": self.trigger_exercise()
        elif next_mode == "stretch": self.trigger_stretch()
        elif next_mode == "punch": self.trigger_boxing()
        elif next_mode == "stardust": self.trigger_stardust()
        elif next_mode == "loaf": self.trigger_loaf()

    def trigger_blink(self):
        if self.mood == "normal" and not self.is_blinking:
            self.is_blinking = True
            self.queue_draw()
            GLib.timeout_add(160, self.finish_blink)
        return GLib.SOURCE_CONTINUE

    def finish_blink(self):
        self.is_blinking = False
        self.queue_draw()
        return GLib.SOURCE_REMOVE

    def set_gaze(self, lx, ly):
        self.target_look_x = max(-1.0, min(1.0, lx))
        self.target_look_y = max(-1.0, min(1.0, ly))

    def on_anim_frame(self):
        if not self.get_mapped():
            return GLib.SOURCE_CONTINUE

        if getattr(self, 'is_mini', False):
            # In resting Dynamic Island, sleep completely unless blinking or reacting
            needs_anim = (
                self.is_blinking or
                abs(self.target_ear_wiggle - self.ear_wiggle) > 0.01 or
                abs(self.squish_x - 1.0) > 0.01 or
                bool(self.particles)
            )
            if not needs_anim:
                return GLib.SOURCE_CONTINUE

        diff_x = self.target_look_x - self.look_x
        diff_y = self.target_look_y - self.look_y
        self.look_x += diff_x * 0.22
        self.look_y += diff_y * 0.22

        self.squish_x += (1.0 - self.squish_x) * 0.16
        self.squish_y += (1.0 - self.squish_y) * 0.16
        self.ear_wiggle += (self.target_ear_wiggle - self.ear_wiggle) * 0.18

        self.tail_phase = (self.tail_phase + 0.08) % (2 * math.pi)

        # Update particles
        alive_particles = []
        for p in self.particles:
            p['x'] += p['vx']
            p['y'] += p['vy']
            p['alpha'] -= 0.035
            p['size'] = max(0.5, p['size'] * 0.96)
            if p['alpha'] > 0.05:
                alive_particles.append(p)
        self.particles = alive_particles

        s = min(self.mascot_w / 125.0, self.mascot_h / 125.0) if self.is_pet_mode else min(self.mascot_w / 110.0, self.mascot_h / 130.0)

        # Playful Activities & Autonomous Routine
        if self.mood == "normal":
            if self.activity == "walk":
                if self.is_walking:
                    self.walk_phase = (self.walk_phase + 0.16) % (2 * math.pi)
                    if not self.is_pet_mode:
                        self.walk_x += self.facing * 0.48
                        if self.walk_x > 20.0:
                            self.walk_x = 20.0
                            self.is_walking = False
                            self.walk_pause_timer = 50
                            self.facing = -1.0
                        elif self.walk_x < -20.0:
                            self.walk_x = -20.0
                            self.is_walking = False
                            self.walk_pause_timer = 50
                            self.facing = 1.0
                    else:
                        self.walk_x = 0.0
                        self.walk_timer = getattr(self, 'walk_timer', 0) + 1
                        if self.walk_timer > 140:
                            self.walk_timer = 0
                            self.is_walking = False
                            self.walk_pause_timer = 50
                    self.queue_draw()
                else:
                    self.walk_pause_timer -= 1
                    if self.walk_pause_timer <= 0:
                        if self.is_pet_mode and random.random() < 0.65:
                            self.cycle_next_activity()
                        else:
                            self.is_walking = True
                        self.queue_draw()

            elif self.activity == "spiderweb":
                self.activity_timer += 1
                self.web_active = True
                self.arm_spread = min(1.0, self.activity_timer / 10.0)
                if self.activity_timer % 4 == 0:
                    self.spawn_particle(self.walk_x, -16 * s, 'sparkle', 1, color=(0.4, 0.9, 1.0))
                if not self.is_pet_mode:
                    self.jump_y += (-18 * s - self.jump_y) * 0.16
                    if self.activity_timer >= 32:
                        self.trigger_perch()
                self.queue_draw()

            elif self.activity == "perch":
                self.activity_timer += 1
                self.web_active = False
                self.perch_swing = math.sin(self.activity_timer * 0.14) * 0.28
                self.squish_y = 0.95 + 0.03 * math.sin(self.activity_timer * 0.10)
                self.squish_x = 1.05 - 0.02 * math.sin(self.activity_timer * 0.10)
                self.target_look_x = math.sin(self.activity_timer * 0.05) * 0.65
                self.target_look_y = 0.45
                if self.activity_timer % 32 == 0:
                    self.spawn_particle(self.walk_x, 16 * s, 'sparkle', 1, color=(1.0, 0.85, 0.4))
                if not self.is_pet_mode:
                    self.jump_y = -18 * s
                    if self.activity_timer >= 120:
                        self.trigger_hang_web()
                self.queue_draw()

            elif self.activity == "hang_web":
                self.activity_timer += 1
                self.web_active = True
                pendulum = math.sin(self.activity_timer * 0.10) * 0.22
                self.flip_angle = math.pi + pendulum
                self.arm_spread = 0.4 + 0.15 * math.sin(self.activity_timer * 0.12)
                if self.activity_timer % 5 == 0:
                    self.spawn_particle(self.walk_x, -10 * s, 'sparkle', 1, color=(0.7, 0.5, 1.0))
                if not self.is_pet_mode:
                    self.jump_y = -8 * s + 4 * s * math.sin(self.activity_timer * 0.10)
                    if self.activity_timer >= 90:
                        self.trigger_backflip()
                self.queue_draw()

            elif self.activity == "fly":
                self.activity_timer += 1
                self.web_active = False
                self.flip_angle = self.facing * 0.40
                self.arm_spread = 1.0
                self.squish_x = 1.15
                self.squish_y = 0.88
                if self.activity_timer % 2 == 0:
                    self.spawn_particle(self.walk_x - self.facing * 18 * s, 8 * s, 'star', 1, color=(0.4, 0.85, 1.0))
                if not self.is_pet_mode:
                    self.jump_y = -14 * s + 8 * s * math.sin(self.activity_timer * 0.12)
                    if self.activity_timer >= 100:
                        self.trigger_backflip()
                self.queue_draw()

            elif self.activity == "backflip":
                self.activity_timer += 1
                total_frames = 28
                prog = min(1.0, self.activity_timer / float(total_frames))
                self.flip_angle = prog * 2.0 * math.pi
                self.jump_y = -math.sin(prog * math.pi) * 22.0 * s
                self.arm_spread = math.sin(prog * math.pi)
                if self.activity_timer % 3 == 0:
                    self.spawn_particle(self.walk_x, self.jump_y + 10 * s, 'sparkle', 1)
                if self.activity_timer >= total_frames:
                    self.flip_angle = 0.0
                    self.jump_y = 0.0
                    self.arm_spread = 0.0
                    self.squish_y = 0.72
                    self.squish_x = 1.28
                    self.spawn_particle(self.walk_x, 34 * s, 'star', 8)
                    self.activity = "walk"
                    self.activity_timer = 0
                    self.is_walking = True
                    if self.activity_callback: self.activity_callback("✨ Perfect 360° backflip landing! ✦")
                self.queue_draw()

            elif self.activity == "jump":
                self.activity_timer += 1
                if self.activity_timer < 12:
                    self.squish_y = 0.75
                    self.squish_x = 1.25
                    self.tail_phase = (self.tail_phase + 0.35) % (2 * math.pi)
                elif self.activity_timer == 12:
                    self.jump_vy = -8.5 * s
                    self.squish_y = 1.25
                    self.squish_x = 0.85
                    self.spawn_particle(self.walk_x, 32 * s, 'star', 6)
                    if self.activity_callback: self.activity_callback("🐾 *LEAP!* ( ˶ˆ꒳ˆ˵ )")
                else:
                    self.jump_y += self.jump_vy
                    self.jump_vy += 0.82 * s
                    self.arm_spread = min(1.0, abs(self.jump_vy) / (6.0 * s))
                    if self.jump_y >= 0.0:
                        self.jump_y = 0.0
                        self.jump_vy = 0.0
                        self.arm_spread = 0.0
                        self.squish_y = 0.74
                        self.squish_x = 1.26
                        self.spawn_particle(self.walk_x, 34 * s, 'sparkle', 6)
                        self.activity = "walk"
                        self.activity_timer = 0
                        self.is_walking = True
                        if self.activity_callback: self.activity_callback("✨ Landed like a ninja!")
                self.queue_draw()

            elif self.activity == "jacks":
                self.activity_timer += 1
                sub = self.activity_timer % 24
                prog = math.sin((sub / 24.0) * math.pi)
                self.jump_y = -prog * 12.0 * s
                self.arm_spread = prog
                self.feet_spread = prog
                if sub == 12:
                    self.spawn_particle(self.walk_x - 12 * s, -10 * s, 'sparkle', 1)
                    self.spawn_particle(self.walk_x + 12 * s, -10 * s, 'sparkle', 1)
                rep = (self.activity_timer // 24) + 1
                if self.activity_callback and sub == 1:
                    self.activity_callback(f"💪 Workout Rep {rep}/4! (1, 2, 1, 2)")
                if self.activity_timer >= 96:
                    self.jump_y = 0.0
                    self.arm_spread = 0.0
                    self.feet_spread = 0.0
                    self.squish_x = 1.15
                    self.squish_y = 0.88
                    self.spawn_particle(self.walk_x, 0, 'star', 6)
                    self.activity = "walk"
                    self.activity_timer = 0
                    self.is_walking = True
                    if self.activity_callback: self.activity_callback("✨ Workout complete! Feeling energetic!")
                self.queue_draw()

            elif self.activity == "stretch":
                self.activity_timer += 1
                if self.activity_timer < 16:
                    self.stretch_amount = min(1.0, self.activity_timer / 16.0)
                elif self.activity_timer < 48:
                    self.stretch_amount = 1.0
                    self.tail_phase = (self.tail_phase + 0.22) % (2 * math.pi)
                else:
                    self.stretch_amount = max(0.0, 1.0 - (self.activity_timer - 48) / 14.0)
                    if self.stretch_amount <= 0.0:
                        self.stretch_amount = 0.0
                        self.spawn_particle(self.walk_x, 8 * s, 'heart', 3)
                        self.activity = "walk"
                        self.activity_timer = 0
                        self.is_walking = True
                        if self.activity_callback: self.activity_callback("✨ Refreshed and ready!")
                self.queue_draw()

            elif self.activity == "punch":
                self.activity_timer += 1
                phase = self.activity_timer % 20
                if phase < 10:
                    self.punch_l = math.sin((phase / 10.0) * math.pi) * 14.0 * s
                    self.punch_r = 0.0
                    if phase == 5:
                        self.spawn_particle(self.walk_x - 14 * s, 14 * s, 'star', 2)
                else:
                    self.punch_l = 0.0
                    self.punch_r = math.sin(((phase - 10) / 10.0) * math.pi) * 14.0 * s
                    if phase == 15:
                        self.spawn_particle(self.walk_x + 14 * s, 14 * s, 'star', 2)
                if self.activity_timer >= 60:
                    self.punch_l = 0.0
                    self.punch_r = 0.0
                    self.squish_x = 1.14
                    self.squish_y = 0.90
                    self.spawn_particle(self.walk_x, 5 * s, 'star', 6)
                    self.activity = "walk"
                    self.activity_timer = 0
                    self.is_walking = True
                    if self.activity_callback: self.activity_callback("🥊 Karate paws master! ( •̀ ᴗ •́ )و")
                self.queue_draw()

            elif self.activity == "stardust":
                self.activity_timer += 1
                self.star_orb_active = True
                self.star_orb_t += 0.08
                self.star_orb_x = math.sin(self.star_orb_t) * 40.0 * s
                self.star_orb_y = -18.0 * s + math.cos(self.star_orb_t * 1.6) * 14.0 * s
                self.target_look_x = max(-1.0, min(1.0, (self.star_orb_x - self.walk_x) / (32.0 * s)))
                self.target_look_y = max(-1.0, min(1.0, (self.star_orb_y) / (30.0 * s)))
                self.walk_x += (self.star_orb_x - self.walk_x) * 0.05
                if self.activity_timer == 65:
                    self.jump_y = -10.0 * s
                    self.spawn_particle(self.star_orb_x, self.star_orb_y, 'star', 10)
                    self.spawn_particle(self.star_orb_x, self.star_orb_y, 'sparkle', 6)
                    self.star_orb_active = False
                    self.mood = "happy"
                    if self.activity_callback: self.activity_callback("✨ Caught the stardust! ✦")
                elif self.activity_timer >= 85:
                    self.jump_y = 0.0
                    self.star_orb_active = False
                    self.mood = "normal"
                    self.activity = "walk"
                    self.activity_timer = 0
                    self.is_walking = True
                self.queue_draw()

            elif self.activity == "loaf":
                self.activity_timer += 1
                self.squish_y = 1.0 + 0.035 * math.sin(self.activity_timer * 0.08)
                self.squish_x = 1.0 - 0.02 * math.sin(self.activity_timer * 0.08)
                if self.activity_timer % 32 == 0:
                    self.spawn_particle(self.walk_x + 12 * s, -12 * s, 'z', 1, color=(0.85, 0.75, 1.0))
                if self.activity_timer >= 96:
                    self.activity = "walk"
                    self.activity_timer = 0
                    self.is_walking = True
                    self.squish_x = 1.12
                    self.squish_y = 0.90
                    if self.activity_callback: self.activity_callback("✨ Woke up refreshed!")
                self.queue_draw()
        else:
            # During hungry, eating, happy, working: smoothly return to center and stop walking
            if abs(self.walk_x) > 0.5:
                self.walk_x += (0.0 - self.walk_x) * 0.18
                self.queue_draw()
            else:
                self.walk_x = 0.0
            self.facing = 1.0
            self.is_walking = False

        if self.mood == "eating":
            self.eat_phase += 0.20
            self.eat_progress = min(1.0, self.eat_progress + 0.016)
            self.queue_draw()
        elif self.mood in ("working", "thinking"):
            self.paw_anim = (self.paw_anim + 0.16) % 1.0
            self.think_angle = (self.think_angle + 0.12) % (2 * math.pi)
            self.queue_draw()
        elif (abs(diff_x) > 0.005 or abs(diff_y) > 0.005 or
              abs(self.squish_x - 1.0) > 0.005 or abs(self.ear_wiggle) > 0.005 or
              self.is_blinking or self.mood == "hungry" or len(self.particles) > 0 or
              self.star_orb_active or self.jump_y != 0.0):
            self.queue_draw()
        return GLib.SOURCE_CONTINUE

    def pet(self):
        # Wake up from nap or stretch
        if self.activity in ("loaf", "stretch"):
            self.activity = "walk"
            self.stretch_amount = 0.0
        self.squish_x = 1.18
        self.squish_y = 0.85
        self.target_ear_wiggle = 0.22
        self.mood = "happy"
        self.spawn_particle(self.walk_x, -5, 'heart', 5)
        self.queue_draw()
        GLib.timeout_add(2200, self.reset_mood)

    def feed_file(self, filename, on_finished=None):
        self.mood = "eating"
        self.eat_phase = 0.0
        self.eat_progress = 0.0
        self.target_ear_wiggle = 0.12
        self.queue_draw()

        def done_chewing():
            self.mood = "happy"
            self.squish_x = 1.14
            self.squish_y = 0.88
            self.queue_draw()
            if on_finished:
                on_finished()
            GLib.timeout_add(2400, self.reset_mood)
            return GLib.SOURCE_REMOVE

        GLib.timeout_add(1800, done_chewing)

    def set_mood(self, mood):
        self.mood = mood
        if mood == "happy":
            self.squish_x = 1.14
            self.squish_y = 0.88
            self.target_ear_wiggle = 0.14
        elif mood == "hungry":
            self.squish_x = 0.96
            self.squish_y = 1.05
            self.target_ear_wiggle = 0.10
        else:
            self.target_ear_wiggle = 0.0
        self.queue_draw()

    def reset_mood(self):
        self.mood = "normal"
        self.target_ear_wiggle = 0.0
        self.queue_draw()
        return GLib.SOURCE_REMOVE

    def draw_spider_web_tether(self, cr, s, cat_cx, cat_cy):
        if self.activity == "hang_web":
            attach_x = cat_cx
            attach_y = cat_cy - 26.0 * s
            target_x = cat_cx
            target_y = max(0.0, self.web_local_anchor_y) if self.is_pet_mode else 0.0
        else:
            attach_x = cat_cx + self.facing * 14.0 * s
            attach_y = cat_cy - 14.0 * s
            if self.is_pet_mode and self.web_local_anchor_x != 0.0:
                target_x = max(10.0, min(self.mascot_w - 10.0, self.web_local_anchor_x))
                target_y = min(15.0, max(0.0, self.web_local_anchor_y))
            else:
                target_x = cat_cx + self.facing * 32.0 * s
                target_y = 2.0

        cr.save()
        # 1. Outer web glow
        cr.set_line_width(2.8 * s)
        cr.set_source_rgba(0.55, 0.85, 1.0, 0.45)
        cr.move_to(attach_x, attach_y)
        cr.line_to(target_x, target_y)
        cr.stroke()

        # 2. Bright core silk thread
        cr.set_line_width(1.2 * s)
        cr.set_source_rgba(1.0, 1.0, 1.0, 0.95)
        cr.move_to(attach_x, attach_y)
        cr.line_to(target_x, target_y)
        cr.stroke()

        # 3. Web anchor burst
        cr.save()
        cr.translate(target_x, target_y)
        cr.set_line_width(0.9 * s)
        cr.set_source_rgba(0.7, 0.9, 1.0, 0.8)
        for ang in [0, math.pi/4, math.pi/2, 3*math.pi/4, math.pi, 5*math.pi/4, 3*math.pi/2, 7*math.pi/4]:
            cr.move_to(0, 0)
            cr.line_to(math.cos(ang) * 5.0 * s, math.sin(ang) * 5.0 * s)
            cr.stroke()
        cr.arc(0, 0, 2.4 * s, 0, 2 * math.pi)
        cr.set_source_rgba(1.0, 1.0, 1.0, 1.0)
        cr.fill()
        cr.restore()

        # 4. Silk dewdrops
        for t in [0.3, 0.6, 0.85]:
            px = attach_x + (target_x - attach_x) * t
            py = attach_y + (target_y - attach_y) * t
            cr.arc(px, py, 1.2 * s, 0, 2 * math.pi)
            cr.set_source_rgba(0.8, 0.95, 1.0, 0.9)
            cr.fill()

        cr.restore()

    def draw_custom_character(self, cr, s):
        surf = AvatarManager.get().get_surface(is_mini=False)
        if not surf:
            return False

        if AvatarManager.get().is_cutout:
            # 1. Natural grounded floor shadow beneath sneakers (NO background lightning or glow)
            cr.save()
            cr.scale(1.0, 0.24)
            cr.arc(0, 36.0 * s / 0.24, 16.0 * s, 0, 2 * math.pi)
            cr.set_source_rgba(0.0, 0.0, 0.0, 0.28)
            cr.fill()
            cr.restore()

            # 2. Frame selection: full animated walk cycle or idle pose
            is_walk_act = (self.activity == "walk" and self.is_walking)
            frame_idx = int((self.walk_phase / (2 * math.pi)) * 6.0) % 6 if is_walk_act else 0
            surf = AvatarManager.get().get_surface(is_mini=False, frame_index=frame_idx, is_walking=is_walk_act)
            if not surf:
                surf = AvatarManager.get().get_surface(is_mini=False)
            if not surf:
                return False

            # 3. Draw full-body cutout character with real human stride physics
            cr.save()
            if self.facing < 0:
                cr.scale(-1.0, 1.0)
            
            walk_bob = -abs(math.sin(self.walk_phase * 2.0)) * 2.0 * s if is_walk_act else 0.0
            
            h_target = 76.0 * s
            scale = h_target / float(surf.get_height())
            w_target = surf.get_width() * scale
            cr.translate(-w_target / 2.0, -h_target / 2.0 + walk_bob)
            cr.scale(scale, scale)
            cr.set_source_surface(surf, 0, 0)
            cr.paint()
            cr.restore()
            return True

        # Circular Opaque Photo fallback
        # 1. Cosmic Aura
        cr.save()
        pat_aura = cairo.RadialGradient(0, -4 * s, 6 * s, 0, -4 * s, 38 * s)
        pat_aura.add_color_stop_rgba(0.0, 0.75, 0.45, 1.0, 0.35)
        pat_aura.add_color_stop_rgba(0.6, 0.38, 0.85, 0.98, 0.18)
        pat_aura.add_color_stop_rgba(1.0, 0.1, 0.1, 0.2, 0.0)
        cr.set_source(pat_aura)
        cr.arc(0, -4 * s, 38 * s, 0, 2 * math.pi)
        cr.fill()
        cr.restore()

        # 2. Draw Custom Avatar Surface
        cr.save()
        d_target = 56.0 * s
        scale = d_target / float(surf.get_width())
        cr.translate(-d_target / 2.0, -d_target / 2.0 - 4.0 * s)
        cr.scale(scale, scale)
        cr.set_source_surface(surf, 0, 0)
        cr.paint()
        cr.restore()

        # 3. Glowing Cosmic Ring
        cr.save()
        cr.arc(0, -4.0 * s, (d_target / 2.0), 0, 2 * math.pi)
        pat_ring = cairo.LinearGradient(-28 * s, -28 * s, 28 * s, 28 * s)
        pat_ring.add_color_stop_rgba(0.0, 0.75, 0.52, 0.98, 0.95)
        pat_ring.add_color_stop_rgba(0.5, 0.38, 0.85, 0.98, 0.90)
        pat_ring.add_color_stop_rgba(1.0, 1.0, 0.45, 0.75, 0.95)
        cr.set_source(pat_ring)
        cr.set_line_width(2.2 * s)
        cr.stroke()
        cr.restore()

        # 4. Animated Chibi Paws / Hands
        cr.save()
        paw_l_x = -22.0 * s - self.arm_spread * 6.0 * s
        paw_r_x = 22.0 * s + self.arm_spread * 6.0 * s
        paw_l_y = 6.0 * s
        paw_r_y = 6.0 * s

        if self.activity == "fly":
            paw_l_x = -12.0 * s
            paw_r_x = 12.0 * s
            paw_l_y = -28.0 * s
            paw_r_y = -28.0 * s
        elif self.activity == "spiderweb":
            paw_r_x = 16.0 * s
            paw_r_y = -26.0 * s
            paw_l_x = -18.0 * s
            paw_l_y = 4.0 * s
        elif self.activity == "punch":
            paw_l_y -= self.punch_l
            paw_r_y -= self.punch_r
        elif self.activity == "walk" and self.is_walking:
            paw_l_y += math.sin(self.walk_phase) * 5.0 * s
            paw_r_y -= math.sin(self.walk_phase) * 5.0 * s

        for px, py in [(paw_l_x, paw_l_y), (paw_r_x, paw_r_y)]:
            cr.save()
            cr.translate(px, py)
            cr.arc(0, 0, 4.8 * s, 0, 2 * math.pi)
            pat_paw = cairo.RadialGradient(0, -1 * s, 1 * s, 0, 0, 5 * s)
            pat_paw.add_color_stop_rgb(0, 1.0, 1.0, 1.0)
            pat_paw.add_color_stop_rgb(1, 0.88, 0.90, 0.98)
            cr.set_source(pat_paw)
            cr.fill_preserve()
            cr.set_line_width(0.9 * s)
            cr.set_source_rgba(0.6, 0.65, 0.8, 0.5)
            cr.stroke()
            cr.arc(0, 0.5 * s, 1.6 * s, 0, 2 * math.pi)
            cr.set_source_rgba(1.0, 0.55, 0.70, 0.9)
            cr.fill()
            cr.restore()
        cr.restore()

        # 5. Animated Chibi Feet
        cr.save()
        foot_l_x = -14.0 * s - self.feet_spread * 4.0 * s
        foot_r_x = 14.0 * s + self.feet_spread * 4.0 * s
        foot_l_y = 24.0 * s
        foot_r_y = 24.0 * s

        if self.activity == "perch":
            foot_l_y += self.perch_swing * 10.0 * s
            foot_r_y -= self.perch_swing * 10.0 * s
        elif self.activity == "hang_web":
            foot_l_y = -32.0 * s
            foot_r_y = -32.0 * s
        elif self.activity == "fly":
            foot_l_x = -8.0 * s
            foot_r_x = 8.0 * s
            foot_l_y = 28.0 * s
            foot_r_y = 28.0 * s
        elif self.activity == "walk" and self.is_walking:
            foot_l_y -= abs(math.sin(self.walk_phase)) * 4.0 * s
            foot_r_y -= abs(math.cos(self.walk_phase)) * 4.0 * s

        for fx, fy in [(foot_l_x, foot_l_y), (foot_r_x, foot_r_y)]:
            cr.save()
            cr.translate(fx, fy)
            cr.scale(1.15, 0.9)
            cr.arc(0, 0, 6.2 * s, 0, 2 * math.pi)
            pat_foot = cairo.RadialGradient(0, -1 * s, 1 * s, 0, 0, 6.5 * s)
            pat_foot.add_color_stop_rgb(0, 1.0, 1.0, 1.0)
            pat_foot.add_color_stop_rgb(1, 0.86, 0.89, 0.98)
            cr.set_source(pat_foot)
            cr.fill_preserve()
            cr.set_line_width(0.9 * s)
            cr.set_source_rgba(0.6, 0.65, 0.8, 0.5)
            cr.stroke()
            for tx in [-2.5 * s, 0, 2.5 * s]:
                cr.arc(tx, 0.5 * s, 1.1 * s, 0, 2 * math.pi)
                cr.set_source_rgba(1.0, 0.52, 0.68, 0.9)
                cr.fill()
            cr.restore()
        cr.restore()

        return True

    def draw_custom_mini_character(self, cr, w, h):
        surf = AvatarManager.get().get_surface(is_mini=True)
        if not surf:
            return False

        cx = w / 2.0
        cy = h / 2.0
        d_target = min(w, h) - 4.0

        cr.save()
        cr.translate(cx, cy)
        cr.scale(self.squish_x, self.squish_y)

        # 1. Soft glowing aura
        pat_aura = cairo.RadialGradient(0, 0, 2, 0, 0, d_target / 2.0 + 3)
        pat_aura.add_color_stop_rgba(0.0, 0.75, 0.45, 1.0, 0.35)
        pat_aura.add_color_stop_rgba(1.0, 0.38, 0.85, 0.98, 0.0)
        cr.set_source(pat_aura)
        cr.arc(0, 0, d_target / 2.0 + 3, 0, 2 * math.pi)
        cr.fill()

        # 2. Draw Mini Avatar
        scale = d_target / float(surf.get_width())
        cr.save()
        cr.translate(-d_target / 2.0, -d_target / 2.0)
        cr.scale(scale, scale)
        cr.set_source_surface(surf, 0, 0)
        cr.paint()
        cr.restore()

        # 3. Crisp neon ring
        cr.arc(0, 0, d_target / 2.0, 0, 2 * math.pi)
        cr.set_line_width(1.2)
        pat_ring = cairo.LinearGradient(-d_target/2, -d_target/2, d_target/2, d_target/2)
        pat_ring.add_color_stop_rgb(0, 0.75, 0.52, 0.98)
        pat_ring.add_color_stop_rgb(1, 0.38, 0.85, 0.98)
        cr.set_source(pat_ring)
        cr.stroke()

        # 4. Blinking Star Sparkle
        if not self.is_blinking:
            cr.save()
            cr.translate(d_target / 2.0 - 2, -d_target / 2.0 + 2)
            cr.set_source_rgba(1.0, 0.9, 0.4, 0.95)
            cr.arc(0, 0, 1.2, 0, 2 * math.pi)
            cr.fill()
            cr.restore()

        cr.restore()
        return True

    def draw_particles_and_orb(self, cr, cx, cy, s):
        for p in self.particles:
            px, py = cx + p['x'], cy + p['y']
            cr.save()
            r, g, b = p['color']
            cr.set_source_rgba(r, g, b, p['alpha'])
            if p['type'] == 'star':
                draw_star(cr, px, py, p['size'], p['size'] * 0.4, 4)
                cr.fill()
            elif p['type'] == 'sparkle':
                draw_star(cr, px, py, p['size'], p['size'] * 0.25, 4)
                cr.fill()
            elif p['type'] == 'heart':
                draw_heart(cr, px, py, p['size'])
                cr.fill()
            elif p['type'] == 'z':
                draw_z(cr, px, py, p['size'])
            cr.restore()

        if self.star_orb_active:
            ox, oy = cx + self.star_orb_x, cy + self.star_orb_y
            cr.save()
            pat_orb = cairo.RadialGradient(ox, oy, 1, ox, oy, 14 * s)
            pat_orb.add_color_stop_rgba(0.0, 1.0, 1.0, 0.9, 0.95)
            pat_orb.add_color_stop_rgba(0.4, 1.0, 0.85, 0.3, 0.75)
            pat_orb.add_color_stop_rgba(0.8, 0.75, 0.45, 1.0, 0.35)
            pat_orb.add_color_stop_rgba(1.0, 0.5, 0.2, 0.9, 0.0)
            cr.set_source(pat_orb)
            cr.arc(ox, oy, 14 * s, 0, 2 * math.pi)
            cr.fill()
            cr.set_source_rgba(1.0, 1.0, 1.0, 0.95)
            draw_star(cr, ox, oy, 7 * s, 2.5 * s, 4)
            cr.fill()
            cr.restore()

    def on_draw(self, area, cr, w, h):
        if getattr(self, 'is_mini', False):
            if AvatarManager.get().character_type == "custom" and self.draw_custom_mini_character(cr, w, h):
                return
            s = min(w / 48.0, h / 46.0)
            cx = w / 2.0
            cy = h / 2.0 + 3.8 * s

            cr.save()
            cr.translate(cx, cy)
            cr.scale(self.squish_x, self.squish_y)

            # 1. Ears
            for sign in [-1, 1]:
                cr.save()
                ear_base_x = sign * 11.5 * s
                ear_base_y = -12.5 * s
                cr.translate(ear_base_x, ear_base_y)
                cr.rotate(sign * 0.35 + (sign * self.ear_wiggle))

                # Outer ear
                cr.move_to(-sign * 5.5 * s, 5 * s)
                cr.curve_to(-sign * 2.5 * s, -5 * s, sign * 2 * s, -16 * s, sign * 5.5 * s, -14 * s)
                cr.curve_to(sign * 9.5 * s, -10 * s, sign * 7.5 * s, 1 * s, sign * 4.5 * s, 6 * s)
                cr.close_path()

                pat_ear = cairo.LinearGradient(0, -16 * s, 0, 6 * s)
                pat_ear.add_color_stop_rgb(0, 0.98, 0.98, 1.0)
                pat_ear.add_color_stop_rgb(1, 0.88, 0.91, 0.98)
                cr.set_source(pat_ear)
                cr.fill_preserve()
                cr.set_line_width(0.9 * s)
                cr.set_source_rgba(0.65, 0.70, 0.85, 0.5)
                cr.stroke()

                # Inner ear pink fluff
                cr.move_to(-sign * 3.5 * s, 3.5 * s)
                cr.curve_to(-sign * 1.5 * s, -3 * s, sign * 1.5 * s, -10 * s, sign * 4.5 * s, -9 * s)
                cr.curve_to(sign * 7.0 * s, -7 * s, sign * 5.5 * s, 0, sign * 3.0 * s, 4.5 * s)
                cr.close_path()
                pat_inner = cairo.LinearGradient(0, -10 * s, 0, 4 * s)
                pat_inner.add_color_stop_rgba(0, 1.0, 0.45, 0.65, 0.9)
                pat_inner.add_color_stop_rgba(1, 0.98, 0.70, 0.80, 0.5)
                cr.set_source(pat_inner)
                cr.fill()
                cr.restore()

            # 2. Head contour
            cr.save()
            cr.move_to(0, -19 * s)
            cr.curve_to(16 * s, -19 * s, 21 * s, -9 * s, 21 * s, 0 * s)
            cr.curve_to(21 * s, 10 * s, 13 * s, 15 * s, 0, 15 * s)
            cr.curve_to(-13 * s, 15 * s, -21 * s, 10 * s, -21 * s, 0 * s)
            cr.curve_to(-21 * s, -9 * s, -16 * s, -19 * s, 0, -19 * s)
            cr.close_path()

            pat_head = cairo.RadialGradient(-4 * s, -8 * s, 2 * s, 0, -2 * s, 24 * s)
            pat_head.add_color_stop_rgb(0.0, 1.0, 1.0, 1.0)
            pat_head.add_color_stop_rgb(0.70, 0.96, 0.98, 1.0)
            pat_head.add_color_stop_rgb(1.0, 0.86, 0.89, 0.98)
            cr.set_source(pat_head)
            cr.fill_preserve()
            cr.set_line_width(1.0 * s)
            cr.set_source_rgba(0.65, 0.70, 0.85, 0.55)
            cr.stroke()

            # 3. Soft Pink Blush
            for sign in [-1, 1]:
                bx = sign * 12.0 * s
                by = 4.0 * s
                pat_b = cairo.RadialGradient(bx, by, 0.5 * s, bx, by, 5.5 * s)
                pat_b.add_color_stop_rgba(0, 1.0, 0.40, 0.60, 0.55)
                pat_b.add_color_stop_rgba(1, 1.0, 0.40, 0.60, 0.0)
                cr.set_source(pat_b)
                cr.arc(bx, by, 5.5 * s, 0, 2 * math.pi)
                cr.fill()

            # 4. Anime Sparkling Eyes
            eye_dx = 8.5 * s
            eye_dy = -1.0 * s
            for sign in [-1, 1]:
                ex = sign * eye_dx
                ey = eye_dy

                if self.is_blinking:
                    cr.save()
                    cr.translate(ex, ey)
                    cr.move_to(-sign * 3.8 * s, 0)
                    cr.curve_to(-sign * 1.5 * s, -3.2 * s, sign * 1.5 * s, -3.2 * s, sign * 3.8 * s, 0)
                    cr.set_line_width(1.2 * s)
                    cr.set_source_rgba(0.18, 0.12, 0.28, 0.95)
                    cr.stroke()
                    cr.restore()
                else:
                    cr.save()
                    cr.translate(ex, ey)
                    cr.move_to(-sign * 5.0 * s, -4.5 * s)
                    cr.curve_to(-sign * 2.5 * s, -6.5 * s, sign * 2.5 * s, -6.5 * s, sign * 5.0 * s, -4.5 * s)
                    cr.set_line_width(1.2 * s)
                    cr.set_source_rgba(0.18, 0.12, 0.28, 0.95)
                    cr.stroke()

                    cr.save()
                    cr.scale(1.0, 1.15)
                    cr.arc(0, 0, 3.8 * s, 0, 2 * math.pi)
                    cr.set_source_rgb(1.0, 1.0, 1.0)
                    cr.fill_preserve()
                    cr.clip()

                    pat_iris = cairo.LinearGradient(0, -4 * s, 0, 4 * s)
                    pat_iris.add_color_stop_rgb(0, 0.25, 0.05, 0.42)
                    pat_iris.add_color_stop_rgb(0.5, 0.62, 0.18, 0.85)
                    pat_iris.add_color_stop_rgb(1.0, 0.92, 0.38, 0.85)
                    cr.set_source(pat_iris)
                    cr.arc(self.look_x * 0.8 * s, self.look_y * 0.8 * s, 3.4 * s, 0, 2 * math.pi)
                    cr.fill()

                    cr.arc(sign * 1.0 * s + self.look_x * 0.4 * s, -1.2 * s, 1.2 * s, 0, 2 * math.pi)
                    cr.set_source_rgba(1.0, 1.0, 1.0, 0.95)
                    cr.fill()
                    cr.arc(-sign * 1.1 * s, 1.1 * s, 0.65 * s, 0, 2 * math.pi)
                    cr.fill()

                    cr.restore()
                    cr.restore()

            # 5. Nose & Cute Cat Mouth :3
            cr.arc(0, 2.8 * s, 0.65 * s, 0, 2 * math.pi)
            cr.set_source_rgba(1.0, 0.45, 0.60, 0.95)
            cr.fill()

            cr.move_to(-2.8 * s, 5.0 * s)
            cr.curve_to(-1.8 * s, 6.8 * s, -0.6 * s, 6.8 * s, 0, 5.4 * s)
            cr.curve_to(0.6 * s, 6.8 * s, 1.8 * s, 6.8 * s, 2.8 * s, 5.0 * s)
            cr.set_line_width(0.85 * s)
            cr.set_source_rgba(0.25, 0.18, 0.35, 0.85)
            cr.stroke()

            cr.restore()
            return

        s = min(w / 125.0, h / 125.0) if self.is_pet_mode else min(w / 110.0, h / 130.0)
        scale_walk = min(1.0, s / 0.8)
        cx = w / 2.0 + ((self.walk_x * scale_walk) if (w > 50 and not self.is_pet_mode) else 0.0)
        cy = (h / 2.0 + 4.0 * s) if self.is_pet_mode else (h / 2.0)

        # Walking bounce & bobbing physics
        bounce_y = 0.0
        body_tilt = 0.0
        tail_walk_sway = 0.0
        ear_walk_bounce = 0.0
        if self.is_walking and self.mood == "normal" and self.activity == "walk":
            bounce_y = -abs(math.sin(self.walk_phase * 2.0)) * 2.0 * s
            body_tilt = self.facing * math.sin(self.walk_phase) * 0.04
            tail_walk_sway = math.sin(self.walk_phase - 0.4) * 0.35
            ear_walk_bounce = math.sin(self.walk_phase * 2.0) * 0.08

        # 1. Ambient Drop Shadow on Floor (stays anchored to floor, responsive to jump height)
        cr.save()
        cr.scale(1.0, 0.28)
        shadow_floor_y = (cy + 46 * s) / 0.28
        shadow_scale = max(0.18, 1.0 + self.jump_y / (55.0 * s))
        shadow_r = 34 * s * shadow_scale
        shadow_alpha = max(0.06, 0.45 * shadow_scale)
        pat_sh = cairo.RadialGradient(cx, shadow_floor_y, 2 * s, cx, shadow_floor_y, shadow_r)
        pat_sh.add_color_stop_rgba(0, 0.08, 0.05, 0.22, shadow_alpha)
        pat_sh.add_color_stop_rgba(1, 0, 0, 0, 0.0)
        cr.set_source(pat_sh)
        cr.arc(cx, shadow_floor_y, shadow_r, 0, 2 * math.pi)
        cr.fill()
        cr.restore()

        # Draw spider-web tether if active (in widget coordinates reaching top anchor directly)
        cat_cy = cy + 2 * s + bounce_y + self.jump_y + (self.stretch_amount * 3.5 * s)
        if self.web_active:
            self.draw_spider_web_tether(cr, s, cx, cat_cy)

        cr.save()
        stretch_rot = -self.stretch_amount * 0.06
        cr.translate(cx, cat_cy)
        cr.rotate(body_tilt + stretch_rot + self.flip_angle)
        cr.scale(self.squish_x, self.squish_y)

        # If custom photo avatar skin is active, draw the mini character
        if AvatarManager.get().character_type == "custom" and self.draw_custom_character(cr, s):
            cr.restore()
            if not AvatarManager.get().is_cutout:
                self.draw_particles_and_orb(cr, cx, cy, s)
            elif self.activity in ("spiderweb", "stardust"):
                self.draw_particles_and_orb(cr, cx, cy, s)
            return

        # 2. Fluffy Fox/Kitten Tail (Curving up on right with walking sway)
        tail_sway = tail_walk_sway if (self.is_walking and self.mood == "normal") else math.sin(self.tail_phase) * 0.12
        if self.mood in ("eating", "happy"):
            tail_sway = math.sin(self.tail_phase * 2.5) * 0.35
        cr.save()
        cr.translate(14 * s, 18 * s)
        cr.rotate(0.20 + tail_sway)
        cr.move_to(0, 0)
        cr.curve_to(14 * s, 4 * s, 24 * s, -10 * s, 18 * s, -22 * s)
        cr.curve_to(14 * s, -28 * s, 5 * s, -24 * s, 7 * s, -14 * s)
        cr.curve_to(8 * s, -6 * s, 3 * s, -2 * s, 0, 0)
        cr.close_path()

        pat_tail = cairo.LinearGradient(0, 4 * s, 18 * s, -28 * s)
        pat_tail.add_color_stop_rgb(0, 0.88, 0.90, 0.98)
        pat_tail.add_color_stop_rgb(0.65, 0.96, 0.97, 1.0)
        pat_tail.add_color_stop_rgb(1.0, 1.0, 1.0, 1.0)
        cr.set_source(pat_tail)
        cr.fill_preserve()
        cr.set_line_width(1.0 * s)
        cr.set_source_rgba(0.65, 0.70, 0.85, 0.45)
        cr.stroke()

        cr.move_to(13 * s, -20 * s)
        cr.curve_to(15 * s, -16 * s, 9 * s, -18 * s, 7 * s, -14 * s)
        cr.set_line_width(0.8 * s)
        cr.set_source_rgba(0.72, 0.76, 0.90, 0.6)
        cr.stroke()
        cr.restore()

        # 3. Chubby Torso / Body with soft white bib
        cr.save()
        cr.move_to(-14 * s, 8 * s)
        cr.curve_to(-18 * s, 20 * s, -14 * s, 32 * s, 0, 33 * s)
        cr.curve_to(14 * s, 32 * s, 18 * s, 20 * s, 14 * s, 8 * s)
        cr.curve_to(10 * s, 0 * s, -10 * s, 0 * s, -14 * s, 8 * s)
        cr.close_path()

        pat_body = cairo.RadialGradient(-3 * s, 15 * s, 2 * s, 0, 20 * s, 22 * s)
        pat_body.add_color_stop_rgb(0.0, 1.0, 1.0, 1.0)
        pat_body.add_color_stop_rgb(0.65, 0.95, 0.97, 1.0)
        pat_body.add_color_stop_rgb(1.0, 0.87, 0.90, 0.98)
        cr.set_source(pat_body)
        cr.fill_preserve()
        cr.set_line_width(1.1 * s)
        cr.set_source_rgba(0.65, 0.70, 0.85, 0.45)
        cr.stroke()

        cr.move_to(-7 * s, 7 * s)
        cr.curve_to(-9 * s, 16 * s, -5 * s, 24 * s, 0, 26 * s)
        cr.curve_to(5 * s, 24 * s, 9 * s, 16 * s, 7 * s, 7 * s)
        cr.curve_to(3 * s, 4 * s, -3 * s, 4 * s, -7 * s, 7 * s)
        cr.close_path()
        cr.set_source_rgba(1.0, 1.0, 1.0, 0.75)
        cr.fill()
        cr.restore()

        # 4. Cute Stubby Legs & Feet with pink toe beans (taking alternating steps during walk or jumping jacks)
        step_l_x = math.sin(self.walk_phase) * 5.0 * s if (self.is_walking and self.mood == "normal" and self.activity == "walk") else 0.0
        step_l_y = -max(0.0, math.cos(self.walk_phase)) * 4.0 * s if (self.is_walking and self.mood == "normal" and self.activity == "walk") else 0.0
        step_r_x = -math.sin(self.walk_phase) * 5.0 * s if (self.is_walking and self.mood == "normal" and self.activity == "walk") else 0.0
        step_r_y = -max(0.0, -math.cos(self.walk_phase)) * 4.0 * s if (self.is_walking and self.mood == "normal" and self.activity == "walk") else 0.0

        if self.feet_spread > 0:
            feet_data = [
                (-1, -10.5 * s - self.feet_spread * 7.5 * s, 30.0 * s, -0.25),
                (1, 10.5 * s + self.feet_spread * 7.5 * s, 30.0 * s, 0.25)
            ]
        elif self.activity == "loaf":
            feet_data = [
                (-1, -7.0 * s, 31.0 * s, -0.3),
                (1, 7.0 * s, 31.0 * s, 0.3)
            ]
        else:
            feet_data = [
                (-1, -10.5 * s + step_l_x, 30.0 * s + step_l_y, math.sin(self.walk_phase) * 0.16 if (self.is_walking and self.mood == "normal" and self.activity == "walk") else 0.0),
                (1, 10.5 * s + step_r_x, 30.0 * s + step_r_y, -math.sin(self.walk_phase) * 0.16 if (self.is_walking and self.mood == "normal" and self.activity == "walk") else 0.0)
            ]
        for sign, lx, ly, foot_rot in feet_data:
            cr.save()
            cr.translate(lx, ly)
            cr.rotate(foot_rot)
            cr.scale(1.0, 0.85)
            cr.arc(0, 0, 5.2 * s, 0, 2 * math.pi)
            pat_foot = cairo.LinearGradient(0, -5 * s, 0, 5 * s)
            pat_foot.add_color_stop_rgb(0, 1.0, 1.0, 1.0)
            pat_foot.add_color_stop_rgb(1, 0.88, 0.91, 0.98)
            cr.set_source(pat_foot)
            cr.fill_preserve()
            cr.set_line_width(1.0 * s)
            cr.set_source_rgba(0.65, 0.70, 0.85, 0.5)
            cr.stroke()

            # Pink main paw pad
            cr.arc(0, 0.5 * s, 2.2 * s, 0, 2 * math.pi)
            cr.set_source_rgba(1.0, 0.50, 0.68, 0.90)
            cr.fill()

            # 3 Cute Pink Toe Beans
            for toe_angle, toe_r in [(-0.55, 3.4 * s), (0.0, 3.8 * s), (0.55, 3.4 * s)]:
                tx = math.sin(toe_angle) * toe_r
                ty = -math.cos(toe_angle) * toe_r + 0.3 * s
                cr.arc(tx, ty, 0.95 * s, 0, 2 * math.pi)
                cr.set_source_rgba(1.0, 0.52, 0.70, 0.92)
                cr.fill()
            cr.restore()

        # 5. Fluffy Kitten/Fox Ears (Wide base, triangular, perky outward tilt)
        for sign in [-1, 1]:
            cr.save()
            ear_base_x = sign * 13.5 * s
            ear_base_y = -18.0 * s
            cr.translate(ear_base_x, ear_base_y)
            cr.rotate(sign * 0.38 + (sign * self.ear_wiggle) + (sign * ear_walk_bounce))

            # Outer ear
            cr.move_to(-sign * 6.5 * s, 6 * s)
            cr.curve_to(-sign * 3 * s, -6 * s, sign * 2 * s, -18 * s, sign * 6.5 * s, -16 * s)
            cr.curve_to(sign * 11 * s, -12 * s, sign * 8.5 * s, 1 * s, sign * 5.0 * s, 7 * s)
            cr.close_path()

            pat_ear = cairo.LinearGradient(0, -18 * s, 0, 6 * s)
            pat_ear.add_color_stop_rgb(0, 0.96, 0.98, 1.0)
            pat_ear.add_color_stop_rgb(1, 0.88, 0.91, 0.98)
            cr.set_source(pat_ear)
            cr.fill_preserve()
            cr.set_line_width(1.1 * s)
            cr.set_source_rgba(0.65, 0.70, 0.85, 0.5)
            cr.stroke()

            # Inner ear warm rosy pink fluff
            cr.move_to(-sign * 4.0 * s, 4 * s)
            cr.curve_to(-sign * 1.5 * s, -4 * s, sign * 2 * s, -12 * s, sign * 5.5 * s, -11 * s)
            cr.curve_to(sign * 8.0 * s, -8 * s, sign * 6.5 * s, 0, sign * 3.5 * s, 5 * s)
            cr.close_path()
            pat_inner = cairo.LinearGradient(0, -12 * s, 0, 4 * s)
            pat_inner.add_color_stop_rgba(0, 1.0, 0.45, 0.65, 0.88)
            pat_inner.add_color_stop_rgba(1, 0.98, 0.70, 0.80, 0.45)
            cr.set_source(pat_inner)
            cr.fill()

            # Ear-tip tuft
            cr.move_to(sign * 1.0 * s, 1 * s)
            cr.curve_to(sign * 3.5 * s, -2 * s, sign * 5.0 * s, -4 * s, sign * 5.5 * s, 0)
            cr.set_line_width(0.8 * s)
            cr.set_source_rgba(1.0, 1.0, 1.0, 0.9)
            cr.stroke()
            cr.restore()

        # 6. Head Contour (Chubby cheeks, soft kitten silhouette, NO FOREHEAD DOT)
        cr.save()
        cr.move_to(0, -25 * s)
        cr.curve_to(17 * s, -25 * s, 24 * s, -12 * s, 24 * s, 0 * s)
        cr.curve_to(24 * s, 12 * s, 15 * s, 17 * s, 0, 17 * s)
        cr.curve_to(-15 * s, 17 * s, -24 * s, 12 * s, -24 * s, 0 * s)
        cr.curve_to(-24 * s, -12 * s, -17 * s, -25 * s, 0, -25 * s)
        cr.close_path()

        pat_head = cairo.RadialGradient(-5 * s, -10 * s, 3 * s, 0, -3 * s, 28 * s)
        pat_head.add_color_stop_rgb(0.0, 1.0, 1.0, 1.0)
        pat_head.add_color_stop_rgb(0.70, 0.96, 0.98, 1.0)
        pat_head.add_color_stop_rgb(1.0, 0.86, 0.89, 0.98)
        cr.set_source(pat_head)
        cr.fill_preserve()
        cr.set_line_width(1.2 * s)
        cr.set_source_rgba(0.65, 0.70, 0.85, 0.5)
        cr.stroke()

        # Cheek fur tufts
        for sign in [-1, 1]:
            cr.move_to(sign * 23.5 * s, -1 * s)
            cr.curve_to(sign * 26.5 * s, 1 * s, sign * 26 * s, 4 * s, sign * 22 * s, 5 * s)
            cr.set_line_width(0.9 * s)
            cr.set_source_rgba(0.68, 0.72, 0.86, 0.6)
            cr.stroke()

        # Soft top sheen
        cr.save()
        cr.scale(0.85, 0.42)
        pat_gloss = cairo.LinearGradient(0, -55 * s, 0, -25 * s)
        pat_gloss.add_color_stop_rgba(0, 1.0, 1.0, 1.0, 0.65)
        pat_gloss.add_color_stop_rgba(1, 1.0, 1.0, 1.0, 0.0)
        cr.set_source(pat_gloss)
        cr.arc(0, -38 * s, 16 * s, 0, 2 * math.pi)
        cr.fill()
        cr.restore()

        # 7. Soft Cheek Blush
        for sign in [-1, 1]:
            cr.save()
            ch_x = sign * 14 * s
            ch_y = 3.5 * s
            pat_bl = cairo.RadialGradient(ch_x, ch_y, 0, ch_x, ch_y, 6.0 * s)
            pat_bl.add_color_stop_rgba(0.0, 1.0, 0.40, 0.55, 0.55)
            pat_bl.add_color_stop_rgba(0.6, 1.0, 0.55, 0.68, 0.25)
            pat_bl.add_color_stop_rgba(1.0, 1.0, 0.65, 0.75, 0.0)
            cr.set_source(pat_bl)
            cr.arc(ch_x, ch_y, 6.0 * s, 0, 2 * math.pi)
            cr.fill()

            cr.arc(ch_x - sign * 1.5 * s, ch_y - 0.5 * s, 0.7 * s, 0, 2 * math.pi)
            cr.arc(ch_x + sign * 1.5 * s, ch_y + 0.5 * s, 0.5 * s, 0, 2 * math.pi)
            cr.set_source_rgba(1.0, 1.0, 1.0, 0.8)
            cr.fill()
            cr.restore()

        # 8. Cute Button Nose
        cr.save()
        cr.move_to(-1.2 * s, -0.2 * s)
        cr.line_to(1.2 * s, -0.2 * s)
        cr.line_to(0, 1.2 * s)
        cr.close_path()
        cr.set_source_rgb(0.95, 0.45, 0.62)
        cr.fill()
        cr.restore()

        # 9. Cute Cat Mouth
        cr.save()
        cr.set_line_cap(cairo.LINE_CAP_ROUND)
        cr.set_line_join(cairo.LINE_JOIN_ROUND)
        if self.mood == "hungry":
            # Excited wide round open "O" anticipating food
            cr.save()
            cr.translate(0, 5.0 * s)
            cr.scale(1.0, 1.2)
            cr.arc(0, 0, 3.6 * s, 0, 2 * math.pi)
            cr.set_source_rgb(0.22, 0.08, 0.20)
            cr.fill_preserve()
            cr.set_line_width(1.1 * s)
            cr.set_source_rgb(0.16, 0.05, 0.16)
            cr.stroke()

            # Cute pink tongue
            cr.save()
            cr.arc(0, 0, 3.6 * s, 0, 2 * math.pi)
            cr.clip()
            cr.arc(0, 2.2 * s, 2.6 * s, 0, 2 * math.pi)
            cr.set_source_rgb(1.0, 0.48, 0.65)
            cr.fill()
            cr.restore()
            cr.restore()

        elif self.mood == "eating":
            # Chewing cycle
            chew_open = (math.sin(self.eat_phase * 12.0) > 0.0)
            if chew_open:
                cr.save()
                cr.translate(0, 4.6 * s)
                cr.scale(1.1, 0.8)
                cr.arc(0, 0, 3.2 * s, 0, 2 * math.pi)
                cr.set_source_rgb(0.22, 0.08, 0.20)
                cr.fill_preserve()
                cr.set_line_width(1.0 * s)
                cr.set_source_rgb(0.16, 0.05, 0.16)
                cr.stroke()

                cr.save()
                cr.arc(0, 0, 3.2 * s, 0, 2 * math.pi)
                cr.clip()
                cr.arc(0, 1.8 * s, 2.2 * s, 0, 2 * math.pi)
                cr.set_source_rgb(1.0, 0.48, 0.65)
                cr.fill()
                cr.restore()
                cr.restore()
            else:
                cr.set_line_width(1.5 * s)
                cr.set_source_rgba(0.22, 0.16, 0.32, 0.95)
                cr.move_to(-4.0 * s, 4.0 * s)
                cr.curve_to(-2.0 * s, 6.2 * s, 0, 4.2 * s, 0, 4.2 * s)
                cr.curve_to(0, 4.2 * s, 2.0 * s, 6.2 * s, 4.0 * s, 4.0 * s)
                cr.stroke()

        elif self.mood == "happy":
            cr.move_to(-4.0 * s, 3.2 * s)
            cr.curve_to(-4.0 * s, 9.0 * s, 4.0 * s, 9.0 * s, 4.0 * s, 3.2 * s)
            cr.close_path()
            cr.set_source_rgb(0.24, 0.08, 0.22)
            cr.fill_preserve()
            cr.set_line_width(1.1 * s)
            cr.set_source_rgb(0.18, 0.05, 0.18)
            cr.stroke()

            cr.save()
            cr.move_to(-4.0 * s, 3.2 * s)
            cr.curve_to(-4.0 * s, 9.0 * s, 4.0 * s, 9.0 * s, 4.0 * s, 3.2 * s)
            cr.clip()
            cr.arc(0, 8.5 * s, 3.0 * s, 0, 2 * math.pi)
            cr.set_source_rgb(1.0, 0.48, 0.65)
            cr.fill()
            cr.restore()
        else:
            cr.set_line_width(1.3 * s)
            cr.set_source_rgba(0.22, 0.16, 0.32, 0.88)
            cr.move_to(-3.5 * s, 3.8 * s)
            cr.curve_to(-2.4 * s, 5.8 * s, -0.5 * s, 5.8 * s, 0, 4.4 * s)
            cr.curve_to(0.5 * s, 5.8 * s, 2.4 * s, 5.8 * s, 3.5 * s, 3.8 * s)
            cr.stroke()
        cr.restore()

        # 10. Expressive Anime Eyes (NO FOREHEAD DOT!)
        eye_spacing = 9.2 * s
        eye_y = -5.5 * s

        for sign in [-1, 1]:
            ex = sign * eye_spacing
            ey = eye_y

            # Eyebrow
            cr.save()
            cr.set_line_width(1.3 * s)
            cr.set_line_cap(cairo.LINE_CAP_ROUND)
            cr.set_source_rgba(0.28, 0.22, 0.42, 0.78)
            brow_lift = -1.6 * s if self.mood in ("happy", "hungry") else 0
            brow_tilt = sign * 0.5 * s if self.mood in ("working", "thinking") else 0
            cr.move_to(ex - 3.8 * s, ey - 7.5 * s + brow_lift - brow_tilt)
            cr.curve_to(ex - 1.2 * s, ey - 9.0 * s + brow_lift, ex + 1.2 * s, ey - 9.0 * s + brow_lift, ex + 3.8 * s, ey - 7.5 * s + brow_lift + brow_tilt)
            cr.stroke()
            cr.restore()

            if self.is_blinking:
                cr.save()
                cr.set_line_width(2.0 * s)
                cr.set_line_cap(cairo.LINE_CAP_ROUND)
                cr.set_source_rgba(0.18, 0.12, 0.32, 0.95)
                cr.move_to(ex - 4.8 * s, ey + 0.5 * s)
                cr.curve_to(ex - 2 * s, ey + 3.8 * s, ex + 2 * s, ey + 3.8 * s, ex + 4.8 * s, ey + 0.5 * s)
                cr.stroke()
                cr.move_to(ex + sign * 4.2 * s, ey + 0.8 * s)
                cr.line_to(ex + sign * 5.8 * s, ey - 0.2 * s)
                cr.stroke()
                cr.restore()

            elif self.mood in ("happy", "eating") or self.activity in ("loaf", "stretch"):
                cr.save()
                cr.set_line_width(2.4 * s)
                cr.set_line_cap(cairo.LINE_CAP_ROUND)
                cr.set_source_rgba(0.18, 0.12, 0.32, 0.95)
                cr.move_to(ex - 4.8 * s, ey + 2.0 * s)
                cr.curve_to(ex - 2 * s, ey - 3.0 * s, ex + 2 * s, ey - 3.0 * s, ex + 4.8 * s, ey + 2.0 * s)
                cr.stroke()
                cr.set_line_width(1.5 * s)
                cr.move_to(ex + sign * 4.2 * s, ey + 1.2 * s)
                cr.line_to(ex + sign * 6.0 * s, ey - 0.8 * s)
                cr.stroke()
                cr.restore()

            elif self.mood == "hungry":
                # Large sparkling eyes looking up in anticipation
                cr.save()
                cr.translate(ex, ey)
                cr.scale(4.5 * s, 6.2 * s)
                cr.arc(0, 0, 1.0, 0, 2 * math.pi)
                cr.set_source_rgb(1.0, 1.0, 1.0)
                cr.fill_preserve()
                cr.set_line_width(0.3)
                cr.set_source_rgba(0.7, 0.75, 0.85, 0.4)
                cr.stroke()
                cr.restore()

                cr.save()
                cr.save()
                cr.translate(ex, ey)
                cr.scale(4.5 * s, 6.2 * s)
                cr.arc(0, 0, 1.0, 0, 2 * math.pi)
                cr.restore()
                cr.clip()

                px = ex
                py = ey - 1.5 * s
                cr.save()
                cr.translate(px, py)
                cr.scale(3.8 * s, 5.0 * s)
                pat_iris = cairo.LinearGradient(0, -1, 0, 1)
                pat_iris.add_color_stop_rgb(0.0, 0.12, 0.05, 0.28)
                pat_iris.add_color_stop_rgb(0.5, 0.55, 0.18, 0.72)
                pat_iris.add_color_stop_rgb(1.0, 0.95, 0.35, 0.75)
                cr.set_source(pat_iris)
                cr.arc(0, 0, 1.0, 0, 2 * math.pi)
                cr.fill()
                cr.restore()

                cr.arc(px - 1.2 * s, py - 1.8 * s, 1.8 * s, 0, 2 * math.pi)
                cr.set_source_rgb(1.0, 1.0, 1.0)
                cr.fill()
                cr.arc(px + 1.4 * s, py + 1.2 * s, 1.0 * s, 0, 2 * math.pi)
                cr.fill()
                cr.restore()

            elif self.mood in ("working", "thinking"):
                # Inquisitive upward starlight glance
                cr.save()
                cr.translate(ex, ey)
                cr.scale(4.2 * s, 5.6 * s)
                cr.arc(0, 0, 1.0, 0, 2 * math.pi)
                cr.set_source_rgb(1.0, 1.0, 1.0)
                cr.fill()
                cr.restore()

                cr.save()
                cr.translate(ex, ey - 1.2 * s)
                cr.scale(3.2 * s, 4.4 * s)
                pat_iris = cairo.LinearGradient(0, -1, 0, 1)
                pat_iris.add_color_stop_rgb(0.0, 0.12, 0.05, 0.28)
                pat_iris.add_color_stop_rgb(1.0, 0.65, 0.20, 0.75)
                cr.set_source(pat_iris)
                cr.arc(0, 0, 1.0, 0, 2 * math.pi)
                cr.fill()
                cr.restore()

                cr.save()
                cr.translate(ex, ey - 1.2 * s)
                cr.rotate(self.think_angle)
                cr.set_line_width(1.4 * s)
                cr.set_line_cap(cairo.LINE_CAP_ROUND)
                cr.set_source_rgb(1.0, 1.0, 1.0)
                cr.move_to(-2.4 * s, 0)
                cr.line_to(2.4 * s, 0)
                cr.move_to(0, -2.4 * s)
                cr.line_to(0, 2.4 * s)
                cr.stroke()
                cr.arc(0, 0, 0.7 * s, 0, 2 * math.pi)
                cr.fill()
                cr.restore()

                cr.save()
                cr.set_line_width(1.8 * s)
                cr.set_line_cap(cairo.LINE_CAP_ROUND)
                cr.set_source_rgba(0.18, 0.12, 0.32, 0.95)
                cr.move_to(ex - 4.5 * s, ey - 2.5 * s)
                cr.curve_to(ex - 1.8 * s, ey - 5.8 * s, ex + 1.8 * s, ey - 5.8 * s, ex + 4.5 * s, ey - 2.5 * s)
                cr.stroke()
                cr.restore()

            else:
                # 1. Clean Sclera Base
                cr.save()
                cr.translate(ex, ey)
                cr.scale(4.2 * s, 5.8 * s)
                cr.arc(0, 0, 1.0, 0, 2 * math.pi)
                cr.set_source_rgb(1.0, 1.0, 1.0)
                cr.fill_preserve()
                cr.set_line_width(0.3)
                cr.set_source_rgba(0.7, 0.75, 0.85, 0.4)
                cr.stroke()
                cr.restore()

                # 2. Clip iris strictly inside sclera
                cr.save()
                cr.save()
                cr.translate(ex, ey)
                cr.scale(4.2 * s, 5.8 * s)
                cr.arc(0, 0, 1.0, 0, 2 * math.pi)
                cr.restore()
                cr.clip()

                # Jewel Iris tracking cursor
                px = ex + self.look_x * 2.2 * s
                py = ey + self.look_y * 2.0 * s
                cr.save()
                cr.translate(px, py)
                cr.scale(3.2 * s, 4.5 * s)
                pat_iris = cairo.LinearGradient(0, -1, 0, 1)
                pat_iris.add_color_stop_rgb(0.0, 0.08, 0.04, 0.22)
                pat_iris.add_color_stop_rgb(0.55, 0.45, 0.18, 0.68)
                pat_iris.add_color_stop_rgb(1.0, 0.95, 0.32, 0.68)
                cr.set_source(pat_iris)
                cr.arc(0, 0, 1.0, 0, 2 * math.pi)
                cr.fill()
                cr.restore()

                # Obsidian Pupil
                cr.save()
                cr.translate(px, py - 0.2 * s)
                cr.scale(1.7 * s, 2.5 * s)
                cr.arc(0, 0, 1.0, 0, 2 * math.pi)
                cr.set_source_rgb(0.05, 0.02, 0.10)
                cr.fill()
                cr.restore()

                # Triple Specular Catchlights
                cr.save()
                cr.arc(px - 1.2 * s, py - 1.5 * s, 1.3 * s, 0, 2 * math.pi)
                cr.set_source_rgb(1.0, 1.0, 1.0)
                cr.fill()

                cr.arc(px + 1.2 * s, py + 1.4 * s, 0.75 * s, 0, 2 * math.pi)
                cr.set_source_rgba(1.0, 1.0, 1.0, 0.92)
                cr.fill()

                cr.arc(px - 0.4 * s, py + 1.8 * s, 0.5 * s, 0, 2 * math.pi)
                cr.set_source_rgba(1.0, 0.85, 1.0, 0.9)
                cr.fill()
                cr.restore()

                cr.restore() # End clip

                # 3. Upper Eyelid & Winged Eyelash Line
                cr.save()
                cr.set_line_width(2.0 * s)
                cr.set_line_cap(cairo.LINE_CAP_ROUND)
                cr.set_source_rgba(0.16, 0.10, 0.28, 0.98)
                cr.move_to(ex - 4.6 * s, ey - 2.5 * s)
                cr.curve_to(ex - 2.0 * s, ey - 6.2 * s, ex + 2.0 * s, ey - 6.2 * s, ex + 4.6 * s, ey - 2.5 * s)
                cr.stroke()
                cr.set_line_width(1.4 * s)
                cr.move_to(ex + sign * 4.2 * s, ey - 3.0 * s)
                cr.line_to(ex + sign * 6.0 * s, ey - 4.8 * s)
                cr.stroke()
                cr.restore()

        cr.restore() # End Head save

        # 11. Hands & Paws + Food Item
        if self.mood == "eating":
            cr.save()
            snack_w = 13.0 * s * (1.0 - self.eat_progress * 0.65)
            snack_h = 16.0 * s * (1.0 - self.eat_progress * 0.65)
            snack_y = 7.0 * s + math.sin(self.eat_phase * 12.0) * 1.5 * s

            cr.save()
            cr.translate(0, snack_y)
            cr.rotate(-0.08)

            # File document wafer
            cr.save()
            cr.rectangle(-snack_w / 2.0, -snack_h / 2.0, snack_w, snack_h)
            pat_snack = cairo.LinearGradient(0, -snack_h / 2.0, 0, snack_h / 2.0)
            pat_snack.add_color_stop_rgb(0, 0.92, 0.85, 1.0)
            pat_snack.add_color_stop_rgb(1, 0.65, 0.45, 0.95)
            cr.set_source(pat_snack)
            cr.fill_preserve()
            cr.set_line_width(1.0 * s)
            cr.set_source_rgb(0.98, 0.85, 0.40)
            cr.stroke()

            # Document lines on wafer
            cr.set_line_width(1.0 * s)
            cr.set_source_rgba(1.0, 1.0, 1.0, 0.7)
            for line_y in [-3 * s, 0, 3 * s]:
                if line_y < snack_h / 2.0 - 2 * s:
                    cr.move_to(-snack_w * 0.32, line_y)
                    cr.line_to(snack_w * 0.32, line_y)
                    cr.stroke()
            cr.restore()
            cr.restore()

            # Both paws grasping the snack
            for sign in [-1, 1]:
                cr.save()
                cr.translate(sign * 6.2 * s, snack_y + 3.0 * s)
                cr.scale(1.0, 0.92)
                cr.arc(0, 0, 4.4 * s, 0, 2 * math.pi)
                cr.set_source_rgb(1.0, 1.0, 1.0)
                cr.fill_preserve()
                cr.set_line_width(0.9 * s)
                cr.set_source_rgba(0.65, 0.70, 0.85, 0.5)
                cr.stroke()

                # Pink paw pad
                cr.arc(0, 0.4 * s, 2.0 * s, 0, 2 * math.pi)
                cr.set_source_rgba(1.0, 0.55, 0.72, 0.85)
                cr.fill()
                cr.restore()

            # Floating crunchy crumbs / sparkles!
            crumb_seed = [
                (-9.0 * s, 4.0 * s, 1.2 * s),
                (8.5 * s, 3.5 * s, 1.0 * s),
                (-7.0 * s, 12.0 * s, 0.8 * s),
                (9.5 * s, 11.0 * s, 1.4 * s),
                (0.0 * s, -1.0 * s, 1.1 * s)
            ]
            for cx_i, cy_i, cr_i in crumb_seed:
                cr.save()
                cr.translate(cx_i + math.sin(self.eat_phase * 8.0 + cx_i) * 1.5 * s, 
                             cy_i + math.cos(self.eat_phase * 8.0 + cy_i) * 1.5 * s)
                cr.set_source_rgba(1.0, 0.88, 0.40, 0.90)
                cr.arc(0, 0, cr_i, 0, 2 * math.pi)
                cr.fill()
                cr.restore()

            cr.restore()

        elif self.mood == "hungry":
            # Paws reaching cutely forward in anticipation
            for sign in [-1, 1]:
                cr.save()
                cr.translate(sign * 8.5 * s, 14.0 * s)
                cr.scale(1.0, 0.9)
                cr.arc(0, 0, 4.4 * s, 0, 2 * math.pi)
                cr.set_source_rgb(1.0, 1.0, 1.0)
                cr.fill_preserve()
                cr.set_line_width(0.9 * s)
                cr.set_source_rgba(0.65, 0.70, 0.85, 0.5)
                cr.stroke()

                cr.arc(0, 0.5 * s, 2.0 * s, 0, 2 * math.pi)
                cr.set_source_rgba(1.0, 0.55, 0.72, 0.85)
                cr.fill()
                cr.restore()

        elif self.mood in ("working", "thinking"):
            paw_phase = math.sin(self.paw_anim * 2 * math.pi)
            cr.save()
            # Left paw holding/typing
            cr.translate(-7 * s + paw_phase * 2 * s, 18 * s - paw_phase * 3 * s)
            cr.scale(1.0, 0.9)
            cr.arc(0, 0, 4.2 * s, 0, 2 * math.pi)
            cr.set_source_rgb(1.0, 1.0, 1.0)
            cr.fill_preserve()
            cr.set_line_width(0.9 * s)
            cr.set_source_rgba(0.65, 0.70, 0.85, 0.5)
            cr.stroke()
            cr.arc(0, 0.5 * s, 2.0 * s, 0, 2 * math.pi)
            cr.set_source_rgba(1.0, 0.55, 0.72, 0.8)
            cr.fill()
            cr.restore()

            # Right paw holding cosmic glowing stylus
            cr.save()
            cr.translate(7 * s - paw_phase * 2 * s, 17 * s + paw_phase * 3 * s)
            cr.save()
            cr.rotate(-0.35)
            pat_stylus = cairo.LinearGradient(0, -10 * s, 0, 8 * s)
            pat_stylus.add_color_stop_rgb(0, 0.75, 0.5, 1.0)
            pat_stylus.add_color_stop_rgb(1, 0.35, 0.2, 0.6)
            cr.set_source(pat_stylus)
            cr.rectangle(-1.2 * s, -10 * s, 2.4 * s, 16 * s)
            cr.fill()
            cr.arc(0, -10 * s, 2.4 * s, 0, 2 * math.pi)
            cr.set_source_rgba(1.0, 0.8, 1.0, 0.9)
            cr.fill()
            cr.restore()

            cr.scale(1.0, 0.9)
            cr.arc(0, 0, 4.2 * s, 0, 2 * math.pi)
            cr.set_source_rgb(1.0, 1.0, 1.0)
            cr.fill_preserve()
            cr.set_line_width(0.9 * s)
            cr.set_source_rgba(0.65, 0.70, 0.85, 0.5)
            cr.stroke()
            cr.arc(0, 0.5 * s, 2.0 * s, 0, 2 * math.pi)
            cr.set_source_rgba(1.0, 0.55, 0.72, 0.8)
            cr.fill()
            cr.restore()

        elif self.mood == "happy":
            for sign in [-1, 1]:
                cr.save()
                cr.translate(sign * 18 * s, 6 * s)
                cr.scale(1.0, 0.9)
                cr.arc(0, 0, 4.4 * s, 0, 2 * math.pi)
                cr.set_source_rgb(1.0, 1.0, 1.0)
                cr.fill_preserve()
                cr.set_line_width(0.9 * s)
                cr.set_source_rgba(0.65, 0.70, 0.85, 0.5)
                cr.stroke()
                cr.arc(0, 0, 2.2 * s, 0, 2 * math.pi)
                cr.set_source_rgba(1.0, 0.55, 0.72, 0.85)
                cr.fill()
                cr.restore()

        else:
            # Resting little paws / swinging while walking / jumping / stretching / boxing / loaf
            arm_swing_l = -math.sin(self.walk_phase) * 3.5 * s if (self.is_walking and self.mood == "normal" and self.activity == "walk") else 0.0
            arm_swing_r = math.sin(self.walk_phase) * 3.5 * s if (self.is_walking and self.mood == "normal" and self.activity == "walk") else 0.0

            if self.arm_spread > 0:
                paws_data = [
                    (-1, -10.5 * s - self.arm_spread * 8.0 * s, (20.0 - self.arm_spread * 15.0) * s),
                    (1, 10.5 * s + self.arm_spread * 8.0 * s, (20.0 - self.arm_spread * 15.0) * s)
                ]
            elif self.stretch_amount > 0:
                paws_data = [
                    (-1, -14.0 * s, (20.0 + self.stretch_amount * 8.5) * s),
                    (1, 14.0 * s, (20.0 + self.stretch_amount * 8.5) * s)
                ]
            elif self.punch_l > 0 or self.punch_r > 0:
                paws_data = [
                    (-1, -10.5 * s - self.punch_l, 20.0 * s - self.punch_l * 0.3),
                    (1, 10.5 * s + self.punch_r, 20.0 * s - self.punch_r * 0.3)
                ]
            elif self.activity == "loaf":
                paws_data = [
                    (-1, -5.0 * s, 23.0 * s),
                    (1, 5.0 * s, 23.0 * s)
                ]
            else:
                paws_data = [
                    (-1, -10.5 * s + arm_swing_l, 20.0 * s),
                    (1, 10.5 * s + arm_swing_r, 20.0 * s)
                ]

            for sign, px, py in paws_data:
                cr.save()
                cr.translate(px, py)
                cr.scale(1.0, 0.92)
                cr.arc(0, 0, 4.5 * s, 0, 2 * math.pi)
                pat_paw = cairo.LinearGradient(0, -4 * s, 0, 4 * s)
                pat_paw.add_color_stop_rgb(0, 1.0, 1.0, 1.0)
                pat_paw.add_color_stop_rgb(1, 0.90, 0.92, 0.98)
                cr.set_source(pat_paw)
                cr.fill_preserve()
                cr.set_line_width(0.9 * s)
                cr.set_source_rgba(0.65, 0.70, 0.85, 0.5)
                cr.stroke()

                cr.arc(0, 0.8 * s, 2.0 * s, 0, 2 * math.pi)
                cr.set_source_rgba(1.0, 0.55, 0.72, 0.8)
                cr.fill()

                cr.set_line_width(0.7 * s)
                cr.set_source_rgba(0.7, 0.75, 0.88, 0.6)
                cr.move_to(-1.5 * s, -2.5 * s)
                cr.line_to(-1.5 * s, -0.5 * s)
                cr.move_to(1.5 * s, -2.5 * s)
                cr.line_to(1.5 * s, -0.5 * s)
                cr.stroke()
                cr.restore()

        cr.restore() # End overall translate

        self.draw_particles_and_orb(cr, cx, cy, s)


class LexiRadialDonutWindow(Gtk.Window):
    """
    Filee Radial Donut Menu:
    Transparent, topmost floating circular donut window that opens at cursor.
    Features:
      - Clean Cairo segmented ring geometry with adaptive presets per file
      - Hovered slice pops out with neon glowing accent border
      - Instant offline background conversion upon click or file drop
      - Animated spinning stardust progress ring during conversion
      - Center hub displays Lexi's mascot face and status
      - Closes on Escape or click outside
    """
    def __init__(self, file_path=None, on_converted_cb=None, spawn_pos=None):
        super().__init__(title="Filee Radial Donut")
        self.set_decorated(False)
        self.set_resizable(False)
        self.set_default_size(340, 340)
        self.add_css_class('lexi-donut-win')

        self.file_path = file_path
        self.on_converted_cb = on_converted_cb
        self.spawn_pos = spawn_pos
        self.presets = []
        if file_path:
            self.presets = converter.get_presets_for_file(file_path)
        if not self.presets:
            self.presets = [
                {"id": "webp", "label": "WEBP", "desc": "Web-optimized image", "ext": "webp"},
                {"id": "png", "label": "PNG", "desc": "Lossless crisp image", "ext": "png"},
                {"id": "jpg", "label": "JPG", "desc": "Standard photo 90%", "ext": "jpg"},
                {"id": "pdf", "label": "PDF", "desc": "Clean formatted PDF", "ext": "pdf"},
                {"id": "docx", "label": "DOCX", "desc": "Editable Word document", "ext": "docx"},
                {"id": "zip", "label": "ZIP", "desc": "Compressed archive", "ext": "zip"},
            ]

        self.hovered_idx = -1
        self.is_converting = False
        self.anim_phase = 0.0
        self.convert_status = ""
        self.convert_result = None
        self.anim_timer_id = None

        self.darea = Gtk.DrawingArea()
        self.darea.set_content_width(340)
        self.darea.set_content_height(340)
        self.darea.set_draw_func(self.on_draw)
        self.set_child(self.darea)

        # Motion controller
        motion = Gtk.EventControllerMotion()
        motion.connect('motion', self.on_mouse_motion)
        motion.connect('leave', self.on_mouse_leave)
        self.darea.add_controller(motion)

        # Click controller
        click = Gtk.GestureClick()
        click.connect('pressed', self.on_clicked)
        self.darea.add_controller(click)

        # Drop target
        drop_target = Gtk.DropTarget(actions=Gdk.DragAction.COPY)
        drop_target.set_gtypes([Gdk.FileList, Gio.File, str])
        drop_target.set_preload(True)
        drop_target.connect('enter', self.on_drag_enter)
        drop_target.connect('drop', self.on_drag_drop)
        self.darea.add_controller(drop_target)

        # Key controller (Esc to close)
        key_ctrl = Gtk.EventControllerKey()
        key_ctrl.connect('key-pressed', self.on_key_pressed)
        self.add_controller(key_ctrl)

        self.connect('realize', self.on_realize)

    def on_realize(self, _):
        surface = self.get_surface()
        if surface and hasattr(surface, 'get_xid'):
            try:
                xid = surface.get_xid()
                X11Helper.get().make_always_on_top_and_sticky(xid)
                if hasattr(surface, 'move_to_desktop'):
                    surface.move_to_desktop(0xFFFFFFFF)
            except Exception:
                pass
        GLib.timeout_add(20, self.position_at_cursor)

    def position_at_cursor(self):
        if self.spawn_pos:
            mx, my = self.spawn_pos
        else:
            pt = X11Helper.get().get_pointer()
            if not pt:
                pt = (960, 540)
            mx, my = pt
        x = max(20, min(1920 - 360, mx - 170))
        y = max(40, min(1080 - 360, my - 170))
        surface = self.get_surface()
        if surface and hasattr(surface, 'get_xid'):
            X11Helper.get().move_window(surface.get_xid(), int(x), int(y))
        return GLib.SOURCE_REMOVE

    def on_key_pressed(self, ctrl, keyval, keycode, state):
        if keyval == Gdk.KEY_Escape:
            self.close_donut()
            return True
        return False

    def on_mouse_leave(self, ctrl):
        if not self.is_converting and self.hovered_idx != -1:
            self.hovered_idx = -1
            self.darea.queue_draw()

    def on_mouse_motion(self, ctrl, x, y):
        if self.is_converting:
            return
        cx, cy = 170.0, 170.0
        dx = x - cx
        dy = y - cy
        dist = math.hypot(dx, dy)
        n = len(self.presets)
        new_hover = -1
        if dist < 62.0:
            new_hover = -2  # center hole
        elif 68.0 <= dist <= 148.0 and n > 0:
            angle = math.atan2(dy, dx) + math.pi / 2
            if angle < 0:
                angle += 2 * math.pi
            span = 2 * math.pi / n
            new_hover = int(math.floor(angle / span)) % n

        if new_hover != self.hovered_idx:
            self.hovered_idx = new_hover
            self.darea.queue_draw()

    def on_clicked(self, gesture, n_press, x, y):
        if self.is_converting:
            return
        if self.hovered_idx >= 0 and self.hovered_idx < len(self.presets):
            pr = self.presets[self.hovered_idx]
            if self.file_path:
                self.start_conversion(self.file_path, pr)
        elif self.hovered_idx == -2:
            if self.convert_result and self.convert_result.get("output_path"):
                out_p = self.convert_result["output_path"]
                folder = out_p if os.path.isdir(out_p) else os.path.dirname(out_p)
                subprocess.Popen(['xdg-open', folder])
                self.close_donut()
            elif self.file_path:
                self.close_donut()
        else:
            self.close_donut()

    def on_drag_enter(self, target, x, y):
        return Gdk.DragAction.COPY

    def on_drag_drop(self, target, value, x, y):
        paths = []
        if value is not None:
            if hasattr(value, 'get_files'):
                for gf in value.get_files():
                    p = gf.get_path()
                    if p and os.path.exists(p):
                        paths.append(p)
            elif isinstance(value, Gio.File):
                p = value.get_path()
                if p and os.path.exists(p):
                    paths.append(p)
            elif isinstance(value, str):
                for line in value.strip().splitlines():
                    p = line.strip().replace('file://', '')
                    if os.path.exists(p):
                        paths.append(p)
        if paths:
            self.file_path = paths[0]
            self.presets = converter.get_presets_for_file(self.file_path)
            cx, cy = 170.0, 170.0
            dx = x - cx
            dy = y - cy
            dist = math.hypot(dx, dy)
            n = len(self.presets)
            if 68.0 <= dist <= 148.0 and n > 0:
                angle = math.atan2(dy, dx) + math.pi / 2
                if angle < 0:
                    angle += 2 * math.pi
                span = 2 * math.pi / n
                idx = int(math.floor(angle / span)) % n
                self.start_conversion(self.file_path, self.presets[idx])
            else:
                self.darea.queue_draw()
            return True
        return False

    def start_conversion(self, file_path, preset):
        self.is_converting = True
        self.convert_status = f"Converting to {preset['label']}..."
        if not self.anim_timer_id:
            self.anim_timer_id = GLib.timeout_add(33, self._anim_tick)
        self.darea.queue_draw()

        def done(res):
            GLib.idle_add(self._on_convert_done, res)

        converter.async_convert(file_path, preset['ext'], preset.get('option'), on_complete=done)

    def _anim_tick(self):
        if not self.is_converting:
            self.anim_timer_id = None
            return GLib.SOURCE_REMOVE
        self.anim_phase += 0.15
        self.darea.queue_draw()
        return GLib.SOURCE_CONTINUE

    def _on_convert_done(self, res):
        self.is_converting = False
        self.convert_result = res
        if res["success"]:
            self.convert_status = f"Saved {res.get('summary', '')}"
            if self.on_converted_cb:
                self.on_converted_cb(res)
            GLib.timeout_add(2000, self.close_donut)
        else:
            self.convert_status = f"{res.get('error', 'Error')[:20]}"
            GLib.timeout_add(2500, self.close_donut)
        self.darea.queue_draw()
        return GLib.SOURCE_REMOVE

    def close_donut(self):
        if self.anim_timer_id:
            GLib.source_remove(self.anim_timer_id)
            self.anim_timer_id = None
        self.destroy()

    def on_draw(self, area, cr, width, height):
        cx, cy = 170.0, 170.0
        r_in = 70.0
        r_out = 140.0
        r_center = 62.0

        n = len(self.presets)
        span = 2 * math.pi / max(1, n)
        gap = 0.035

        # 1. Subtle Outer Ambient Glow
        cr.save()
        cr.arc(cx, cy, r_out + 18, 0, 2 * math.pi)
        pat = cairo.RadialGradient(cx, cy, r_in, cx, cy, r_out + 18)
        pat.add_color_stop_rgba(0.0, 0.45, 0.22, 0.85, 0.20)
        pat.add_color_stop_rgba(1.0, 0.0, 0.0, 0.0, 0.0)
        cr.set_source(pat)
        cr.fill()
        cr.restore()

        # 2. Draw Sectors
        for i, p in enumerate(self.presets):
            start_a = i * span - math.pi / 2 + gap
            end_a = (i + 1) * span - math.pi / 2 - gap
            mid_a = (start_a + end_a) / 2.0

            is_hov = (i == self.hovered_idx)
            cur_rout = r_out + (8.0 if is_hov else 0.0)
            cur_rin = r_in - (2.0 if is_hov else 0.0)

            cr.new_path()
            cr.arc(cx, cy, cur_rout, start_a, end_a)
            cr.arc_negative(cx, cy, cur_rin, end_a, start_a)
            cr.close_path()

            if is_hov:
                grad = cairo.RadialGradient(cx, cy, cur_rin, cx, cy, cur_rout)
                grad.add_color_stop_rgba(0.0, 0.28, 0.14, 0.45, 0.98)
                grad.add_color_stop_rgba(1.0, 0.50, 0.22, 0.78, 0.98)
                cr.set_source(grad)
                cr.fill_preserve()
                cr.set_source_rgba(0.85, 0.60, 1.0, 0.95)
                cr.set_line_width(2.0)
                cr.stroke()
            else:
                cr.set_source_rgba(0.08, 0.06, 0.14, 0.94)
                cr.fill_preserve()
                cr.set_source_rgba(0.40, 0.35, 0.55, 0.40)
                cr.set_line_width(1.2)
                cr.stroke()

            # Label
            cr.new_path()
            label_r = (cur_rin + cur_rout) / 2.0
            tx = cx + label_r * math.cos(mid_a)
            ty = cy + label_r * math.sin(mid_a)

            cr.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
            cr.set_font_size(13.0 if is_hov else 11.5)
            ext = cr.text_extents(p["label"])
            cr.move_to(tx - ext.width / 2 - ext.x_bearing, ty - ext.height / 2 - ext.y_bearing)
            if is_hov:
                cr.set_source_rgba(1.0, 1.0, 1.0, 1.0)
            else:
                cr.set_source_rgba(0.88, 0.85, 0.95, 0.85)
            cr.show_text(p["label"])

        # 3. Rotating Stardust Progress Arc if converting
        if self.is_converting:
            cr.new_path()
            arc_start = self.anim_phase
            arc_end = arc_start + 1.2
            cr.arc(cx, cy, r_out + 12, arc_start, arc_end)
            cr.set_source_rgba(0.9, 0.6, 1.0, 0.95)
            cr.set_line_width(3.5)
            cr.stroke()

        # 4. Center Hub
        cr.new_path()
        cr.arc(cx, cy, r_center, 0, 2 * math.pi)
        cr.set_source_rgba(0.04, 0.03, 0.08, 0.98)
        cr.fill_preserve()
        cr.set_source_rgba(0.58, 0.35, 0.95, 0.6)
        cr.set_line_width(1.5)
        cr.stroke()

        # Center Text Content
        cr.new_path()
        cr.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)

        if self.is_converting:
            cr.set_font_size(12.0)
            ext = cr.text_extents("Converting...")
            cr.move_to(cx - ext.width / 2 - ext.x_bearing, cy - 8 - ext.y_bearing)
            cr.set_source_rgba(0.9, 0.6, 1.0, 1.0)
            cr.show_text("Converting...")

            cr.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
            cr.set_font_size(9.5)
            ext2 = cr.text_extents(self.convert_status[:18])
            cr.move_to(cx - ext2.width / 2 - ext2.x_bearing, cy + 12 - ext2.y_bearing)
            cr.set_source_rgba(0.7, 0.7, 0.8, 0.9)
            cr.show_text(self.convert_status[:18])

        elif self.convert_result:
            cr.set_font_size(12.0)
            ext = cr.text_extents("Done!")
            cr.move_to(cx - ext.width / 2 - ext.x_bearing, cy - 8 - ext.y_bearing)
            cr.set_source_rgba(0.4, 0.9, 0.6, 1.0)
            cr.show_text("Done!")

            cr.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
            cr.set_font_size(8.5)
            ext2 = cr.text_extents("Click to Open")
            cr.move_to(cx - ext2.width / 2 - ext2.x_bearing, cy + 12 - ext2.y_bearing)
            cr.set_source_rgba(0.8, 0.8, 0.9, 0.9)
            cr.show_text("Click to Open")

        elif self.hovered_idx >= 0 and self.hovered_idx < len(self.presets):
            pr = self.presets[self.hovered_idx]
            cr.set_font_size(13.0)
            ext = cr.text_extents(pr['label'])
            cr.move_to(cx - ext.width / 2 - ext.x_bearing, cy - 8 - ext.y_bearing)
            cr.set_source_rgba(0.9, 0.6, 1.0, 1.0)
            cr.show_text(pr['label'])

            cr.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
            cr.set_font_size(8.5)
            desc_text = pr.get('desc', '')[:19]
            ext2 = cr.text_extents(desc_text)
            cr.move_to(cx - ext2.width / 2 - ext2.x_bearing, cy + 12 - ext2.y_bearing)
            cr.set_source_rgba(0.7, 0.7, 0.8, 0.9)
            cr.show_text(desc_text)

        else:
            fname = Path(self.file_path).name[:12] if self.file_path else "Drop File"
            cr.set_font_size(12.0)
            ext = cr.text_extents("Filee Donut")
            cr.move_to(cx - ext.width / 2 - ext.x_bearing, cy - 8 - ext.y_bearing)
            cr.set_source_rgba(0.9, 0.7, 1.0, 1.0)
            cr.show_text("Filee Donut")

            cr.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
            cr.set_font_size(9.0)
            ext2 = cr.text_extents(fname)
            cr.move_to(cx - ext2.width / 2 - ext2.x_bearing, cy + 12 - ext2.y_bearing)
            cr.set_source_rgba(0.65, 0.65, 0.75, 0.9)
            cr.show_text(fname)


class LexiEqualizerWidget(Gtk.DrawingArea):
    """
    Animated Music / Spotify neon equalizer bars (4 bars with rounded tips).
    Smoothly pulses when media is playing, settles to resting dots when paused.
    """
    def __init__(self, width=20, height=16):
        super().__init__()
        self.set_size_request(width, height)
        self.set_draw_func(self._draw)
        self.is_playing = False
        self.phase = 0.0
        self.anim_id = None
        self.bar_count = 4

    def set_playing(self, playing: bool):
        if self.is_playing == playing:
            return
        self.is_playing = playing
        if playing and not self.anim_id:
            self.anim_id = GLib.timeout_add(70, self._on_tick)
        elif not playing:
            self.queue_draw()

    def _on_tick(self):
        if not self.is_playing:
            self.anim_id = None
            self.queue_draw()
            return GLib.SOURCE_REMOVE
        self.phase += 0.28
        self.queue_draw()
        return GLib.SOURCE_CONTINUE

    def _draw(self, area, cr, width, height):
        n = self.bar_count
        bar_w = 2.4
        gap = 2.0
        total_w = n * bar_w + (n - 1) * gap
        start_x = (width - total_w) / 2.0
        cr.set_source_rgba(0.11, 0.73, 0.33, 0.95)  # Spotify neon green
        for i in range(n):
            if self.is_playing:
                amp = (math.sin(self.phase * 1.5 + i * 1.2) + 1.0) / 2.0
                bar_h = 3.0 + amp * (height - 6.0)
            else:
                bar_h = 2.5
            x = start_x + i * (bar_w + gap)
            y = (height - bar_h) / 2.0
            r = bar_w / 2.0
            cr.new_sub_path()
            cr.arc(x + r, y + r, r, math.pi, 1.5 * math.pi)
            cr.arc(x + bar_w - r, y + r, r, 1.5 * math.pi, 2 * math.pi)
            cr.arc(x + bar_w - r, y + bar_h - r, r, 0, 0.5 * math.pi)
            cr.arc(x + r, y + bar_h - r, r, 0.5 * math.pi, math.pi)
            cr.close_path()
            cr.fill()


class DynamicIslandEventHub:
    """
    Background daemon for Lexi Dynamic Island:
    - Real-time MPRIS media tracking (Spotify, Chrome, Firefox, VLC)
    - Power / Battery state monitoring (/sys/class/power_supply)
    - Personal Wi-Fi Hotspot status & client connections (nmcli & ip neigh)
    - Bluetooth audio / peripherals (bluetoothctl)
    """
    def __init__(self, island_win):
        self.island_win = island_win
        self.running = True
        self.last_media_key = None
        self.last_ac_online = None
        self.last_low_battery_warned = False
        self.last_hotspot_active = False
        self.last_hotspot_clients = 0
        self.last_bt_devices = set()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def stop(self):
        self.running = False

    def _cache_art(self, art_url):
        if not art_url:
            return ""
        if art_url.startswith("file://"):
            return art_url[7:]
        if os.path.exists(art_url):
            return art_url
        if art_url.startswith("http://") or art_url.startswith("https://"):
            try:
                url_hash = hashlib.md5(art_url.encode()).hexdigest()
                cache_path = f"/tmp/lexi_art_{url_hash}.jpg"
                thumb_path = f"/tmp/lexi_art_thumb_{url_hash}.png"
                if os.path.exists(thumb_path):
                    return thumb_path
                if not os.path.exists(cache_path):
                    req = urllib.request.Request(art_url, headers={'User-Agent': 'Mozilla/5.0'})
                    with urllib.request.urlopen(req, timeout=2.5) as resp, open(cache_path, 'wb') as f:
                        f.write(resp.read())
                if os.path.exists(cache_path):
                    try:
                        im = Image.open(cache_path)
                        im.thumbnail((48, 48), Image.Resampling.LANCZOS)
                        im.save(thumb_path, "PNG")
                        return thumb_path
                    except Exception:
                        return cache_path
            except Exception:
                return ""
        return ""

    def _check_media(self):
        try:
            env = dict(os.environ)
            uid = os.getuid()
            if 'DBUS_SESSION_BUS_ADDRESS' not in env:
                env['DBUS_SESSION_BUS_ADDRESS'] = f"unix:path=/run/user/{uid}/bus"
            if 'XDG_RUNTIME_DIR' not in env:
                env['XDG_RUNTIME_DIR'] = f"/run/user/{uid}"

            res = subprocess.run(
                ['playerctl', 'metadata', '--format', '{{status}}@@{{title}}@@{{artist}}@@{{mpris:artUrl}}@@{{playerName}}'],
                capture_output=True, text=True, timeout=1.2, env=env
            )
            if res.returncode == 0 and res.stdout.strip():
                first_line = res.stdout.strip().splitlines()[0]
                parts = first_line.split('@@')
                status = parts[0] if len(parts) > 0 else "Paused"
                title = parts[1] if len(parts) > 1 else ""
                artist = parts[2] if len(parts) > 2 else ""
                art_url = parts[3] if len(parts) > 3 else ""
                player = parts[4] if len(parts) > 4 else "media"

                if player.lower() == 'spotify' and (not artist or title.lower() in ('advertisement', 'spotify', 'learn more', 'listen to music, ad-free.', 'ad')):
                    title = "Spotify Ad"
                    artist = "Sponsored"

                art_path = self._cache_art(art_url) if art_url else ""
                media_key = f"{status}:{title}:{artist}:{art_path}"
                if media_key != self.last_media_key:
                    self.last_media_key = media_key
                    media_info = {
                        'active': True,
                        'status': status,
                        'title': title,
                        'artist': artist,
                        'art_path': art_path,
                        'player': player
                    }
                    GLib.idle_add(self.island_win.set_media_state, media_info)
            else:
                if self.last_media_key is not None:
                    self.last_media_key = None
                    GLib.idle_add(self.island_win.set_media_state, {'active': False})
        except Exception:
            pass

    def _check_power(self):
        try:
            adp_file = "/sys/class/power_supply/ADP0/online"
            bat_file = "/sys/class/power_supply/BAT0/capacity"
            if not os.path.exists(adp_file):
                return
            with open(adp_file) as f:
                ac_online = int(f.read().strip())
            cap = 100
            if os.path.exists(bat_file):
                with open(bat_file) as f:
                    cap = int(f.read().strip())

            if self.last_ac_online is not None and ac_online != self.last_ac_online:
                if ac_online == 1:
                    GLib.idle_add(self.island_win.trigger_island_alert, "⚡", f"Charging {cap}%", "green")
                else:
                    GLib.idle_add(self.island_win.trigger_island_alert, "🔋", f"On Battery {cap}%", "silver")
            self.last_ac_online = ac_online

            if ac_online == 0 and cap <= 20:
                if not self.last_low_battery_warned:
                    self.last_low_battery_warned = True
                    GLib.idle_add(self.island_win.trigger_island_alert, "🪫", f"Low Battery {cap}%", "amber")
            elif cap > 25:
                self.last_low_battery_warned = False
        except Exception:
            pass

    def _check_hotspot(self):
        try:
            res = subprocess.run(['nmcli', '-t', '-f', 'NAME,TYPE', 'con', 'show', '--active'], capture_output=True, text=True, timeout=1.5)
            is_ap = False
            if res.returncode == 0:
                for line in res.stdout.strip().splitlines():
                    if ':' in line:
                        cname, ctype = line.split(':', 1)
                        if ctype in ('wifi', '802-11-wireless'):
                            m_res = subprocess.run(['nmcli', '-t', '-f', '802-11-wireless.mode', 'con', 'show', cname], capture_output=True, text=True, timeout=1.2)
                            if 'ap' in m_res.stdout.lower():
                                is_ap = True
                                break

            client_count = 0
            if is_ap:
                neigh = subprocess.run(['ip', 'neigh'], capture_output=True, text=True, timeout=1.0)
                if neigh.returncode == 0:
                    for nl in neigh.stdout.strip().splitlines():
                        if 'REACHABLE' in nl or 'DELAY' in nl or 'STALE' in nl:
                            client_count += 1

            if is_ap != self.last_hotspot_active or (is_ap and client_count != self.last_hotspot_clients):
                if is_ap and not self.last_hotspot_active:
                    GLib.idle_add(self.island_win.trigger_island_alert, "🔗", "Hotspot Active", "cyan")
                elif is_ap and client_count > self.last_hotspot_clients:
                    msg = f"Hotspot: {client_count} Connected"
                    GLib.idle_add(self.island_win.trigger_island_alert, "🔗", msg, "cyan")
                elif not is_ap and self.last_hotspot_active:
                    GLib.idle_add(self.island_win.trigger_island_alert, "🔗", "Hotspot Off", "silver")
                self.last_hotspot_active = is_ap
                self.last_hotspot_clients = client_count
        except Exception:
            pass

    def _check_bluetooth(self):
        try:
            res = subprocess.run(['bluetoothctl', 'devices', 'Connected'], capture_output=True, text=True, timeout=1.5)
            if res.returncode == 0:
                current_devs = set()
                for line in res.stdout.strip().splitlines():
                    parts = line.split(maxsplit=2)
                    if len(parts) >= 3 and parts[0] == "Device":
                        current_devs.add(parts[2])
                new_devs = current_devs - self.last_bt_devices
                for d in new_devs:
                    GLib.idle_add(self.island_win.trigger_island_alert, "🎧", f"Connected: {d[:14]}", "purple")
                self.last_bt_devices = current_devs
        except Exception:
            pass

    def _run(self):
        import time
        cycle = 0
        while self.running:
            try:
                # Dynamic cadence: poll media faster (1s) when music is playing, slower (2s) when idle
                is_media_active = bool(self.island_win.last_media_info and self.island_win.last_media_info.get('active'))
                if is_media_active or cycle % 2 == 0:
                    self._check_media()

                # Power / battery state: check every 4 seconds (pure sysfs file read, 0 CPU)
                if cycle % 4 == 0:
                    self._check_power()

                # Hotspot connection check: every 8 seconds
                if cycle % 8 == 0:
                    self._check_hotspot()

                # Bluetooth audio peripherals check: every 10 seconds
                if cycle % 10 == 0:
                    self._check_bluetooth()
            except Exception:
                pass
            cycle += 1
            time.sleep(1.0)


class LexiDynamicIslandWindow(Gtk.Window):
    """
    Lexi Dynamic Island (Liquid Morphing Pill):
    Floats at top of screen as a deep OLED black pill with neon rim light.
    Morphs seamlessly between:
      1. Compact Resting Pill: Mini Lexi mascot, dynamic live status, glowing pulse dot.
      2. Expanded Action Island (on hover): Smoothly slides open quick chips:
         [💬 Copilot] [◨ Side Panel] [✂️ Snip] [🐾 Detach Pet]
      3. Live Media Playback: Mini album art, track/artist title, animated neon equalizer waveform.
         Hover reveals media controls: [⏮] [⏯] [⏭] [✨ Copilot]
      4. System Event Alerts: Battery charging/discharging, low battery, Hotspot connections, Bluetooth.
      5. Drop-to-Convert Filee Bar: Dropping a file opens instant offline format chips:
         [📄 photo.png] [WEBP] [JPG] [PDF] [🍩 Donut] [💬 Ask AI] [✕]
    """
    def __init__(self, copilot_win):
        super().__init__(title="Lexi Dynamic Island")
        self.copilot_win = copilot_win
        self.set_decorated(False)
        self.set_resizable(False)
        self.set_focusable(False)  # NEVER steal keyboard focus from active desktop apps or Copilot
        self.set_default_size(680, 52)
        self.add_css_class('lexi-island-win')

        self.current_mode = "compact"  # "compact", "actions", "media", "alert", "convert"
        self.active_file_paths = []
        self.dismiss_timer_id = None
        self.alert_timer_id = None
        self.last_media_info = None

        # Outer centering container
        outer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        outer.set_halign(Gtk.Align.CENTER)
        outer.set_valign(Gtk.Align.CENTER)
        self.set_child(outer)

        # Dynamic Island Pill
        self.pill = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.pill.add_css_class('dynamic-island-pill')
        self.pill.set_valign(Gtk.Align.CENTER)
        outer.append(self.pill)

        # 1. Mascot Box (Resting idle mode: avatar + status)
        self.mascot_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.mascot_box.set_valign(Gtk.Align.CENTER)
        self.pill.append(self.mascot_box)

        # Mini animated Lexi mascot (24x24 avatar with blinking anime eyes)
        self.mini_mascot = LexiMascotWidget(width=24, height=24, is_mini=True)
        self.mascot_box.append(self.mini_mascot)

        # Dynamic Status text
        self.status_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.status_box.set_valign(Gtk.Align.CENTER)
        self.mascot_box.append(self.status_box)

        self.status_label = Gtk.Label(label="✨ Lexi")
        self.status_label.add_css_class('dynamic-island-label')
        self.status_box.append(self.status_label)

        self.pulse_dot = Gtk.Label(label="●")
        self.pulse_dot.add_css_class('dynamic-island-dot')
        self.status_box.append(self.pulse_dot)

        # Click on mascot box expands Copilot card
        mascot_click = Gtk.GestureClick()
        mascot_click.connect('pressed', lambda *_: self.on_expand_copilot())
        self.mascot_box.add_controller(mascot_click)

        # 2. Expanded action buttons revealer (morphs open on hover in compact mode)
        self.actions_revealer = Gtk.Revealer()
        self.actions_revealer.set_transition_type(Gtk.RevealerTransitionType.SLIDE_LEFT)
        self.actions_revealer.set_transition_duration(200)
        self.actions_revealer.set_reveal_child(False)
        self.pill.append(self.actions_revealer)

        actions_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        actions_box.set_valign(Gtk.Align.CENTER)
        self.actions_revealer.set_child(actions_box)

        def make_btn(label, css_class, tooltip, cb):
            b = Gtk.Button(label=label)
            b.set_focusable(False)
            b.add_css_class(css_class)
            b.set_tooltip_text(tooltip)
            b.connect('clicked', lambda *_: cb())
            return b

        actions_box.append(make_btn("💬 Copilot", "island-action-btn island-btn-primary", "Open floating center copilot card", self.on_expand_copilot))
        actions_box.append(make_btn("⚡ Beam", "island-action-btn island-btn-secondary", "Local Wi-Fi file sharing & PWA status", self.on_show_beam_info))
        actions_box.append(make_btn("◨ Side Panel", "island-action-btn island-btn-secondary", "Open vertical Side Panel", self.on_expand_side_panel))
        actions_box.append(make_btn("✂️ Snip", "island-action-btn island-btn-secondary", "Interactive region snip", self.on_trigger_snip))
        actions_box.append(make_btn("🐾 Pet", "island-action-btn island-btn-secondary", "Detach Lexi to roam desktop", self.on_detach_pet))

        # 3. Media Mode Box (Active when music is playing)
        self.media_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.media_box.set_valign(Gtk.Align.CENTER)
        self.media_box.set_visible(False)
        self.pill.append(self.media_box)

        # Container for artwork and title (tapping brings Spotify to focus)
        self.media_info_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
        self.media_info_box.set_valign(Gtk.Align.CENTER)
        self.media_box.append(self.media_info_box)

        self.media_art = Gtk.Picture()
        self.media_art.set_size_request(22, 22)
        self.media_art.set_can_shrink(True)
        if hasattr(Gtk, 'ContentFit'):
            self.media_art.set_content_fit(Gtk.ContentFit.COVER)
        self.media_art.add_css_class('island-media-art')
        self.media_art.set_visible(False)
        self.media_info_box.append(self.media_art)

        self.media_label = Gtk.Label(label="")
        self.media_label.add_css_class('island-media-title')
        self.media_label.set_ellipsize(Pango.EllipsizeMode.END)
        self.media_label.set_max_width_chars(32)
        self.media_info_box.append(self.media_label)

        # Click on media info box raises player app
        media_click = Gtk.GestureClick()
        media_click.connect('pressed', self.on_media_info_click)
        self.media_info_box.add_controller(media_click)

        self.media_eq = LexiEqualizerWidget(width=20, height=16)
        self.media_box.append(self.media_eq)

        # 4. Media Controls Revealer (morphs open on hover during media playback)
        self.media_controls_revealer = Gtk.Revealer()
        self.media_controls_revealer.set_transition_type(Gtk.RevealerTransitionType.SLIDE_LEFT)
        self.media_controls_revealer.set_transition_duration(200)
        self.media_controls_revealer.set_reveal_child(False)
        self.pill.append(self.media_controls_revealer)

        media_controls_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        media_controls_box.set_valign(Gtk.Align.CENTER)
        self.media_controls_revealer.set_child(media_controls_box)

        self.media_prev_btn = make_btn("⏮", "island-media-btn", "Previous track", lambda: self.on_media_action('previous'))
        self.media_play_btn = make_btn("⏸", "island-media-btn", "Play / Pause", lambda: self.on_media_action('play-pause'))
        self.media_next_btn = make_btn("⏭", "island-media-btn", "Next track", lambda: self.on_media_action('next'))
        self.media_copilot_btn = make_btn("✨", "island-action-btn island-btn-primary", "Open Copilot", self.on_expand_copilot)

        media_controls_box.append(self.media_prev_btn)
        media_controls_box.append(self.media_play_btn)
        media_controls_box.append(self.media_next_btn)
        media_controls_box.append(self.media_copilot_btn)

        # 5. System Alert Box (Charging, Hotspot, Bluetooth, Battery)
        self.alert_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.alert_box.set_valign(Gtk.Align.CENTER)
        self.alert_box.set_visible(False)
        self.pill.append(self.alert_box)

        self.alert_icon = Gtk.Label(label="⚡")
        self.alert_icon.add_css_class('island-alert-icon')
        self.alert_box.append(self.alert_icon)

        self.alert_label = Gtk.Label(label="")
        self.alert_label.add_css_class('island-alert-text')
        self.alert_box.append(self.alert_label)

        alert_click = Gtk.GestureClick()
        alert_click.connect('pressed', self.on_alert_click)
        self.alert_box.add_controller(alert_click)

        # 6. Filee Converter Revealer (morphs open on file drop)
        self.convert_revealer = Gtk.Revealer()
        self.convert_revealer.set_transition_type(Gtk.RevealerTransitionType.SLIDE_LEFT)
        self.convert_revealer.set_transition_duration(220)
        self.convert_revealer.set_reveal_child(False)
        self.pill.append(self.convert_revealer)

        self.convert_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        self.convert_box.set_valign(Gtk.Align.CENTER)
        self.convert_revealer.set_child(self.convert_box)

        # Mouse hover motion: smooth morphing expansion
        motion = Gtk.EventControllerMotion()
        motion.connect('enter', self.on_hover_enter)
        motion.connect('leave', self.on_hover_leave)
        self.pill.add_controller(motion)

        # File drag & drop directly onto Dynamic Island
        drop_target = Gtk.DropTarget(actions=Gdk.DragAction.COPY)
        drop_target.set_gtypes([Gdk.FileList, Gio.File, str])
        drop_target.set_preload(True)
        drop_target.connect('enter', self.on_drag_enter)
        drop_target.connect('leave', self.on_drag_leave)
        drop_target.connect('drop', self.on_drag_drop)
        self.pill.add_controller(drop_target)

        # Start dynamic system event hub
        self.event_hub = DynamicIslandEventHub(self)

        # Start Lexi Beam / Drop LAN sharing service
        self.drop_server = None
        if lexi_drop:
            try:
                self.drop_server = lexi_drop.get_lexi_drop_instance()
                self.drop_server.on_peer_joined = lambda dev, ip: GLib.idle_add(
                    self.trigger_island_alert, "📱" if ("Phone" in dev or "Android" in dev) else "💻", f"{dev} Connected", "green", 3.5
                )
                self.drop_server.on_file_received = lambda fp, sz, sp: GLib.idle_add(
                    self.show_received_file_bar, fp, sz, sp
                )
                self.drop_server.on_p2p_incoming_request = lambda info: GLib.idle_add(
                    self.on_p2p_incoming_from_peer, info
                )
                self.drop_server.on_transfer_progress = lambda tok, d, cur, tot, spd, pct: GLib.idle_add(
                    self.on_transfer_progress_update, tok, d, spd, pct
                )
                self.drop_server.start_background()
            except Exception as e:
                log(f"Failed to start Lexi Drop server: {e}")

        self.connect('realize', self.on_realize)

    def update_input_region(self, mode="compact"):
        surface = self.get_surface()
        if not surface:
            return
        try:
            if mode == "convert":
                rect = cairo.RectangleInt(40, 2, 600, 48)
            elif mode == "actions":
                rect = cairo.RectangleInt(100, 2, 480, 48)
            elif mode == "media_expanded":
                rect = cairo.RectangleInt(140, 2, 400, 48)
            elif mode == "media_compact":
                rect = cairo.RectangleInt(170, 2, 340, 48)
            elif mode == "alert":
                rect = cairo.RectangleInt(230, 2, 220, 48)
            else:
                rect = cairo.RectangleInt(275, 2, 130, 48)
            region = cairo.Region(rect)
            surface.set_input_region(region)
        except Exception:
            pass

    def on_hover_enter(self, *args):
        if self.current_mode == "convert":
            return
        surface = self.get_surface()
        if surface and hasattr(surface, 'get_xid'):
            try:
                X11Helper.get().raise_window(surface.get_xid())
            except Exception:
                pass

        self.pill.add_css_class('dynamic-island-expanded')
        if self.current_mode == "media":
            self.update_input_region(mode="media_expanded")
            self.media_controls_revealer.set_reveal_child(True)
        elif self.current_mode == "compact":
            self.update_input_region(mode="actions")
            self.actions_revealer.set_reveal_child(True)

    def on_hover_leave(self, *args):
        if self.current_mode == "convert":
            return
        self.pill.remove_css_class('dynamic-island-expanded')
        if self.current_mode == "media":
            self.media_controls_revealer.set_reveal_child(False)
            GLib.timeout_add(220, lambda: self.update_input_region(mode="media_compact"))
        elif self.current_mode == "compact":
            self.actions_revealer.set_reveal_child(False)
            GLib.timeout_add(220, lambda: self.update_input_region(mode="compact"))

    def on_media_action(self, cmd):
        env = dict(os.environ)
        uid = os.getuid()
        if 'DBUS_SESSION_BUS_ADDRESS' not in env:
            env['DBUS_SESSION_BUS_ADDRESS'] = f"unix:path=/run/user/{uid}/bus"
        if 'XDG_RUNTIME_DIR' not in env:
            env['XDG_RUNTIME_DIR'] = f"/run/user/{uid}"
        try:
            player_arg = []
            if self.last_media_info and self.last_media_info.get('player'):
                player_arg = ['-p', self.last_media_info.get('player')]
            subprocess.Popen(['playerctl'] + player_arg + [cmd], env=env)
        except Exception as e:
            log(f"playerctl {cmd} error: {e}")

    def on_media_info_click(self, gesture, n_press, x, y):
        # Tapping the album art or track title brings Spotify / media player to focus
        try:
            subprocess.Popen(['xdotool', 'search', '--class', 'spotify', 'windowactivate'])
        except Exception:
            pass

    def on_alert_click(self, gesture, n_press, x, y):
        if self.alert_timer_id:
            GLib.source_remove(self.alert_timer_id)
            self.alert_timer_id = None
        self.alert_box.set_visible(False)
        if self.last_media_info and self.last_media_info.get('active'):
            self.current_mode = "media"
            self.media_box.set_visible(True)
            self.update_input_region(mode="media_compact")
        else:
            self.current_mode = "compact"
            self.mascot_box.set_visible(True)
            self.update_input_region(mode="compact")

    def set_media_state(self, media_info):
        self.last_media_info = media_info
        if self.current_mode in ("convert", "alert"):
            return False

        if not media_info.get('active'):
            if self.current_mode == "media":
                self.current_mode = "compact"
                self.media_box.set_visible(False)
                self.media_controls_revealer.set_reveal_child(False)
                self.mascot_box.set_visible(True)
                self.pill.remove_css_class('dynamic-island-media')
                self.update_input_region(mode="compact")
            return False

        status = media_info.get('status', 'Paused')
        title = media_info.get('title', 'Unknown Track')
        artist = media_info.get('artist', '')
        art_path = media_info.get('art_path', '')
        is_playing = (status.lower() == 'playing')

        text = f"{title} • {artist}" if artist else title
        self.media_label.set_text(text)
        self.media_label.set_tooltip_text(f"{title}\n{artist} ({status})")

        if art_path and os.path.exists(art_path):
            self.media_art.set_filename(art_path)
            self.media_art.set_visible(True)
        else:
            self.media_art.set_visible(False)

        self.media_eq.set_playing(is_playing)
        if hasattr(self, 'media_play_btn'):
            self.media_play_btn.set_label("⏸" if is_playing else "▶")
            self.media_play_btn.set_tooltip_text("Pause track" if is_playing else "Play track")

        if self.current_mode != "media":
            self.current_mode = "media"
            self.mascot_box.set_visible(False)
            self.actions_revealer.set_reveal_child(False)
            self.media_box.set_visible(True)
            self.pill.add_css_class('dynamic-island-media')
            self.update_input_region(mode="media_compact")

        return False

    def trigger_island_alert(self, icon, text, style="green", duration=3.5):
        if self.current_mode == "convert":
            return False

        if self.alert_timer_id:
            GLib.source_remove(self.alert_timer_id)
            self.alert_timer_id = None

        prev_mode = "media" if (self.last_media_info and self.last_media_info.get('active')) else "compact"
        self.current_mode = "alert"

        self.mascot_box.set_visible(False)
        self.actions_revealer.set_reveal_child(False)
        self.media_box.set_visible(False)
        self.media_controls_revealer.set_reveal_child(False)

        self.alert_icon.set_text(icon)
        self.alert_label.set_text(text)
        self.alert_box.set_visible(True)

        for s in ['green', 'cyan', 'amber', 'purple', 'silver']:
            self.pill.remove_css_class(f'island-alert-{s}')
        self.pill.add_css_class(f'island-alert-{style}')
        self.update_input_region(mode="alert")

        def _revert():
            self.alert_timer_id = None
            for s in ['green', 'cyan', 'amber', 'purple', 'silver']:
                self.pill.remove_css_class(f'island-alert-{s}')
            self.alert_box.set_visible(False)
            if prev_mode == "media" and self.last_media_info and self.last_media_info.get('active'):
                self.current_mode = "media"
                self.media_box.set_visible(True)
                self.pill.add_css_class('dynamic-island-media')
                self.update_input_region(mode="media_compact")
            else:
                self.current_mode = "compact"
                self.mascot_box.set_visible(True)
                self.update_input_region(mode="compact")
            return GLib.SOURCE_REMOVE

        self.alert_timer_id = GLib.timeout_add(int(duration * 1000), _revert)
        return False

    def destroy(self):
        if hasattr(self, 'event_hub') and self.event_hub:
            self.event_hub.stop()
        super().destroy()

    def on_expand_copilot(self):
        self.set_visible(False)
        if self.copilot_win:
            self.copilot_win.set_mode('card')
            self.copilot_win.set_mode('card')
            self.copilot_win.expand_from_notch()

    def on_expand_side_panel(self):
        self.set_visible(False)
        if self.copilot_win:
            self.copilot_win.set_mode('side_panel')
            self.copilot_win.expand_from_notch()

    def on_trigger_snip(self):
        self.set_visible(False)
        if self.copilot_win:
            GLib.timeout_add(100, lambda: self.copilot_win.capture_screen_and_reveal(interactive=True, append_mode=True))

    def on_detach_pet(self):
        self.set_visible(False)
        if self.copilot_win:
            self.copilot_win.detach_to_desktop()

    def on_realize(self, _):
        surface = self.get_surface()
        if surface:
            if hasattr(surface, 'move_to_desktop'):
                surface.move_to_desktop(0xFFFFFFFF)
            if hasattr(surface, 'set_skip_taskbar_hint'):
                surface.set_skip_taskbar_hint(True)
            if hasattr(surface, 'set_skip_pager_hint'):
                surface.set_skip_pager_hint(True)
        GLib.timeout_add(50, self.position_at_top)

    def position_at_top(self):
        surface = self.get_surface()
        if surface and hasattr(surface, 'get_xid'):
            try:
                xid = surface.get_xid()
                screen_w = 1920
                display = Gdk.Display.get_default()
                if display and display.get_monitors():
                    screen_w = display.get_monitors().get_item(0).get_geometry().width
                pos_x = (screen_w - 680) // 2
                X11Helper.get().move_window(xid, pos_x, 34)
                X11Helper.get().make_always_on_top_and_sticky(xid, is_dock=True)
                if hasattr(surface, 'move_to_desktop'):
                    surface.move_to_desktop(0xFFFFFFFF)
                if hasattr(surface, 'set_skip_taskbar_hint'):
                    surface.set_skip_taskbar_hint(True)
                if hasattr(surface, 'set_skip_pager_hint'):
                    surface.set_skip_pager_hint(True)
            except Exception:
                pass
        self.update_input_region(mode="compact")
        if not getattr(self, 'watchdog_id', None):
            self.watchdog_id = GLib.timeout_add_seconds(2, self._watchdog_keep_on_top)
        return GLib.SOURCE_REMOVE

    def _watchdog_keep_on_top(self):
        # If Copilot card, Side Panel, or Desktop Pet is active, keep Island HIDDEN!
        is_copilot_open = bool(self.copilot_win and self.copilot_win.get_visible())
        is_pet_open = bool(self.copilot_win and getattr(self.copilot_win, 'pet_win', None) and self.copilot_win.pet_win.get_visible())
        if is_copilot_open or is_pet_open:
            if self.get_visible():
                self.set_visible(False)
            return GLib.SOURCE_CONTINUE

        # When Copilot is closed and pet is docked, ensure Island stays visible and elevated
        if not self.get_visible():
            self.set_visible(True)
        surface = self.get_surface()
        if surface and hasattr(surface, 'get_xid'):
            try:
                xid = surface.get_xid()
                X11Helper.get().raise_window(xid)
            except Exception:
                pass
        return GLib.SOURCE_CONTINUE

    def update_status(self, text, dot_active=True):
        self.status_label.set_text(text)
        if dot_active:
            self.pulse_dot.set_visible(True)
        else:
            self.pulse_dot.set_visible(False)

    def on_drag_enter(self, target, x, y):
        self.update_input_region(mode="convert")
        self.pill.add_css_class('dynamic-island-feeding')
        self.actions_revealer.set_reveal_child(False)
        self.media_controls_revealer.set_reveal_child(False)
        self.media_box.set_visible(False)
        self.alert_box.set_visible(False)
        self.mascot_box.set_visible(True)
        self.status_label.set_text("😋 Feed / Convert!")
        return Gdk.DragAction.COPY

    def on_drag_leave(self, target):
        self.pill.remove_css_class('dynamic-island-feeding')
        if self.current_mode != "convert":
            if self.last_media_info and self.last_media_info.get('active'):
                self.current_mode = "media"
                self.mascot_box.set_visible(False)
                self.media_box.set_visible(True)
                self.update_input_region(mode="media_compact")
            else:
                self.current_mode = "compact"
                self.status_label.set_text("✨ Lexi")
                self.update_input_region(mode="compact")

    def show_converter_bar(self, paths):
        if not paths:
            return
        self.active_file_paths = paths
        p = paths[0]
        fname = os.path.basename(p)
        try:
            sz_bytes = os.path.getsize(p)
            sz_str = converter.format_size(sz_bytes)
        except Exception:
            sz_str = ""

        if self.dismiss_timer_id:
            GLib.source_remove(self.dismiss_timer_id)
            self.dismiss_timer_id = None

        while child := self.convert_box.get_first_child():
            self.convert_box.remove(child)

        # 1. File Chip
        short_name = (fname[:14] + '…') if len(fname) > 15 else fname
        chip_label = f"📄 {short_name} ({sz_str})" if sz_str else f"📄 {short_name}"
        chip = Gtk.Label(label=chip_label)
        chip.add_css_class('island-file-chip')
        chip.set_tooltip_text(fname)
        self.convert_box.append(chip)

        # 2. Preset Buttons & Peer-to-Peer / Beam Sharing
        ext = os.path.splitext(p)[1].lower()
        is_archive = ext in ('.zip', '.tar', '.gz', '.tgz', '.bz2', '.xz', '.7z', '.iso', '.dmg', '.bin', '.rar', '.pkg')

        peers = self.drop_server.get_all_peers() if getattr(self, 'drop_server', None) else []
        if peers:
            p0 = peers[0]
            peer_label = f"⚡ Send to {p0['device']}"
            p_btn = Gtk.Button(label=peer_label)
            p_btn.set_focusable(False)
            p_btn.add_css_class('island-beam-btn')
            p_btn.set_tooltip_text(f"Send directly to {p0['device']} ({p0['ip']})")
            p_btn.connect('clicked', lambda _, pip=p0['ip'], fp=p: self.on_send_direct_to_peer(pip, fp))
            self.convert_box.append(p_btn)
        else:
            beam_label = "⚡ Beam to Laptop" if is_archive else "⚡ Beam"
            beam_btn = Gtk.Button(label=beam_label)
            beam_btn.set_focusable(False)
            beam_btn.add_css_class('island-beam-btn')
            beam_btn.set_tooltip_text("Beam file at wire speed to nearby laptops or phones")
            beam_btn.connect('clicked', lambda *_: self.on_beam_file_from_island(p))
            self.convert_box.append(beam_btn)

        # 📱 Phone QR button for Android camera scan
        qr_btn = Gtk.Button(label="📱 QR")
        qr_btn.set_focusable(False)
        qr_btn.add_css_class('island-convert-btn')
        qr_btn.set_tooltip_text("Show QR code for Android phone camera scanning")
        qr_btn.connect('clicked', lambda *_: self.on_show_qr_for_file(p))
        self.convert_box.append(qr_btn)

        presets = converter.get_presets_for_file(p)
        limit_presets = 2 if is_archive else 3
        for preset in presets[:limit_presets]:
            btn = Gtk.Button(label=preset['label'])
            btn.set_focusable(False)
            btn.add_css_class('island-convert-btn')
            btn.set_tooltip_text(preset.get('desc', f"Convert to {preset['label']}"))
            btn.connect('clicked', lambda _, pr=preset, fp=p: self.on_execute_convert(fp, pr))
            self.convert_box.append(btn)

        # 3. Donut radial button
        donut_btn = Gtk.Button(label="🍩 Donut")
        donut_btn.set_focusable(False)
        donut_btn.add_css_class('island-convert-btn')
        donut_btn.add_css_class('island-convert-special')
        donut_btn.set_tooltip_text("Open circular radial donut converter menu at cursor")
        donut_btn.connect('clicked', lambda *_: self.on_open_donut_from_island(p))
        self.convert_box.append(donut_btn)

        # 4. Ask AI button
        ai_btn = Gtk.Button(label="💬 Ask AI")
        ai_btn.set_focusable(False)
        ai_btn.add_css_class('island-convert-btn')
        ai_btn.set_tooltip_text("Feed file to Lexi Copilot chat")
        ai_btn.connect('clicked', lambda *_: self.on_feed_to_copilot_from_island(paths))
        self.convert_box.append(ai_btn)

        # 5. Dismiss [✕]
        close_btn = Gtk.Button(label="✕")
        close_btn.set_focusable(False)
        close_btn.add_css_class('island-action-btn')
        close_btn.add_css_class('island-btn-close')
        close_btn.set_tooltip_text("Dismiss converter")
        close_btn.connect('clicked', lambda *_: self.hide_converter_bar())
        self.convert_box.append(close_btn)

        self.current_mode = "convert"
        self.actions_revealer.set_reveal_child(False)
        self.media_controls_revealer.set_reveal_child(False)
        self.mascot_box.set_visible(False)
        self.media_box.set_visible(False)
        self.alert_box.set_visible(False)
        self.convert_revealer.set_reveal_child(True)
        self.pill.add_css_class('dynamic-island-expanded')
        self.pill.add_css_class('dynamic-island-convert')
        self.update_input_region(mode="convert")
        self.mini_mascot.set_mood("happy")

        self.dismiss_timer_id = GLib.timeout_add_seconds(18, self.hide_converter_bar)

    def on_execute_convert(self, file_path, preset):
        if self.dismiss_timer_id:
            GLib.source_remove(self.dismiss_timer_id)
            self.dismiss_timer_id = None

        while child := self.convert_box.get_first_child():
            self.convert_box.remove(child)

        lbl = Gtk.Label(label=f"⏳ Converting to {preset['label']}...")
        lbl.add_css_class('dynamic-island-label')
        self.convert_box.append(lbl)
        self.mini_mascot.set_mood("working")

        def on_done(res):
            GLib.idle_add(self._on_island_convert_finished, res)

        converter.async_convert(file_path, preset['ext'], preset.get('option'), on_complete=on_done)

    def _on_island_convert_finished(self, res):
        while child := self.convert_box.get_first_child():
            self.convert_box.remove(child)

        if res.get("success"):
            self.mini_mascot.set_mood("star_eyes")
            saved_str = res.get('summary', 'Done!')
            lbl = Gtk.Label(label=f"✨ {saved_str}")
            lbl.add_css_class('dynamic-island-label')
            self.convert_box.append(lbl)

            out_p = res.get("output_path")
            if out_p and os.path.exists(out_p):
                open_btn = Gtk.Button(label="📂 Open")
                open_btn.set_focusable(False)
                open_btn.add_css_class('island-convert-btn')
                open_btn.add_css_class('island-convert-special')
                open_btn.set_tooltip_text(f"Show {os.path.basename(out_p)} in file manager")
                folder = out_p if os.path.isdir(out_p) else os.path.dirname(out_p)
                open_btn.connect('clicked', lambda *_: subprocess.Popen(['xdg-open', folder]))
                self.convert_box.append(open_btn)

            close_btn = Gtk.Button(label="✕")
            close_btn.set_focusable(False)
            close_btn.add_css_class('island-action-btn')
            close_btn.connect('clicked', lambda *_: self.hide_converter_bar())
            self.convert_box.append(close_btn)

            self.dismiss_timer_id = GLib.timeout_add_seconds(6, self.hide_converter_bar)
        else:
            self.mini_mascot.set_mood("dizzy")
            err_msg = res.get('error', 'Failed')[:28]
            lbl = Gtk.Label(label=f"⚠️ {err_msg}")
            lbl.add_css_class('dynamic-island-label')
            self.convert_box.append(lbl)

            close_btn = Gtk.Button(label="✕")
            close_btn.set_focusable(False)
            close_btn.add_css_class('island-action-btn')
            close_btn.connect('clicked', lambda *_: self.hide_converter_bar())
            self.convert_box.append(close_btn)

            self.dismiss_timer_id = GLib.timeout_add_seconds(4, self.hide_converter_bar)
        return GLib.SOURCE_REMOVE

    def hide_converter_bar(self):
        if self.dismiss_timer_id:
            GLib.source_remove(self.dismiss_timer_id)
            self.dismiss_timer_id = None
        self.convert_revealer.set_reveal_child(False)
        self.pill.remove_css_class('dynamic-island-convert')
        self.pill.remove_css_class('dynamic-island-expanded')
        self.mini_mascot.set_mood("happy")
        if self.last_media_info and self.last_media_info.get('active'):
            self.current_mode = "media"
            self.media_box.set_visible(True)
            self.pill.add_css_class('dynamic-island-media')
            self.update_input_region(mode="media_compact")
        else:
            self.current_mode = "compact"
            self.status_label.set_text("✨ Lexi")
            self.mascot_box.set_visible(True)
            self.update_input_region(mode="compact")
        return GLib.SOURCE_REMOVE

    def on_show_beam_info(self):
        if not getattr(self, 'drop_server', None):
            self.trigger_island_alert("⚡", "Lexi Beam Starting...", "cyan", 3.0)
            return
        peers = self.drop_server.get_all_peers()
        if peers:
            p_name = peers[0].get("device", "Laptop")
            self.trigger_island_alert("⚡", f"Online: {p_name} ({len(peers)} peer)", "green", 4.0)
        else:
            ip = self.drop_server.local_ip
            port = self.drop_server.port
            self.trigger_island_alert("⚡", f"Beam LAN: http://{ip}:{port}", "cyan", 4.5)

    def on_show_qr_for_file(self, file_path=None):
        target_url = None
        if file_path and getattr(self, 'drop_server', None):
            info = self.drop_server.share_file(file_path)
            target_url = info.get("url")
        self.hide_converter_bar()
        qr_win = LexiQrPopoverWindow(target_url=target_url)
        qr_win.present()

    def on_send_direct_to_peer(self, peer_ip, file_path):
        if self.dismiss_timer_id:
            GLib.source_remove(self.dismiss_timer_id)
            self.dismiss_timer_id = None

        while child := self.convert_box.get_first_child():
            self.convert_box.remove(child)

        lbl = Gtk.Label(label=f"⚡ Beaming to peer...")
        lbl.add_css_class('dynamic-island-label')
        self.convert_box.append(lbl)
        self.mini_mascot.set_mood("working")

        def _do_send():
            if not getattr(self, 'drop_server', None):
                return None
            return self.drop_server.send_p2p_to_peer(peer_ip, file_path)

        def _on_done(res):
            while child := self.convert_box.get_first_child():
                self.convert_box.remove(child)

            if not res or res.get("error"):
                err = res.get("error", "Failed") if res else "Error"
                lbl = Gtk.Label(label=f"⚠️ {err[:30]}")
                lbl.add_css_class('dynamic-island-label')
                self.convert_box.append(lbl)
                self.dismiss_timer_id = GLib.timeout_add_seconds(5, self.hide_converter_bar)
                return

            fname = os.path.basename(file_path)
            short_name = (fname[:12] + '…') if len(fname) > 13 else fname
            lbl = Gtk.Label(label=f"⚡ Sent proposal for {short_name} to peer!")
            lbl.add_css_class('dynamic-island-label')
            self.convert_box.append(lbl)

            close_btn = Gtk.Button(label="✕")
            close_btn.set_focusable(False)
            close_btn.add_css_class('island-action-btn')
            close_btn.connect('clicked', lambda *_: self.hide_converter_bar())
            self.convert_box.append(close_btn)

            self.mini_mascot.set_mood("star_eyes")
            self.dismiss_timer_id = GLib.timeout_add_seconds(8, self.hide_converter_bar)

        threading.Thread(target=lambda: GLib.idle_add(_on_done, _do_send()), daemon=True).start()

    def on_p2p_incoming_from_peer(self, info):
        token = info.get("token")
        filename = info.get("filename", "file.bin")
        from_dev = info.get("from_device", "Nearby Linux")
        sz_bytes = info.get("size_bytes", 0)
        sz_str = converter.format_size(sz_bytes) if hasattr(converter, 'format_size') else f"{sz_bytes}B"
        short_name = (filename[:12] + '…') if len(filename) > 13 else filename

        if self.dismiss_timer_id:
            GLib.source_remove(self.dismiss_timer_id)
            self.dismiss_timer_id = None

        while child := self.convert_box.get_first_child():
            self.convert_box.remove(child)

        lbl = Gtk.Label(label=f"⚡ {from_dev} wants to send {short_name} ({sz_str})")
        lbl.add_css_class('dynamic-island-label')
        self.convert_box.append(lbl)

        # Accept button
        accept_btn = Gtk.Button(label="✓ Accept")
        accept_btn.set_focusable(False)
        accept_btn.add_css_class('island-convert-btn')
        accept_btn.add_css_class('island-convert-special')
        def _accept(*_):
            accept_btn.set_visible(False)
            lbl.set_label(f"📥 Receiving {short_name}...")
            threading.Thread(target=lambda: self._respond_p2p_transfer(token, "accept"), daemon=True).start()
        accept_btn.connect('clicked', _accept)
        self.convert_box.append(accept_btn)

        # Decline button
        decline_btn = Gtk.Button(label="✕ Decline")
        decline_btn.set_focusable(False)
        decline_btn.add_css_class('island-action-btn')
        def _decline(*_):
            threading.Thread(target=lambda: self._respond_p2p_transfer(token, "decline"), daemon=True).start()
            self.hide_converter_bar()
        decline_btn.connect('clicked', _decline)
        self.convert_box.append(decline_btn)

        self.current_mode = "convert"
        self.actions_revealer.set_reveal_child(False)
        self.media_controls_revealer.set_reveal_child(False)
        self.mascot_box.set_visible(False)
        self.media_box.set_visible(False)
        self.alert_box.set_visible(False)
        self.convert_revealer.set_reveal_child(True)
        self.pill.add_css_class('dynamic-island-expanded')
        self.pill.add_css_class('dynamic-island-convert')
        self.update_input_region(mode="convert")
        self.mini_mascot.set_mood("working")

        self.dismiss_timer_id = GLib.timeout_add_seconds(20, self.hide_converter_bar)
        return GLib.SOURCE_REMOVE

    def _respond_p2p_transfer(self, token, action):
        if not getattr(self, 'drop_server', None):
            return
        payload = json.dumps({"token": token, "action": action}).encode('utf-8')
        try:
            req = urllib.request.Request(
                f"http://127.0.0.1:{self.drop_server.port}/api/peer/respond",
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST"
            )
            urllib.request.urlopen(req, timeout=2.0)
        except Exception as e:
            print(f"[LexiIsland] Failed to respond to P2P transfer: {e}")

    def on_transfer_progress_update(self, token, direction, speed_mbps, pct):
        if self.current_mode != "convert":
            return GLib.SOURCE_REMOVE
        icon = "📥" if direction == "download" else "⚡"
        first = self.convert_box.get_first_child()
        if isinstance(first, Gtk.Label):
            first.set_label(f"{icon} Transferring... {pct}% @ {speed_mbps:.1f} MB/s")
        return GLib.SOURCE_REMOVE

    def on_beam_file_from_island(self, file_path):
        if self.dismiss_timer_id:
            GLib.source_remove(self.dismiss_timer_id)
            self.dismiss_timer_id = None

        while child := self.convert_box.get_first_child():
            self.convert_box.remove(child)

        lbl = Gtk.Label(label="⚡ Beaming file...")
        lbl.add_css_class('dynamic-island-label')
        self.convert_box.append(lbl)
        self.mini_mascot.set_mood("working")

        def _do_share():
            if not lexi_drop:
                return None
            server = lexi_drop.get_lexi_drop_instance()
            return server.share_file(file_path)

        def _share_finished(share_info):
            while child := self.convert_box.get_first_child():
                self.convert_box.remove(child)

            if not share_info:
                lbl = Gtk.Label(label="⚠️ Beam failed")
                lbl.add_css_class('dynamic-island-label')
                self.convert_box.append(lbl)
                self.dismiss_timer_id = GLib.timeout_add_seconds(4, self.hide_converter_bar)
                return

            fname = share_info.get("name", "file")
            short_fname = (fname[:12] + '…') if len(fname) > 13 else fname
            server = getattr(self, 'drop_server', None)
            peers = server.get_all_peers() if server else []

            if peers:
                peer_name = peers[0].get("device", "Laptop")
                lbl = Gtk.Label(label=f"⚡ Beamed {short_fname} to {peer_name}!")
            else:
                ip = server.local_ip if server else "127.0.0.1"
                lbl = Gtk.Label(label=f"⚡ Beamed {short_fname} · Open {ip}:{server.port if server else 7890}")
            lbl.add_css_class('dynamic-island-label')
            self.convert_box.append(lbl)

            # Copy link button
            copy_btn = Gtk.Button(label="📋 Copy Link")
            copy_btn.set_focusable(False)
            copy_btn.add_css_class('island-convert-btn')
            copy_btn.add_css_class('island-convert-special')
            url = share_info.get("url") or share_info.get("server_url")
            def _copy():
                clipboard = Gdk.Display.get_default().get_clipboard()
                clipboard.set(url)
                copy_btn.set_label("✓ Copied!")
            copy_btn.connect('clicked', lambda *_: _copy())
            self.convert_box.append(copy_btn)

            # 📱 Phone QR button
            qr_btn = Gtk.Button(label="📱 QR")
            qr_btn.set_focusable(False)
            qr_btn.add_css_class('island-convert-btn')
            qr_btn.set_tooltip_text("Show QR code for Android phone camera scanning")
            qr_btn.connect('clicked', lambda *_: self.on_show_qr_for_file(file_path))
            self.convert_box.append(qr_btn)

            # Dismiss button
            close_btn = Gtk.Button(label="✕")
            close_btn.set_focusable(False)
            close_btn.add_css_class('island-action-btn')
            close_btn.connect('clicked', lambda *_: self.hide_converter_bar())
            self.convert_box.append(close_btn)

            self.mini_mascot.set_mood("star_eyes")
            self.dismiss_timer_id = GLib.timeout_add_seconds(12, self.hide_converter_bar)

        def _worker():
            res = _do_share()
            GLib.idle_add(_share_finished, res)

        threading.Thread(target=_worker, daemon=True).start()

    def show_received_file_bar(self, filepath, size_bytes, speed_mbps):
        if self.dismiss_timer_id:
            GLib.source_remove(self.dismiss_timer_id)
            self.dismiss_timer_id = None

        while child := self.convert_box.get_first_child():
            self.convert_box.remove(child)

        fname = os.path.basename(filepath)
        short_fname = (fname[:12] + '…') if len(fname) > 13 else fname
        sz_str = converter.format_size(size_bytes) if hasattr(converter, 'format_size') else f"{size_bytes}B"

        lbl = Gtk.Label(label=f"📥 Received {short_fname} ({sz_str}) @ {speed_mbps:.1f} MB/s")
        lbl.add_css_class('dynamic-island-label')
        self.convert_box.append(lbl)

        open_btn = Gtk.Button(label="📂 Open")
        open_btn.set_focusable(False)
        open_btn.add_css_class('island-convert-btn')
        open_btn.add_css_class('island-convert-special')
        open_btn.set_tooltip_text("Open Downloads directory")
        open_btn.connect('clicked', lambda *_: subprocess.Popen(['xdg-open', os.path.dirname(filepath)]))
        self.convert_box.append(open_btn)

        close_btn = Gtk.Button(label="✕")
        close_btn.set_focusable(False)
        close_btn.add_css_class('island-action-btn')
        close_btn.connect('clicked', lambda *_: self.hide_converter_bar())
        self.convert_box.append(close_btn)

        self.current_mode = "convert"
        self.actions_revealer.set_reveal_child(False)
        self.media_controls_revealer.set_reveal_child(False)
        self.mascot_box.set_visible(False)
        self.media_box.set_visible(False)
        self.alert_box.set_visible(False)
        self.convert_revealer.set_reveal_child(True)
        self.pill.add_css_class('dynamic-island-expanded')
        self.pill.add_css_class('dynamic-island-convert')
        self.update_input_region(mode="convert")
        self.mini_mascot.set_mood("star_eyes")

        self.dismiss_timer_id = GLib.timeout_add_seconds(10, self.hide_converter_bar)
        return GLib.SOURCE_REMOVE

    def on_open_donut_from_island(self, file_path):
        self.hide_converter_bar()
        donut = LexiRadialDonutWindow(file_path=file_path)
        donut.present()

    def on_feed_to_copilot_from_island(self, paths):
        self.hide_converter_bar()
        if paths and self.copilot_win:
            for p in paths:
                self.copilot_win.feed_file(p)
            self.on_expand_copilot()

    def on_drag_drop(self, target, value, x, y):
        self.pill.remove_css_class('dynamic-island-feeding')
        self.status_label.set_text("✨ Lexi")
        paths = []
        if value is not None:
            if hasattr(value, 'get_files'):
                for gf in value.get_files():
                    p = gf.get_path()
                    if p and os.path.exists(p):
                        paths.append(p)
            elif isinstance(value, Gio.File):
                p = value.get_path()
                if p and os.path.exists(p):
                    paths.append(p)
            elif isinstance(value, str):
                for line in value.strip().splitlines():
                    p = line.strip().replace('file://', '')
                    if os.path.exists(p):
                        paths.append(p)
        if paths:
            presets = converter.get_presets_for_file(paths[0])
            ext = os.path.splitext(paths[0])[1].lower()
            is_archive = ext in ('.zip', '.tar', '.gz', '.tgz', '.bz2', '.xz', '.7z', '.iso', '.dmg', '.bin', '.rar', '.pkg')
            if presets or is_archive:
                self.show_converter_bar(paths)
                return True
            elif self.copilot_win:
                for p in paths:
                    self.copilot_win.feed_file(p)
                self.on_expand_copilot()
                return True
        return False

# Backwards compatibility alias
LexiNotchWindow = LexiDynamicIslandWindow


class LexiPetWindow(Gtk.Window):
    """
    Detachable Desktop Pet Mode:
    A transparent, frameless floating companion that wanders your desktop,
    follows your cursor, jumps high, exercises, stretches, plays with stardust,
    eats dropped files, and docks on double-click. Zero-latency X11 dragging.
    """
    def __init__(self, copilot_win):
        super().__init__(title="Lexi Desktop Pet")
        self.copilot_win = copilot_win
        self.set_decorated(False)
        self.set_resizable(False)
        self.set_default_size(160, 160)
        self.add_css_class('lexi-pet-win')

        # Dynamically detect full screen geometry for laptop roaming
        self.screen_w = 1920
        self.screen_h = 1080
        display = Gdk.Display.get_default()
        if display and display.get_monitors():
            geom = display.get_monitors().get_item(0).get_geometry()
            self.screen_w = geom.width
            self.screen_h = geom.height

        # Position at bottom of screen above dock/taskbar
        self.floor_y = float(self.screen_h - 190)
        self.pet_x = float(self.screen_w - 240)
        self.pet_y = self.floor_y
        self.web_anchor_x = float(self.pet_x)
        self.web_anchor_y = 35.0
        self.is_dragging = False
        self.drag_start_pet_x = self.pet_x
        self.drag_start_pet_y = self.pet_y

        # Pure Mascot Widget - Clean, organic desktop companion (no buttons or badges)
        self.mascot = LexiMascotWidget(width=160, height=160)
        self.mascot.is_pet_mode = True
        self.set_child(self.mascot)

        # Drag gesture to pick up and place Lexi anywhere on screen
        drag = Gtk.GestureDrag()
        drag.connect('drag-begin', self.on_drag_begin)
        drag.connect('drag-update', self.on_drag_update)
        drag.connect('drag-end', self.on_drag_end)
        self.add_controller(drag)

        # Click to pet / Double-click to dock back
        click = Gtk.GestureClick()
        click.connect('pressed', self.on_click)
        self.add_controller(click)

        # Right-click context menu for stunts and character customization
        right_click = Gtk.GestureClick()
        right_click.set_button(3)
        right_click.connect('pressed', self.on_right_click)
        self.add_controller(right_click)

        # Drag-and-Drop file feeding directly on desktop pet!
        drop_target = Gtk.DropTarget(actions=Gdk.DragAction.COPY)
        drop_target.set_gtypes([Gdk.FileList, Gio.File, str])
        drop_target.set_preload(True)
        drop_target.connect('enter', self.on_drag_enter)
        drop_target.connect('drop', self.on_drag_drop)
        self.add_controller(drop_target)

        # Screen-wide autonomous roaming loop (active only when detached)
        self.roam_timer_id = None

        self.connect('realize', self.on_realize)

    def on_realize(self, _):
        surface = self.get_surface()
        if surface:
            if hasattr(surface, 'move_to_desktop'):
                surface.move_to_desktop(0xFFFFFFFF)
            if hasattr(surface, 'set_skip_taskbar_hint'):
                surface.set_skip_taskbar_hint(True)
            if hasattr(surface, 'set_skip_pager_hint'):
                surface.set_skip_pager_hint(True)
        GLib.timeout_add(50, self.position_on_desktop)

    def position_on_desktop(self):
        if not self.roam_timer_id:
            self.roam_timer_id = GLib.timeout_add(33, self.roam_tick)
        surface = self.get_surface()
        if surface and hasattr(surface, 'get_xid'):
            try:
                xid = surface.get_xid()
                X11Helper.get().move_window(xid, int(self.pet_x), int(self.pet_y))
                X11Helper.get().make_always_on_top_and_sticky(xid)
                if hasattr(surface, 'move_to_desktop'):
                    surface.move_to_desktop(0xFFFFFFFF)
                if hasattr(surface, 'set_skip_taskbar_hint'):
                    surface.set_skip_taskbar_hint(True)
                if hasattr(surface, 'set_skip_pager_hint'):
                    surface.set_skip_pager_hint(True)
            except Exception:
                pass
        return GLib.SOURCE_REMOVE

    def roam_tick(self):
        if not self.get_mapped() or not self.get_visible() or not self.mascot:
            return GLib.SOURCE_CONTINUE
        if self.is_dragging:
            return GLib.SOURCE_CONTINUE

        surface = self.get_surface()
        xid = surface.get_xid() if (surface and hasattr(surface, 'get_xid')) else None

        # Track mouse gaze across the laptop
        try:
            pt = X11Helper.get().get_pointer()
            if pt:
                mx, my = pt
                dx = (mx - (self.pet_x + 80.0)) / 600.0
                dy = (my - (self.pet_y + 80.0)) / 450.0
                self.mascot.set_gaze(dx, dy)
        except Exception:
            pass

        old_x, old_y = self.pet_x, self.pet_y

        # Autonomous Roaming & Stunts Physics across laptop screen
        if self.mascot.mood == "normal":
            act = self.mascot.activity
            if act == "spiderweb":
                dy = self.web_anchor_y - self.pet_y
                dx = (self.web_anchor_x - 80.0) - self.pet_x
                self.pet_x += dx * 0.16
                self.pet_y += dy * 0.18
                self.mascot.web_local_anchor_x = float(self.web_anchor_x - self.pet_x)
                self.mascot.web_local_anchor_y = float(self.web_anchor_y - self.pet_y)
                if abs(dy) < 16:
                    self.pet_y = float(self.web_anchor_y)
                    self.mascot.trigger_perch()

            elif act == "perch":
                self.pet_y = float(self.web_anchor_y)
                if self.mascot.activity_timer > 140:
                    r = random.random()
                    if r < 0.45:
                        self.trigger_backflip_stunt()
                    elif r < 0.75:
                        self.trigger_flight_stunt()
                    else:
                        self.trigger_hang_stunt()

            elif act == "hang_web":
                self.pet_y = float(self.web_anchor_y + 80.0 + 12.0 * math.sin(self.mascot.activity_timer * 0.12))
                self.mascot.web_local_anchor_x = float(self.web_anchor_x - self.pet_x)
                self.mascot.web_local_anchor_y = float(self.web_anchor_y - self.pet_y)
                if self.mascot.activity_timer > 110:
                    self.trigger_backflip_stunt()

            elif act == "fly":
                self.pet_x += self.mascot.facing * 4.2
                self.pet_y = 110.0 + 65.0 * math.sin(self.mascot.activity_timer * 0.07)
                if self.pet_x > self.screen_w - 170:
                    self.pet_x = float(self.screen_w - 170)
                    self.mascot.facing = -1.0
                elif self.pet_x < 15:
                    self.pet_x = 15.0
                    self.mascot.facing = 1.0
                if self.mascot.activity_timer > 160:
                    self.mascot.activity = "walk"
                    self.mascot.activity_timer = 0
                    self.mascot.is_walking = True
                    self.mascot.flip_angle = 0.0

            elif act == "backflip":
                self.pet_x += self.mascot.facing * 2.2
                self.pet_y += (self.floor_y - self.pet_y) * 0.16
                if self.mascot.activity_timer >= 28 or abs(self.pet_y - self.floor_y) < 12:
                    self.pet_y = self.floor_y
                    self.mascot.activity = "walk"
                    self.mascot.activity_timer = 0
                    self.mascot.is_walking = True
                    self.mascot.flip_angle = 0.0

            elif act == "walk":
                if self.mascot.is_walking:
                    self.pet_x += self.mascot.facing * 1.6
                    if self.pet_x > self.screen_w - 170:
                        self.pet_x = float(self.screen_w - 170)
                        self.mascot.facing = -1.0
                        self.mascot.is_walking = False
                        self.mascot.walk_pause_timer = 40
                    elif self.pet_x < 15:
                        self.pet_x = 15.0
                        self.mascot.facing = 1.0
                        self.mascot.is_walking = False
                        self.mascot.walk_pause_timer = 40
                self.pet_y += (self.floor_y - self.pet_y) * 0.2

            elif act == "jump":
                self.pet_x += self.mascot.facing * 2.6
                self.pet_y = self.floor_y + self.mascot.jump_y * 1.8
                if self.pet_y < 50:
                    self.pet_y = 50.0
                if self.pet_x > self.screen_w - 170:
                    self.pet_x = float(self.screen_w - 170)
                    self.mascot.facing = -1.0
                elif self.pet_x < 15:
                    self.pet_x = 15.0
                    self.mascot.facing = 1.0

            elif act == "jacks":
                self.pet_y = self.floor_y + self.mascot.jump_y * 0.8

            elif act == "stardust":
                target_x = self.pet_x + self.mascot.star_orb_x * 0.35
                self.pet_x += (target_x - self.pet_x) * 0.08
                self.pet_y = self.floor_y + self.mascot.jump_y * 1.2

            else:
                self.pet_y += (self.floor_y - self.pet_y) * 0.2

        # Instant C X11 window movement (0.013ms)
        if xid and (int(self.pet_x) != int(old_x) or int(self.pet_y) != int(old_y)):
            X11Helper.get().move_window(xid, int(self.pet_x), int(self.pet_y))

        return GLib.SOURCE_CONTINUE

    def on_drag_begin(self, gesture, x, y):
        self.is_dragging = True
        self.drag_start_pet_x = self.pet_x
        self.drag_start_pet_y = self.pet_y
        self.mascot.squish_x = 0.88
        self.mascot.squish_y = 1.18  # Dangle squish when picked up
        self.mascot.set_mood("happy")
        self.mascot.spawn_particle(0, -10, 'sparkle', 4)

    def on_drag_update(self, gesture, offset_x, offset_y):
        surface = self.get_surface()
        if surface and hasattr(surface, 'get_xid'):
            self.pet_x = int(self.drag_start_pet_x + offset_x)
            self.pet_y = int(self.drag_start_pet_y + offset_y)
            X11Helper.get().move_window(surface.get_xid(), int(self.pet_x), int(self.pet_y))

    def on_drag_end(self, gesture, offset_x, offset_y):
        self.is_dragging = False
        # Update floor level to wherever the user dropped her
        self.floor_y = float(min(self.screen_h - 180, max(50, self.pet_y)))
        self.pet_y = self.floor_y
        self.mascot.squish_x = 1.25
        self.mascot.squish_y = 0.75  # Satisfying landing bounce
        self.mascot.spawn_particle(0, 20, 'star', 6)
        self.mascot.activity = "walk"
        self.mascot.is_walking = True

    def on_click(self, gesture, n_press, x, y):
        if n_press == 1:
            self.mascot.pet()
            self.mascot.cycle_next_activity()
        elif n_press == 2:
            self.dock_back()

    def dock_back(self):
        if self.roam_timer_id:
            GLib.source_remove(self.roam_timer_id)
            self.roam_timer_id = None
        self.set_visible(False)
        if self.copilot_win:
            self.copilot_win.collapse_to_notch()

    def on_drag_enter(self, target, x, y):
        self.mascot.set_mood("hungry")
        return Gdk.DragAction.COPY

    def on_drag_drop(self, target, value, x, y):
        paths = []
        if value is not None:
            if hasattr(value, 'get_files'):
                for gf in value.get_files():
                    p = gf.get_path()
                    if p and os.path.exists(p):
                        paths.append(p)
            elif isinstance(value, Gio.File):
                p = value.get_path()
                if p and os.path.exists(p):
                    paths.append(p)
            elif isinstance(value, str):
                for line in value.strip().splitlines():
                    p = line.strip().replace('file://', '')
                    if os.path.exists(p):
                        paths.append(p)
        if paths:
            ext = os.path.splitext(paths[0])[1].lower()
            if ext in ('.png', '.jpg', '.jpeg', '.webp'):
                AvatarManager.get().set_character("custom", paths[0])
                self.mascot.set_mood("happy")
                self.mascot.spawn_particle(0, 0, 'star', 12)
                self.mascot.spawn_particle(0, 0, 'sparkle', 8)
                self.mascot.trigger_jump()
                return True
            presets = converter.get_presets_for_file(paths[0])
            if presets:
                self.mascot.set_mood("star_eyes")
                donut_x = int(self.pet_x + 80)
                donut_y = int(self.pet_y + 80)
                donut = LexiRadialDonutWindow(file_path=paths[0], spawn_pos=(donut_x, donut_y))
                donut.present()
                return True
            elif self.copilot_win:
                for p in paths:
                    self.copilot_win.feed_file(p)
                self.dock_back()
                return True
        return False

    def on_right_click(self, gesture, n_press, x, y):
        pop = Gtk.Popover()
        pop.set_parent(self.mascot)
        pop.set_pointing_to(Gdk.Rectangle(x=int(x), y=int(y), width=1, height=1))
        pop.add_css_class('lexi-pet-popover')

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        box.set_margin_top(6)
        box.set_margin_bottom(6)
        box.set_margin_start(6)
        box.set_margin_end(6)
        pop.set_child(box)

        title = Gtk.Label(label="🎪 Lexi Acrobatics & Skills", xalign=0)
        title.add_css_class('lexi-pet-menu-title')
        box.append(title)

        def make_item(label, cb):
            btn = Gtk.Button(label=label)
            btn.add_css_class('lexi-pet-menu-item')
            def on_pressed(_):
                pop.popdown()
                cb()
            btn.connect('clicked', on_pressed)
            return btn

        # Stunts
        box.append(make_item("🕸️ Spider-Web Grapple to Ledge", lambda: self.trigger_spiderweb_stunt()))
        box.append(make_item("🧘 Perch on Top Ledge", lambda: self.trigger_perch_stunt()))
        box.append(make_item("🤸 Hang Upside-Down on Silk", lambda: self.trigger_hang_stunt()))
        box.append(make_item("🚀 Superhero Flying Sweep", lambda: self.trigger_flight_stunt()))
        box.append(make_item("🔄 Acrobatic 360° Backflip", lambda: self.trigger_backflip_stunt()))
        box.append(make_item("🐾 Walk & Roam", lambda: self.trigger_walk_stunt()))

        # Separator
        sep = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        sep.add_css_class('lexi-pet-menu-sep')
        box.append(sep)

        # Character Studio & Copilot controls
        char_label = "🐱 Switch to Lexi Cat" if AvatarManager.get().character_type == "custom" else "👤 Switch to My Photo Avatar"
        box.append(make_item(char_label, lambda: self.toggle_character()))
        box.append(make_item("🎨 Character Studio...", lambda: self.open_avatar_studio()))
        box.append(make_item("💬 Open Copilot", lambda: self.copilot_win.expand_to_card() if self.copilot_win else None))
        box.append(make_item("✕ Dock Back to Dynamic Island", lambda: self.dock_back()))

        pop.popup()

    def trigger_spiderweb_stunt(self):
        target_x, target_y = find_top_ledge_target(self.pet_x + self.mascot.facing * 40, 35)
        self.web_anchor_x = target_x
        self.web_anchor_y = target_y
        self.mascot.web_anchor_x = target_x
        self.mascot.web_anchor_y = target_y
        self.mascot.trigger_spiderweb(target_x, target_y)

    def trigger_perch_stunt(self):
        target_x, target_y = find_top_ledge_target(self.pet_x, 35)
        self.web_anchor_x = target_x
        self.web_anchor_y = target_y
        self.pet_y = float(target_y)
        self.mascot.trigger_perch()

    def trigger_hang_stunt(self):
        target_x, target_y = find_top_ledge_target(self.pet_x, 35)
        self.web_anchor_x = target_x
        self.web_anchor_y = target_y
        self.pet_y = float(target_y + 80)
        self.mascot.web_anchor_x = target_x
        self.mascot.web_anchor_y = target_y
        self.mascot.trigger_hang_web()

    def trigger_flight_stunt(self):
        self.mascot.trigger_flight()

    def trigger_backflip_stunt(self):
        self.mascot.trigger_backflip()

    def trigger_walk_stunt(self):
        self.mascot.trigger_walk()

    def toggle_character(self):
        mgr = AvatarManager.get()
        if mgr.character_type == "custom":
            mgr.set_character("cat")
        else:
            if mgr.custom_avatar_path and os.path.exists(mgr.custom_avatar_path):
                mgr.set_character("custom")
            else:
                self.open_avatar_studio()

    def open_avatar_studio(self):
        if self.copilot_win:
            self.copilot_win.open_avatar_dialog()
        else:
            d = LexiAvatarDialog(parent=self)
            d.present()


class LexiQrPopoverWindow(Gtk.Window):
    """
    Sleek Obsidian & Titanium QR popover for Android phone camera scanning.
    Renders high-contrast QR code pointing to http://{local_ip}:7890.
    Auto-detects when the phone connects and auto-closes.
    """
    def __init__(self, target_url=None, title="⚡ Lexi Beam · Android Scan"):
        super().__init__(title="Lexi Beam QR")
        self.set_decorated(False)
        self.set_resizable(False)
        self.set_default_size(300, 360)
        self.add_css_class('lexi-qr-win')

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        root.set_margin_top(16)
        root.set_margin_bottom(16)
        root.set_margin_start(18)
        root.set_margin_end(18)
        root.add_css_class('lexi-qr-box')
        self.set_child(root)

        # Header
        head_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        title_lbl = Gtk.Label(label=title, xalign=0, hexpand=True)
        title_lbl.add_css_class('lexi-qr-title')
        head_box.append(title_lbl)

        close_btn = Gtk.Button(label="✕")
        close_btn.set_focusable(False)
        close_btn.add_css_class('island-action-btn')
        close_btn.connect('clicked', lambda *_: self.close())
        head_box.append(close_btn)
        root.append(head_box)

        sub_lbl = Gtk.Label(label="Point your Android camera to download directly", xalign=0)
        sub_lbl.add_css_class('lexi-qr-sub')
        root.append(sub_lbl)

        # Generate QR code
        qr_path = "/tmp/lexi_beam_qr.png"
        if lexi_drop:
            server = lexi_drop.get_lexi_drop_instance()
            server.generate_qr_code(target_url, save_path=qr_path)
            self.qr_url = target_url or server.get_server_url()
        else:
            self.qr_url = target_url or "http://127.0.0.1:7890"

        # Picture widget
        pic_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        pic_box.set_halign(Gtk.Align.CENTER)
        pic_box.add_css_class('lexi-qr-img-wrap')

        if os.path.exists(qr_path):
            gfile = Gio.File.new_for_path(qr_path)
            pic = Gtk.Picture.new_for_file(gfile)
            pic.set_size_request(200, 200)
            pic_box.append(pic)
        root.append(pic_box)

        # Bottom URL pill
        url_btn = Gtk.Button(label=f"🌐 {self.qr_url}")
        url_btn.set_focusable(False)
        url_btn.add_css_class('lexi-qr-url-pill')
        def _copy_url(*_):
            clipboard = Gdk.Display.get_default().get_clipboard()
            clipboard.set(self.qr_url)
            url_btn.set_label("✓ Copied Link!")
        url_btn.connect('clicked', _copy_url)
        root.append(url_btn)

        # Status text
        self.status_lbl = Gtk.Label(label="📡 Waiting for scan...", xalign=0.5)
        self.status_lbl.add_css_class('lexi-qr-status')
        root.append(self.status_lbl)

        # Listen for phone connection to auto-close
        if lexi_drop:
            server = lexi_drop.get_lexi_drop_instance()
            orig_cb = server.on_peer_joined
            def _on_peer(dev, ip):
                if orig_cb:
                    orig_cb(dev, ip)
                GLib.idle_add(self._on_phone_connected, dev)
            server.on_peer_joined = _on_peer

    def _on_phone_connected(self, dev):
        self.status_lbl.set_label(f"✨ Connected to {dev}!")
        self.status_lbl.add_css_class('lexi-qr-connected')
        GLib.timeout_add_seconds(2, self.close)


class LexiAvatarDialog(Gtk.Window):
    """
    Character Studio Dialog:
    Lets the user choose between the default Celestial Cat (Lexi)
    or personalize with their own photo/avatar as their mini character,
    and trigger stunts on command.
    """
    def __init__(self, parent=None):
        super().__init__(title="Lexi Character Studio")
        self.parent_win = parent
        self.set_modal(True)
        if parent:
            self.set_transient_for(parent)
        self.set_default_size(480, 540)
        self.set_resizable(False)
        self.add_css_class('lexi-avatar-win')

        mgr = AvatarManager.get()
        self.mgr = mgr

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        root.set_margin_top(18)
        root.set_margin_bottom(18)
        root.set_margin_start(20)
        root.set_margin_end(20)
        root.add_css_class('lexi-avatar-root')
        self.set_child(root)

        # Header
        head_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        t = Gtk.Label(label="✨ Character & Avatar Studio", xalign=0)
        t.add_css_class('lexi-studio-title')
        head_box.append(t)
        sub = Gtk.Label(label="Keep Lexi the celestial cat as your default or upload your photo as your mini character.", xalign=0)
        sub.set_wrap(True)
        sub.add_css_class('lexi-studio-subtitle')
        head_box.append(sub)
        root.append(head_box)

        # Card 1: Lexi Celestial Cat (Default)
        self.cat_card = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=14)
        self.cat_card.add_css_class('lexi-avatar-card')
        if mgr.character_type == "cat":
            self.cat_card.add_css_class('lexi-avatar-card-active')

        cat_icon = Gtk.Label(label="🐱")
        cat_icon.add_css_class('lexi-card-emoji')
        self.cat_card.append(cat_icon)

        cat_info = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        cat_info.set_hexpand(True)
        cat_t = Gtk.Label(label="Celestial Cat (Lexi)  •  Default", xalign=0)
        cat_t.add_css_class('lexi-card-heading')
        cat_info.append(cat_t)
        cat_desc = Gtk.Label(label="Handcrafted anime chibi kitten with expressive eyes, perky ears, swaying tail, and mouse tracking.", xalign=0)
        cat_desc.set_wrap(True)
        cat_desc.add_css_class('lexi-card-desc')
        cat_info.append(cat_desc)
        self.cat_card.append(cat_info)

        self.cat_btn = Gtk.Button(label="Active ✓" if mgr.character_type == "cat" else "Use Cat")
        self.cat_btn.add_css_class('lexi-card-select-btn')
        self.cat_btn.connect('clicked', lambda _: self.select_cat())
        self.cat_card.append(self.cat_btn)
        root.append(self.cat_card)

        # Card 2: Custom Avatar / Mini Character
        self.custom_card = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=14)
        self.custom_card.add_css_class('lexi-avatar-card')
        if mgr.character_type == "custom":
            self.custom_card.add_css_class('lexi-avatar-card-active')

        custom_icon = Gtk.Label(label="👤")
        custom_icon.add_css_class('lexi-card-emoji')
        self.custom_card.append(custom_icon)

        custom_info = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        custom_info.set_hexpand(True)
        custom_t = Gtk.Label(label="Personal Mini Character", xalign=0)
        custom_t.add_css_class('lexi-card-heading')
        custom_info.append(custom_t)

        has_avatar = bool(mgr.custom_avatar_path and os.path.exists(mgr.custom_avatar_path))
        avatar_name = os.path.basename(mgr.custom_avatar_path) if has_avatar else "No photo chosen yet"
        self.custom_desc = Gtk.Label(label=f"Photo: {avatar_name}\nProcessed into an animated mini character with aura, chibi paws & feet.", xalign=0)
        self.custom_desc.set_wrap(True)
        self.custom_desc.add_css_class('lexi-card-desc')
        custom_info.append(self.custom_desc)

        # Buttons row inside custom card: Choose Photo & Select
        c_btns = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        choose_btn = Gtk.Button(label="📁 Choose Image...")
        choose_btn.add_css_class('lexi-pill-btn')
        choose_btn.connect('clicked', lambda _: self.open_file_picker())
        c_btns.append(choose_btn)

        self.custom_btn = Gtk.Button(label="Active ✓" if mgr.character_type == "custom" else "Use Photo")
        self.custom_btn.add_css_class('lexi-card-select-btn')
        self.custom_btn.connect('clicked', lambda _: self.select_custom())
        c_btns.append(self.custom_btn)

        custom_info.append(c_btns)
        self.custom_card.append(custom_info)
        root.append(self.custom_card)

        # Section 3: Acrobatic Stunts & Movement Controls
        stunt_sec = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        stunt_title = Gtk.Label(label="🎪 Perform Acrobatic Stunts Live", xalign=0)
        stunt_title.add_css_class('lexi-card-heading')
        stunt_sec.append(stunt_title)

        stunt_grid = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        stunt_grid.set_halign(Gtk.Align.CENTER)

        stunts = [
            ("🕸️ Web Grapple", self.do_stunt_web),
            ("🧘 Perch Ledge", self.do_stunt_perch),
            ("🤸 Silk Hang", self.do_stunt_hang),
            ("🚀 Flight", self.do_stunt_fly),
            ("🔄 Backflip", self.do_stunt_flip),
            ("🐾 Roam / Walk", self.do_stunt_walk)
        ]
        for name, callback in stunts:
            b = Gtk.Button(label=name)
            b.add_css_class('lexi-stunt-chip')
            b.connect('clicked', lambda _, cb=callback: cb())
            stunt_grid.append(b)

        stunt_sec.append(stunt_grid)
        root.append(stunt_sec)

        # Bottom Bar: Close button
        bottom = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        bottom.set_halign(Gtk.Align.END)
        close_btn = Gtk.Button(label="Done")
        close_btn.add_css_class('lexi-submit-btn')
        close_btn.connect('clicked', lambda _: self.destroy())
        bottom.append(close_btn)
        root.append(bottom)

    def select_cat(self):
        self.mgr.set_character("cat")
        self.cat_card.add_css_class('lexi-avatar-card-active')
        self.custom_card.remove_css_class('lexi-avatar-card-active')
        self.cat_btn.set_label("Active ✓")
        self.custom_btn.set_label("Use Photo")

    def select_custom(self):
        if not self.mgr.custom_avatar_path or not os.path.exists(self.mgr.custom_avatar_path):
            self.open_file_picker()
            return
        self.mgr.set_character("custom")
        self.custom_card.add_css_class('lexi-avatar-card-active')
        self.cat_card.remove_css_class('lexi-avatar-card-active')
        self.custom_btn.set_label("Active ✓")
        self.cat_btn.set_label("Use Cat")

    def open_file_picker(self):
        dialog = Gtk.FileDialog()
        dialog.set_title("Select Photo or Mini Character Image")
        ff = Gtk.FileFilter()
        ff.set_name("Images (*.png, *.jpg, *.jpeg, *.webp)")
        ff.add_pattern("*.png")
        ff.add_pattern("*.jpg")
        ff.add_pattern("*.jpeg")
        ff.add_pattern("*.webp")
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(ff)
        dialog.set_filters(filters)

        def on_open_done(source, result):
            try:
                gfile = source.open_finish(result)
                if gfile:
                    path = gfile.get_path()
                    if path and os.path.exists(path):
                        self.mgr.set_character("custom", path)
                        avatar_name = os.path.basename(path)
                        self.custom_desc.set_text(f"Photo: {avatar_name}\nProcessed into an animated mini character with aura, chibi paws & feet.")
                        self.select_custom()
            except Exception as e:
                log(f"FileDialog error or cancelled: {e}")

        dialog.open(self, None, on_open_done)

    def _get_active_mascot(self):
        if self.parent_win and hasattr(self.parent_win, 'mascot') and self.parent_win.mascot:
            return self.parent_win.mascot
        if self.mgr.registered_mascots:
            return self.mgr.registered_mascots[0]
        return None

    def do_stunt_web(self):
        m = self._get_active_mascot()
        if m:
            if hasattr(self.parent_win, 'trigger_spiderweb_stunt'):
                self.parent_win.trigger_spiderweb_stunt()
            else:
                m.trigger_spiderweb()

    def do_stunt_perch(self):
        m = self._get_active_mascot()
        if m:
            if hasattr(self.parent_win, 'trigger_perch_stunt'):
                self.parent_win.trigger_perch_stunt()
            else:
                m.trigger_perch()

    def do_stunt_hang(self):
        m = self._get_active_mascot()
        if m:
            if hasattr(self.parent_win, 'trigger_hang_stunt'):
                self.parent_win.trigger_hang_stunt()
            else:
                m.trigger_hang_web()

    def do_stunt_fly(self):
        m = self._get_active_mascot()
        if m:
            if hasattr(self.parent_win, 'trigger_flight_stunt'):
                self.parent_win.trigger_flight_stunt()
            else:
                m.trigger_flight()

    def do_stunt_flip(self):
        m = self._get_active_mascot()
        if m:
            if hasattr(self.parent_win, 'trigger_backflip_stunt'):
                self.parent_win.trigger_backflip_stunt()
            else:
                m.trigger_backflip()

    def do_stunt_walk(self):
        m = self._get_active_mascot()
        if m:
            if hasattr(self.parent_win, 'trigger_walk_stunt'):
                self.parent_win.trigger_walk_stunt()
            else:
                m.trigger_walk()


class LexiWindow(Adw.ApplicationWindow):
    def __init__(self, app, config, is_snip=False):
        super().__init__(application=app, title="Lexi Cosmic Screen Copilot")
        self.app = app
        self.config = config
        self.is_snip = is_snip
        self.captured_pages = []  # List of {"b64": str, "texture": Gdk.Texture, "is_snip": bool}
        self.max_pages = 5
        self.saved_pos = None
        self.is_streaming = False
        self.timer_source_id = None
        self.query_start_time = 0
        self.conversation = []
        self.last_answer = ""
        self.turn_start_mark = None
        self.notch_win = None
        self.pet_win = None
        self.current_mode = "card"  # "card" or "side_panel"
        self.is_dragging_win = False
        self.card_w = 940
        self.card_h = 385
        self.screen_w = 1920
        self.screen_h = 1080

        self.set_default_size(940, 385)
        self.set_resizable(True)
        self.set_decorated(False)
        self.add_css_class('lexi-window')

        # Build UI layout
        self.build_ui()

        # Window-wide shortcut controller (Global scope)
        shortcut_ctrl = Gtk.ShortcutController.new()
        shortcut_ctrl.set_scope(Gtk.ShortcutScope.GLOBAL)

        esc_trig = Gtk.ShortcutTrigger.parse_string("Escape")
        esc_act = Gtk.CallbackAction.new(lambda *_: (self.collapse_to_notch(), True)[1])
        shortcut_ctrl.add_shortcut(Gtk.Shortcut.new(esc_trig, esc_act))

        cb_trig = Gtk.ShortcutTrigger.parse_string("<Control>b")
        cb_act = Gtk.CallbackAction.new(lambda *_: (self.toggle_mode(), True)[1])
        shortcut_ctrl.add_shortcut(Gtk.Shortcut.new(cb_trig, cb_act))

        cw_trig = Gtk.ShortcutTrigger.parse_string("<Control>w")
        cw_act = Gtk.CallbackAction.new(lambda *_: (self.animate_and_close(), True)[1])
        shortcut_ctrl.add_shortcut(Gtk.Shortcut.new(cw_trig, cw_act))

        cq_trig = Gtk.ShortcutTrigger.parse_string("<Control>q")
        cq_act = Gtk.CallbackAction.new(lambda *_: (self.animate_and_close(), True)[1])
        shortcut_ctrl.add_shortcut(Gtk.Shortcut.new(cq_trig, cq_act))

        cm_trig = Gtk.ShortcutTrigger.parse_string("<Control>m")
        cm_act = Gtk.CallbackAction.new(lambda *_: (self.collapse_to_notch(), True)[1])
        shortcut_ctrl.add_shortcut(Gtk.Shortcut.new(cm_trig, cm_act))

        cd_trig = Gtk.ShortcutTrigger.parse_string("<Control>d")
        cd_act = Gtk.CallbackAction.new(lambda *_: (self.detach_to_desktop(), True)[1])
        shortcut_ctrl.add_shortcut(Gtk.Shortcut.new(cd_trig, cd_act))

        cl_trig = Gtk.ShortcutTrigger.parse_string("<Control>l")
        cl_act = Gtk.CallbackAction.new(lambda *_: (self.on_clear_chat(None), True)[1])
        shortcut_ctrl.add_shortcut(Gtk.Shortcut.new(cl_trig, cl_act))

        co_trig = Gtk.ShortcutTrigger.parse_string("<Control>o")
        co_act = Gtk.CallbackAction.new(lambda *_: (self.on_feed_file_clicked(None), True)[1])
        shortcut_ctrl.add_shortcut(Gtk.Shortcut.new(co_trig, co_act))

        self.add_controller(shortcut_ctrl)

        # Keyboard controllers (Capture phase)
        key_ctrl = Gtk.EventControllerKey()
        key_ctrl.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        key_ctrl.connect('key-pressed', self.on_key_pressed)
        self.add_controller(key_ctrl)

        # Mouse pointer motion tracking for dynamic eye physics (capture across all child widgets)
        motion_ctrl = Gtk.EventControllerMotion()
        motion_ctrl.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        motion_ctrl.connect('motion', self.on_pointer_motion)
        self.add_controller(motion_ctrl)

        # Connect close request to clean up app reference
        self.connect('close-request', self.on_close_request)

        # Screen-wide pointer polling (active only when full window is visible)
        self.mouse_poll_id = None

        # Automatically reveal child if unminimized or activated
        def on_active_notify(win, _):
            if win.is_active() and hasattr(win, 'revealer') and not win.revealer.get_reveal_child():
                win.revealer.set_reveal_child(True)
        self.connect('notify::is-active', on_active_notify)

    def on_close_request(self, _):
        self.collapse_to_notch()
        return True

    LEXI_PET_REACTIONS = [
        ("✨", "✨ *stardust!* ( ⸝⸝•ᴗ•⸝⸝ ) Navigating your screen!"),
        ("🪐", "🪐 *orbital spin!* You're the brightest star in the cosmos!"),
        ("🌟", "🌟 ( ˶ˆ꒳ˆ˵ ) Cosmic focus activated! What are we exploring?"),
        ("🚀", "🚀 *blastoff!* Let's conquer this together!"),
        ("🛸", "🛸 ( •̀ ᴗ •́ )و Beaming down solutions from orbit!"),
        ("🌙", "🌙 (*˘︶˘*).｡.:*♡ Floating peacefully in starlight..."),
        ("💫", "💫 ( ⊙‿⊙ ) Gravity stabilized! All systems ready!"),
    ]

    def update_mascot_mood(self, mood):
        if hasattr(self, 'mascot') and self.mascot:
            self.mascot.set_mood(mood)
        if hasattr(self, 'header_mascot') and self.header_mascot:
            self.header_mascot.set_mood(mood)
        status_map = {
            "happy": "● Ready",
            "working": "● Thinking",
            "hungry": "● Feed Me",
            "star_eyes": "● Amazed",
            "dizzy": "● Alert"
        }
        st = status_map.get(mood, "● Ready")
        if hasattr(self, 'status_pill'):
            self.status_pill.set_text(st)
        if hasattr(self, 'header_status_pill'):
            self.header_status_pill.set_text(st)

    def on_pointer_motion(self, ctrl, x, y):
        dx = (x - 75.0) / 440.0
        dy = (y - 75.0) / 280.0
        if hasattr(self, 'mascot') and self.mascot:
            self.mascot.set_gaze(dx, dy)
        if hasattr(self, 'header_mascot') and self.header_mascot:
            self.header_mascot.set_gaze(dx, dy)

    def poll_screen_mouse(self):
        if not self.get_mapped() or not self.get_visible():
            return GLib.SOURCE_CONTINUE
        try:
            pt = X11Helper.get().get_pointer()
            if pt:
                mx, my = pt
                wx, wy = getattr(self, 'window_pos', (500, 30))
                dx = (mx - (wx + 75.0)) / 560.0
                dy = (my - (wy + 75.0)) / 420.0
                if hasattr(self, 'mascot') and self.mascot:
                    self.mascot.set_gaze(dx, dy)
                if hasattr(self, 'header_mascot') and self.header_mascot:
                    self.header_mascot.set_gaze(dx, dy)
        except Exception:
            pass
        return GLib.SOURCE_CONTINUE

    def on_pet_lexi(self, _):
        emoji, message = random.choice(self.LEXI_PET_REACTIONS)
        self.mascot.pet()
        self.status_pill.set_text(message)

        def reset_mood():
            if not self.is_streaming:
                count = len(self.captured_pages)
                self.status_pill.set_text(f"✨ {count}/{self.max_pages} Pages" if count > 0 else "⚠️ No pages")
            return GLib.SOURCE_REMOVE

        GLib.timeout_add(2500, reset_mood)

    def trigger_drop_animation(self):
        if not hasattr(self, 'revealer'):
            return
        if getattr(self, 'current_mode', 'card') == 'side_panel':
            self.revealer.set_transition_type(Gtk.RevealerTransitionType.SLIDE_LEFT)
        else:
            self.revealer.set_transition_type(Gtk.RevealerTransitionType.SLIDE_DOWN)
        self.revealer.set_transition_duration(220)
        self.revealer.set_reveal_child(True)

    def get_notch_window(self):
        if not self.notch_win:
            self.notch_win = LexiNotchWindow(self)
            self.notch_win.set_application(self.app)
        return self.notch_win

    def get_pet_window(self):
        if not self.pet_win:
            self.pet_win = LexiPetWindow(self)
            self.pet_win.set_application(self.app)
        return self.pet_win

    def expand_from_notch(self):
        if not self.mouse_poll_id:
            self.mouse_poll_id = GLib.timeout_add(40, self.poll_screen_mouse)
        if self.notch_win:
            self.notch_win.set_visible(False)
        if self.pet_win:
            self.pet_win.set_visible(False)
        self.position_at_top()
        if hasattr(self, 'revealer'):
            self.revealer.set_reveal_child(False)
            self.set_visible(True)
            self.present()
            self.position_at_top()
            GLib.timeout_add(20, self.trigger_drop_animation)
        else:
            self.set_visible(True)
            self.present()
            self.position_at_top()

        surface = self.get_surface()
        if surface and hasattr(surface, 'get_xid'):
            xid = surface.get_xid()
            X11Helper.get().activate_window(xid)

        if hasattr(self, 'entry'):
            self.entry.grab_focus()
            GLib.timeout_add(50, lambda: (self.entry.grab_focus(), GLib.SOURCE_REMOVE)[1])
            GLib.timeout_add(150, lambda: (self.entry.grab_focus(), GLib.SOURCE_REMOVE)[1])
            GLib.timeout_add(300, lambda: (self.entry.grab_focus(), GLib.SOURCE_REMOVE)[1])

    def collapse_to_notch(self):
        if self.mouse_poll_id:
            GLib.source_remove(self.mouse_poll_id)
            self.mouse_poll_id = None
        if self.pet_win:
            self.pet_win.set_visible(False)
        nw = self.get_notch_window()
        # Pre-position and show notch at the top BEFORE sliding copilot up
        nw.set_visible(True)
        nw.present()
        nw.position_at_top()

        def finish_collapse():
            self.set_visible(False)

        if hasattr(self, 'revealer'):
            if getattr(self, 'current_mode', 'card') == 'side_panel':
                self.revealer.set_transition_type(Gtk.RevealerTransitionType.SLIDE_RIGHT)
            else:
                self.revealer.set_transition_type(Gtk.RevealerTransitionType.SLIDE_UP)
            self.revealer.set_transition_duration(180)
            self.revealer.set_reveal_child(False)
            GLib.timeout_add(190, lambda: (finish_collapse(), GLib.SOURCE_REMOVE)[1])
        else:
            finish_collapse()

    def detach_to_desktop(self):
        if self.mouse_poll_id:
            GLib.source_remove(self.mouse_poll_id)
            self.mouse_poll_id = None
        pw = self.get_pet_window()
        pw.set_visible(True)
        pw.present()
        pw.position_on_desktop()

        def finish_detach():
            self.set_visible(False)
            if self.notch_win:
                self.notch_win.set_visible(False)

        if hasattr(self, 'revealer'):
            self.revealer.set_transition_type(Gtk.RevealerTransitionType.SLIDE_UP)
            self.revealer.set_transition_duration(180)
            self.revealer.set_reveal_child(False)
            GLib.timeout_add(190, lambda: (finish_detach(), GLib.SOURCE_REMOVE)[1])
        else:
            finish_detach()

    def open_avatar_dialog(self):
        dialog = LexiAvatarDialog(parent=self)
        dialog.present()

    def on_avatar_drop(self, target, value, x, y):
        paths = []
        if value is not None:
            if hasattr(value, 'get_files'):
                for gf in value.get_files():
                    p = gf.get_path()
                    if p and os.path.exists(p):
                        paths.append(p)
            elif isinstance(value, Gio.File):
                p = value.get_path()
                if p and os.path.exists(p):
                    paths.append(p)
            elif isinstance(value, str):
                for line in value.strip().splitlines():
                    p = line.strip().replace('file://', '')
                    if os.path.exists(p):
                        paths.append(p)
        if paths:
            ext = os.path.splitext(paths[0])[1].lower()
            if ext in ('.png', '.jpg', '.jpeg', '.webp'):
                AvatarManager.get().set_character("custom", paths[0])
                self.mascot.set_mood("happy")
                self.mascot.spawn_particle(0, 0, 'star', 12)
                self.mascot.spawn_particle(0, 0, 'sparkle', 8)
                self.mascot.trigger_jump()
                self.status_pill.set_text("✨ Mini Character Updated!")
                return True
        return False

    def animate_and_close(self):
        # Keep Dynamic Island permanent like iOS: collapse copilot back to notch
        self.collapse_to_notch()

    def animate_and_minimize(self):
        if getattr(self, '_is_minimizing', False):
            return
        self._is_minimizing = True
        if not hasattr(self, 'revealer'):
            self.minimize()
            self._is_minimizing = False
            return

        def on_revealed(rev, _):
            if not rev.get_reveal_child() and not rev.get_child_revealed():
                try:
                    rev.disconnect(handler_id)
                except Exception:
                    pass
                self.minimize()
                self._is_minimizing = False

        handler_id = self.revealer.connect('notify::child-revealed', on_revealed)
        self.revealer.set_transition_type(Gtk.RevealerTransitionType.SLIDE_UP)
        self.revealer.set_transition_duration(180)
        self.revealer.set_reveal_child(False)
        # Safe fallback in case signal is dropped
        GLib.timeout_add(220, lambda: (self.minimize(), setattr(self, '_is_minimizing', False), GLib.SOURCE_REMOVE)[2])

    def get_current_pos(self):
        return getattr(self, 'window_pos', (500, 30))

    def position_at_top(self):
        display = Gdk.Display.get_default()
        monitors = display.get_monitors() if display else None

        screen_x = 0
        screen_y = 0
        screen_w = 1920
        screen_h = 1080
        if monitors and monitors.get_n_items() > 0:
            m = monitors.get_item(0)
            geom = m.get_geometry()
            screen_x = geom.x
            screen_y = geom.y
            screen_w = geom.width
            screen_h = geom.height

        self.screen_w = screen_w
        self.screen_h = screen_h

        if getattr(self, 'current_mode', 'card') == 'side_panel':
            self.card_w = 440
            self.card_h = screen_h - 38
            pos_x = screen_x + screen_w - self.card_w
            pos_y = screen_y + 35
        else:
            self.card_w = 940
            self.card_h = 385
            pos_x = screen_x + (screen_w - self.card_w) // 2
            pos_y = screen_y + 34

        self.window_pos = (pos_x, pos_y)
        self.saved_pos = (pos_x, pos_y)

        surface = self.get_surface()
        if surface and hasattr(surface, 'get_xid'):
            try:
                xid = surface.get_xid()
                X11Helper.get().move_resize_window(xid, pos_x, pos_y, self.card_w, self.card_h)
                X11Helper.get().make_always_on_top_and_sticky(xid, is_dock=False)
                if hasattr(surface, 'move_to_desktop'):
                    surface.move_to_desktop(0xFFFFFFFF)
            except Exception as e:
                log(f"Window move/above exception: {e}")

    def toggle_mode(self):
        new_mode = 'side_panel' if getattr(self, 'current_mode', 'card') == 'card' else 'card'
        self.set_mode(new_mode)

    def set_mode(self, mode):
        self.current_mode = mode
        surface = self.get_surface()
        xid = surface.get_xid() if (surface and hasattr(surface, 'get_xid')) else None

        display = Gdk.Display.get_default()
        if display and display.get_monitors():
            geom = display.get_monitors().get_item(0).get_geometry()
            self.screen_w = geom.width
            self.screen_h = geom.height

        if mode == 'side_panel':
            self.card_w = 440
            self.card_h = self.screen_h - 38
            pos_x = self.screen_w - self.card_w
            pos_y = 35

            self.stage_box.set_visible(False)
            self.header_mascot.set_visible(True)
            self.card.add_css_class('lexi-side-panel')
            self.subtitle.set_text("Side Panel")
            self.mode_toggle_btn.set_label("Center Card")
            self.mode_toggle_btn.set_tooltip_text("Switch to Floating Center Card (Ctrl+B or drag left)")
            if hasattr(self, 'detach_header_btn'):
                self.detach_header_btn.set_visible(False)
            self.clear_btn.set_label("Clear")
            if hasattr(self, 'action_strip'):
                self.action_strip.set_orientation(Gtk.Orientation.VERTICAL)
            if hasattr(self, 'strip_sep'):
                self.strip_sep.set_visible(False)
            if hasattr(self, 'pages_tray_scroll'):
                self.pages_tray_scroll.set_max_content_width(-1)

            self.set_default_size(self.card_w, self.card_h)
            self.set_size_request(self.card_w, self.card_h)
            if xid:
                X11Helper.get().move_resize_window(xid, pos_x, pos_y, self.card_w, self.card_h)
                X11Helper.get().make_always_on_top_and_sticky(xid, is_dock=False)
            self.window_pos = (pos_x, pos_y)
            self.saved_pos = (pos_x, pos_y)

            def enforce_dock():
                s = self.get_surface()
                cur_x = s.get_xid() if (s and hasattr(s, 'get_xid')) else xid
                if cur_x:
                    X11Helper.get().move_resize_window(cur_x, pos_x, pos_y, self.card_w, self.card_h)
                    X11Helper.get().make_always_on_top_and_sticky(cur_x, is_dock=False)
                return GLib.SOURCE_REMOVE

            GLib.timeout_add(30, enforce_dock)
            GLib.timeout_add(100, enforce_dock)

        else: # 'card'
            self.card_w = 940
            self.card_h = 385
            pos_x = (self.screen_w - self.card_w) // 2
            pos_y = 34

            self.stage_box.set_visible(True)
            self.header_mascot.set_visible(False)
            self.card.remove_css_class('lexi-side-panel')
            self.subtitle.set_text("Screen Copilot")
            self.mode_toggle_btn.set_label("Side Panel")
            self.mode_toggle_btn.set_tooltip_text("Dock to vertical Side Panel (Ctrl+B or drag to right edge)")
            if hasattr(self, 'detach_header_btn'):
                self.detach_header_btn.set_visible(True)
            self.clear_btn.set_label("Clear")
            if hasattr(self, 'action_strip'):
                self.action_strip.set_orientation(Gtk.Orientation.HORIZONTAL)
            if hasattr(self, 'strip_sep'):
                self.strip_sep.set_visible(True)
            if hasattr(self, 'pages_tray_scroll'):
                self.pages_tray_scroll.set_max_content_width(480)

            self.set_default_size(self.card_w, self.card_h)
            self.set_size_request(self.card_w, self.card_h)
            if xid:
                X11Helper.get().move_resize_window(xid, pos_x, pos_y, self.card_w, self.card_h)
                X11Helper.get().make_always_on_top_and_sticky(xid, is_dock=False)
            self.window_pos = (pos_x, pos_y)
            self.saved_pos = (pos_x, pos_y)

            def enforce_center():
                s = self.get_surface()
                cur_x = s.get_xid() if (s and hasattr(s, 'get_xid')) else xid
                if cur_x:
                    X11Helper.get().move_resize_window(cur_x, pos_x, pos_y, self.card_w, self.card_h)
                    X11Helper.get().make_always_on_top_and_sticky(cur_x, is_dock=False)
                return GLib.SOURCE_REMOVE

            GLib.timeout_add(30, enforce_center)
            GLib.timeout_add(100, enforce_center)

        self.update_mascot_mood(getattr(self.mascot, 'mood', 'happy'))

    def on_header_drag_begin(self, gesture, x, y):
        self.is_dragging_win = True
        pt = X11Helper.get().get_pointer()
        if pt:
            self.drag_start_ptr = pt
        else:
            self.drag_start_ptr = (960, 540)
        self.drag_start_win = getattr(self, 'window_pos', (490, 34))

    def on_header_drag_update(self, gesture, offset_x, offset_y):
        surface = self.get_surface()
        if not surface or not hasattr(surface, 'get_xid'):
            return
        xid = surface.get_xid()
        new_x = int(self.drag_start_win[0] + offset_x)
        new_y = int(self.drag_start_win[1] + offset_y)
        new_y = max(10, min(self.screen_h - 100, new_y))

        X11Helper.get().move_window(xid, new_x, new_y)
        self.window_pos = (new_x, new_y)

        if self.current_mode == 'card':
            if new_x + self.card_w >= self.screen_w - 90 or offset_x > 220:
                self.card.add_css_class('lexi-drag-snap-candidate')
                self.subtitle.set_text("👉 Release to dock as Side Panel!")
            else:
                self.card.remove_css_class('lexi-drag-snap-candidate')
                self.subtitle.set_text("Cosmic Screen Copilot")
        else:
            if offset_x < -90:
                self.card.add_css_class('lexi-drag-snap-candidate')
                self.subtitle.set_text("👈 Release to undock into Floating Card!")
            else:
                self.card.remove_css_class('lexi-drag-snap-candidate')
                self.subtitle.set_text("Side Panel")

    def on_header_drag_end(self, gesture, offset_x, offset_y):
        self.is_dragging_win = False
        self.card.remove_css_class('lexi-drag-snap-candidate')

        if self.current_mode == 'card':
            if self.window_pos[0] + self.card_w >= self.screen_w - 90 or offset_x > 220:
                self.set_mode('side_panel')
            else:
                self.subtitle.set_text("Cosmic Screen Copilot")
        else:
            if offset_x < -100:
                self.set_mode('card')
            else:
                pos_x = self.screen_w - self.card_w
                pos_y = 35
                surface = self.get_surface()
                if surface and hasattr(surface, 'get_xid'):
                    X11Helper.get().move_window(surface.get_xid(), pos_x, pos_y)
                self.window_pos = (pos_x, pos_y)
                self.subtitle.set_text("Side Panel")

    def build_ui(self):
        # Main Revealer Container (Slides down smoothly on open, slides up on close)
        self.revealer = Gtk.Revealer()
        self.revealer.set_transition_type(Gtk.RevealerTransitionType.SLIDE_DOWN)
        self.revealer.set_transition_duration(220)
        self.revealer.set_vexpand(True)
        self.revealer.set_hexpand(True)
        self.revealer.set_reveal_child(True)
        self.set_content(self.revealer)

        # Main Card Container: Horizontal 2-Column Split
        self.card = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16)
        self.card.add_css_class('lexi-card')
        self.card.set_vexpand(True)
        self.card.set_hexpand(True)
        self.revealer.set_child(self.card)

        # Setup Drag and Drop File Feeding (Preloaded, window-level)
        drop_target = Gtk.DropTarget(actions=Gdk.DragAction.COPY)
        drop_target.set_gtypes([Gdk.FileList, Gio.File, str])
        drop_target.set_preload(True)
        drop_target.connect('enter', self.on_drag_enter)
        drop_target.connect('motion', self.on_drag_motion)
        drop_target.connect('leave', self.on_drag_leave)
        drop_target.connect('drop', self.on_drag_drop)
        self.add_controller(drop_target)

        # Click anywhere on card background focuses entry
        click_gesture = Gtk.GestureClick()
        click_gesture.connect('pressed', lambda *_: self.entry.grab_focus())
        self.card.add_controller(click_gesture)

        # --- Left Companion Stage (Full-body Animated Lexi & Emotion Badge) ---
        self.stage_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.stage_box.set_valign(Gtk.Align.CENTER)
        self.stage_box.set_size_request(130, -1)
        self.stage_box.add_css_class('lexi-companion-stage')
        self.card.append(self.stage_box)

        self.avatar_btn = Gtk.Button()
        self.avatar_btn.set_focusable(False)
        self.avatar_btn.add_css_class('lexi-mascot-btn')
        self.avatar_btn.set_tooltip_text("Click to pet or cycle stunts! (or drag & drop a photo here to customize avatar)")

        self.mascot = LexiMascotWidget(width=130, height=145)
        self.avatar_btn.set_child(self.mascot)
        self.avatar_btn.connect('clicked', self.on_pet_lexi)

        avatar_drop = Gtk.DropTarget(actions=Gdk.DragAction.COPY)
        avatar_drop.set_gtypes([Gdk.FileList, Gio.File, str])
        avatar_drop.set_preload(True)
        avatar_drop.connect('drop', self.on_avatar_drop)
        self.avatar_btn.add_controller(avatar_drop)

        self.stage_box.append(self.avatar_btn)

        self.status_pill = Gtk.Label(label="Snipping..." if self.is_snip else "● Ready")
        self.status_pill.add_css_class('lexi-status-pill')
        self.stage_box.append(self.status_pill)

        # Quick action buttons row: Character & Stunts
        btns_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        btns_row.set_halign(Gtk.Align.CENTER)

        self.character_btn = Gtk.Button(label="Character")
        self.character_btn.set_focusable(False)
        self.character_btn.add_css_class('lexi-character-btn')
        self.character_btn.set_tooltip_text("Customize avatar: Lexi Cat or your own mini character photo")
        self.character_btn.connect('clicked', lambda _: self.open_avatar_dialog())
        btns_row.append(self.character_btn)

        self.stunt_btn = Gtk.Button(label="Stunts")
        self.stunt_btn.set_focusable(False)
        self.stunt_btn.add_css_class('lexi-stunt-btn')
        self.stunt_btn.set_tooltip_text("Perform acrobatic stunts: Spider-web grapple, Perch, Flight, Backflip!")
        self.stunt_btn.connect('clicked', lambda _: self.mascot.cycle_next_activity())
        btns_row.append(self.stunt_btn)

        self.stage_box.append(btns_row)

        self.detach_btn = Gtk.Button(label="Detach Pet")
        self.detach_btn.set_focusable(False)
        self.detach_btn.add_css_class('lexi-detach-btn')
        self.detach_btn.set_tooltip_text("Detach Lexi onto your desktop wallpaper (Ctrl+D)")
        self.detach_btn.connect('clicked', lambda _: self.detach_to_desktop())
        self.stage_box.append(self.detach_btn)

        # --- Right Content Column (Wide macOS-style Island) ---
        self.content_col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.content_col.set_hexpand(True)
        self.card.append(self.content_col)

        # Header with draggable title and window controls
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self.header = header
        self.content_col.append(header)

        # Title / Handle box (drags window and snaps to edges)
        self.title_handle_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.title_handle_box.set_hexpand(True)
        self.title_handle_box.set_valign(Gtk.Align.CENTER)
        header.append(self.title_handle_box)

        # Mini mascot in header (active in side panel mode)
        self.header_mascot = LexiMascotWidget(width=28, height=28, is_mini=True)
        self.header_mascot.add_css_class('lexi-header-mini-mascot')
        self.header_mascot.set_visible(False)
        self.title_handle_box.append(self.header_mascot)

        title_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        title = Gtk.Label(label="Lexi", xalign=0)
        title.add_css_class('lexi-title')
        self.subtitle = Gtk.Label(label="Screen Copilot", xalign=0)
        self.subtitle.set_ellipsize(Pango.EllipsizeMode.END)
        self.subtitle.add_css_class('lexi-subtitle')
        title_box.append(title)
        title_box.append(self.subtitle)
        self.title_handle_box.append(title_box)

        # Smooth zero-latency X11 dragging & edge snapping on header
        header_drag = Gtk.GestureDrag()
        header_drag.connect('drag-begin', self.on_header_drag_begin)
        header_drag.connect('drag-update', self.on_header_drag_update)
        header_drag.connect('drag-end', self.on_header_drag_end)
        self.title_handle_box.add_controller(header_drag)

        ctrls_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        ctrls_box.add_css_class('mac-traffic-lights')
        header.append(ctrls_box)

        # Side Panel toggle button
        self.mode_toggle_btn = Gtk.Button(label="Side Panel")
        self.mode_toggle_btn.set_focusable(False)
        self.mode_toggle_btn.set_tooltip_text("Switch to vertical Side Panel (Ctrl+B or drag to right edge)")
        self.mode_toggle_btn.add_css_class('lexi-mode-btn')
        self.mode_toggle_btn.connect('clicked', lambda _: self.toggle_mode())
        ctrls_box.append(self.mode_toggle_btn)

        # Detach pet button (in header)
        self.detach_header_btn = Gtk.Button(label="Detach")
        self.detach_header_btn.set_focusable(False)
        self.detach_header_btn.set_tooltip_text("Detach Lexi to roam desktop (Ctrl+D)")
        self.detach_header_btn.add_css_class('lexi-header-btn')
        self.detach_header_btn.connect('clicked', lambda _: self.detach_to_desktop())
        ctrls_box.append(self.detach_header_btn)

        self.clear_btn = Gtk.Button(label="Clear")
        self.clear_btn.set_focusable(False)
        self.clear_btn.set_tooltip_text("Clear chat history (Ctrl+L)")
        self.clear_btn.add_css_class('lexi-header-btn')
        self.clear_btn.connect('clicked', self.on_clear_chat)
        ctrls_box.append(self.clear_btn)

        self.min_dot = Gtk.Button()
        self.min_dot.set_focusable(False)
        self.min_dot.set_valign(Gtk.Align.CENTER)
        self.min_dot.set_size_request(13, 13)
        self.min_dot.set_tooltip_text("Collapse to Dynamic Island (Esc / Ctrl+M)")
        self.min_dot.add_css_class('mac-dot')
        self.min_dot.add_css_class('mac-dot-min')
        self.min_dot.connect('clicked', lambda _: self.collapse_to_notch())
        ctrls_box.append(self.min_dot)

        self.close_dot = Gtk.Button()
        self.close_dot.set_focusable(False)
        self.close_dot.set_valign(Gtk.Align.CENTER)
        self.close_dot.set_tooltip_text("Collapse to Dynamic Island (Esc)")
        self.close_dot.add_css_class('mac-dot')
        self.close_dot.add_css_class('mac-dot-close')
        self.close_dot.connect('clicked', lambda _: self.collapse_to_notch())
        ctrls_box.append(self.close_dot)

        # Unified Wide Action Strip (Pages Tray + Quick Chips)
        action_strip = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        action_strip.add_css_class('lexi-action-strip')
        self.action_strip = action_strip
        self.content_col.append(action_strip)

        self.pages_tray_scroll = Gtk.ScrolledWindow()
        self.pages_tray_scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.NEVER)
        self.pages_tray_scroll.set_propagate_natural_width(True)
        self.pages_tray_scroll.set_hexpand(True)
        self.pages_tray_scroll.add_css_class('lexi-pages-tray-scroll')
        action_strip.append(self.pages_tray_scroll)

        self.pages_tray = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.pages_tray.add_css_class('lexi-pages-tray')
        self.pages_tray.set_valign(Gtk.Align.CENTER)
        self.pages_tray_scroll.set_child(self.pages_tray)

        self.tray_lbl = Gtk.Label(label="Pages (0/5):")
        self.tray_lbl.add_css_class('lexi-tray-label')
        self.pages_tray.append(self.tray_lbl)

        # Direct box container for page cards (never squashed or clipped)
        self.pages_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.pages_box.add_css_class('lexi-pages-box')
        self.pages_box.set_valign(Gtk.Align.CENTER)
        self.pages_tray.append(self.pages_box)

        self.add_snip_btn = Gtk.Button(label="Snip")
        self.add_snip_btn.set_focusable(False)
        self.add_snip_btn.set_tooltip_text("Snip an additional area (Lexi hides completely)")
        self.add_snip_btn.add_css_class('lexi-tray-btn')
        self.add_snip_btn.add_css_class('lexi-tray-btn-accent')
        self.add_snip_btn.connect('clicked', self.on_add_snip)
        self.pages_tray.append(self.add_snip_btn)

        self.add_full_btn = Gtk.Button(label="Screen")
        self.add_full_btn.set_focusable(False)
        self.add_full_btn.set_tooltip_text("Add entire screen as next page")
        self.add_full_btn.add_css_class('lexi-tray-btn')
        self.add_full_btn.connect('clicked', self.on_add_full)
        self.pages_tray.append(self.add_full_btn)

        self.add_feed_btn = Gtk.Button(label="Feed")
        self.add_feed_btn.set_focusable(False)
        self.add_feed_btn.set_tooltip_text("Feed an image, code file, or PDF to Lexi (Ctrl+O)")
        self.add_feed_btn.add_css_class('lexi-tray-btn')
        self.add_feed_btn.connect('clicked', self.on_feed_file_clicked)
        self.pages_tray.append(self.add_feed_btn)

        self.recapture_btn = Gtk.Button(label="New")
        self.recapture_btn.set_focusable(False)
        self.recapture_btn.set_tooltip_text("Clear all pages and capture fresh screen")
        self.recapture_btn.add_css_class('lexi-tray-btn')
        self.recapture_btn.connect('clicked', self.on_recapture)
        self.pages_tray.append(self.recapture_btn)

        strip_sep = Gtk.Separator(orientation=Gtk.Orientation.VERTICAL)
        self.strip_sep = strip_sep
        action_strip.append(strip_sep)

        chips_scroll = Gtk.ScrolledWindow()
        chips_scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.NEVER)
        chips_scroll.add_css_class('lexi-chips-scroll')
        chips_scroll.set_hexpand(True)
        chips_scroll.set_propagate_natural_width(False)
        self.chips_scroll = chips_scroll
        chips_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        chips_scroll.set_child(chips_box)
        action_strip.append(chips_scroll)

        quick_prompts = [
            ("System", "/help"),
            ("Explain", "Explain what is on the attached page(s) and fed files clearly and concisely."),
            ("Fix", "Identify any errors, bugs, or problems across these pages and code files and provide the fix."),
            ("Summarize", "Provide a high-signal executive summary of the attached content."),
            ("Volume", "/vol 80%"),
            ("Extract Text", "Extract all key text, commands, or code from the attached pages."),
        ]

        for label_text, prompt_text in quick_prompts:
            btn = Gtk.Button(label=label_text)
            btn.set_focusable(False)
            btn.add_css_class('lexi-pill-btn')
            btn.connect('clicked', lambda _, p=prompt_text: self.on_quick_prompt(p))
            chips_box.append(btn)

        # Input Bar (Spotlight / Raycast macOS style)
        input_container = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        input_container.add_css_class('lexi-entry-box')
        self.content_col.append(input_container)

        self.entry = Gtk.Entry()
        self.entry.set_placeholder_text("Ask Lexi or command your system... ($ cmd, /open, /vol, /wa or /help)")
        self.entry.set_hexpand(True)
        self.entry.set_focusable(True)
        self.entry.add_css_class('lexi-entry')
        self.entry.connect('activate', self.on_submit)
        input_container.append(self.entry)

        self.send_btn = Gtk.Button.new_from_icon_name('arrow-up-symbolic')
        self.send_btn.set_focusable(False)
        self.send_btn.add_css_class('lexi-send-btn')
        self.send_btn.connect('clicked', self.on_submit)
        input_container.append(self.send_btn)

        # Answer / Multi-Turn Response Area
        self.response_container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.response_container.add_css_class('lexi-response-box')
        self.response_container.set_vexpand(True)
        self.content_col.append(self.response_container)

        # Thinking spinner bar (hidden by default)
        self.thinking_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self.spinner = Gtk.Spinner()
        self.thinking_label = Gtk.Label(label="Lexi is analyzing your screen...")
        self.thinking_label.add_css_class('lexi-thinking')
        self.thinking_box.append(self.spinner)
        self.thinking_box.append(self.thinking_label)
        self.thinking_box.set_visible(False)
        self.response_container.append(self.thinking_box)

        # Scrolled Text View with Markdown Tag Table
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_vexpand(True)
        scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        self.response_container.append(scrolled)

        tag_table = create_markdown_tag_table()
        self.text_buffer = Gtk.TextBuffer.new(tag_table)
        self.text_view = Gtk.TextView.new_with_buffer(self.text_buffer)
        self.text_view.set_editable(False)
        self.text_view.set_cursor_visible(False)
        self.text_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.text_view.add_css_class('lexi-answer-text')
        scrolled.set_child(self.text_view)

        init_hint = "Select an area to snip..." if self.is_snip else "Looking at your screen right now..."
        self.text_buffer.set_text(init_hint)

        # Bottom Toolbar
        bottom_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.response_container.append(bottom_bar)

        self.copy_btn = Gtk.Button(label="Copy Answer")
        self.copy_btn.set_focusable(False)
        self.copy_btn.add_css_class('flat')
        self.copy_btn.add_css_class('lexi-action-btn')
        self.copy_btn.connect('clicked', self.on_copy)
        bottom_bar.append(self.copy_btn)

        bottom_spacer = Gtk.Box()
        bottom_spacer.set_hexpand(True)
        bottom_bar.append(bottom_spacer)

        dismiss_hint = Gtk.Label(label="Ctrl+B: Side Panel • Esc: Notch • Super+C: Add Page")
        dismiss_hint.set_ellipsize(Pango.EllipsizeMode.END)
        dismiss_hint.add_css_class('lexi-subtitle')
        bottom_bar.append(dismiss_hint)

    def update_pages_tray_ui(self):
        # Clear existing cards
        while child := self.pages_box.get_first_child():
            self.pages_box.remove(child)

        for idx, page in enumerate(self.captured_pages, 1):
            card = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            card.add_css_class('lexi-page-card')
            card.set_valign(Gtk.Align.CENTER)

            img = Gtk.Image.new_from_paintable(page["texture"])
            img.add_css_class('lexi-page-img')
            img.set_size_request(28, 20)
            card.append(img)

            if page.get("is_file"):
                raw_name = page.get("name", f"File {idx}")
                title_txt = raw_name if len(raw_name) <= 12 else raw_name[:5] + ".." + raw_name[-5:]
            elif page.get("is_snip"):
                title_txt = f"Page {idx} (snip)"
            else:
                title_txt = f"Page {idx}"

            lbl = Gtk.Label(label=title_txt)
            lbl.add_css_class('lexi-page-title')
            lbl.set_valign(Gtk.Align.CENTER)
            card.append(lbl)

            del_btn = Gtk.Button()
            del_lbl = Gtk.Label(label="✕")
            del_lbl.add_css_class('lexi-del-icon')
            del_btn.set_child(del_lbl)
            del_btn.set_focusable(False)
            del_btn.set_valign(Gtk.Align.CENTER)
            del_btn.add_css_class('lexi-page-del-btn')
            del_btn.set_tooltip_text(f"Remove {page.get('name', f'Page {idx}')}")
            del_btn.connect('clicked', lambda _, p_idx=idx-1: self.remove_page(p_idx))
            card.append(del_btn)

            self.pages_box.append(card)

        count = len(self.captured_pages)
        can_add = count < self.max_pages
        self.add_snip_btn.set_sensitive(can_add)
        self.add_full_btn.set_sensitive(can_add)
        if hasattr(self, 'add_feed_btn'):
            self.add_feed_btn.set_sensitive(can_add)

        if hasattr(self, 'tray_lbl'):
            self.tray_lbl.set_text(f"Pages ({count}/5):")

        self.add_snip_btn.set_label("✂️ Snip")
        self.add_full_btn.set_label("🖥️ Screen")
        if hasattr(self, 'add_feed_btn'):
            self.add_feed_btn.set_label("📁 Feed")
        self.recapture_btn.set_label("🔄 New")

        if count == 0:
            self.status_pill.set_text("⚠️ No pages")
            if hasattr(self, 'subtitle'):
                prefix = "Cosmic Screen Copilot" if getattr(self, 'current_mode', 'card') == 'card' else "Side Panel"
                self.subtitle.set_text(prefix)
        else:
            self.status_pill.set_text(f"● {count}/5 Pages")
            if hasattr(self, 'subtitle'):
                prefix = "Cosmic Screen Copilot" if getattr(self, 'current_mode', 'card') == 'card' else "Side Panel"
                self.subtitle.set_text(f"{prefix} • {count}/5 Pages ready")

    def remove_page(self, page_index):
        if 0 <= page_index < len(self.captured_pages):
            self.captured_pages.pop(page_index)
            self.conversation = []  # Reset context when page set changes
            self.update_pages_tray_ui()
            count = len(self.captured_pages)
            if hasattr(self, 'mascot'):
                self.update_mascot_mood('happy')
            if count == 0:
                self.text_buffer.set_text("⚠️ No pages attached. Click '✂️ Snip', '🖥️ Screen', or '📁 Feed' above to attach.")
            else:
                self.text_buffer.set_text(f"📄 Page removed. {count} page(s) currently attached.")

    def make_doc_icon_texture(self, filename, badge_text="FILE"):
        w, h = 48, 48
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)
        cr = cairo.Context(surface)
        cr.save()
        cr.translate(w / 2.0, h / 2.0)
        doc_w, doc_h = 32, 38
        cr.rectangle(-doc_w / 2.0, -doc_h / 2.0, doc_w, doc_h)

        b_upper = badge_text.upper()
        pat = cairo.LinearGradient(0, -doc_h / 2.0, 0, doc_h / 2.0)
        if b_upper in ("PY", "JS", "TS", "RS", "C", "CPP", "GO", "SH"):
            pat.add_color_stop_rgb(0, 0.58, 0.38, 0.98)
            pat.add_color_stop_rgb(1, 0.36, 0.20, 0.78)
        elif b_upper == "PDF":
            pat.add_color_stop_rgb(0, 0.95, 0.35, 0.45)
            pat.add_color_stop_rgb(1, 0.75, 0.15, 0.25)
        else:
            pat.add_color_stop_rgb(0, 0.35, 0.65, 0.95)
            pat.add_color_stop_rgb(1, 0.18, 0.42, 0.82)

        cr.set_source(pat)
        cr.fill_preserve()
        cr.set_line_width(1.0)
        cr.set_source_rgba(1.0, 1.0, 1.0, 0.35)
        cr.stroke()

        # Dog-ear fold top-right
        cr.move_to(doc_w / 2.0 - 9, -doc_h / 2.0)
        cr.line_to(doc_w / 2.0, -doc_h / 2.0 + 9)
        cr.line_to(doc_w / 2.0 - 9, -doc_h / 2.0 + 9)
        cr.close_path()
        cr.set_source_rgba(1.0, 1.0, 1.0, 0.45)
        cr.fill()

        # Badge text
        cr.set_source_rgb(1.0, 1.0, 1.0)
        cr.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
        cr.set_font_size(9)
        ext = cr.text_extents(badge_text[:4])
        cr.move_to(-ext.width / 2.0, 4)
        cr.show_text(badge_text[:4])

        # Code lines
        cr.set_line_width(1.2)
        cr.set_source_rgba(1.0, 1.0, 1.0, 0.65)
        cr.move_to(-doc_w / 2.0 + 5, 11)
        cr.line_to(doc_w / 2.0 - 5, 11)
        cr.stroke()
        cr.restore()
        surface.flush()

        gbytes = GLib.Bytes.new(surface.get_data().tobytes())
        return Gdk.MemoryTexture.new(w, h, Gdk.MemoryFormat.B8G8R8A8_PREMULTIPLIED, gbytes, surface.get_stride())

    def feed_file(self, filepath):
        if not filepath:
            return
        filepath = os.path.expanduser(urllib.parse.unquote(str(filepath).replace('file://', '')))
        if not os.path.exists(filepath):
            log(f"feed_file: file does not exist: {filepath}")
            self.status_pill.set_text(f"⚠️ Cannot find {os.path.basename(filepath)}")
            return

        if len(self.captured_pages) >= self.max_pages:
            self.status_pill.set_text("⚠️ Lexi is full! (5/5)")
            return

        filename = os.path.basename(filepath)
        ext = os.path.splitext(filepath)[1].lower()

        # Chewing nom-nom eating state!
        self.status_pill.set_text(f"😋 Nom nom... Eating {filename}!")
        self.stage_box.add_css_class('lexi-stage-eating')

        def on_eaten():
            self.stage_box.remove_css_class('lexi-stage-eating')
            page_data = None
            if ext in ('.png', '.jpg', '.jpeg', '.webp', '.svg', '.bmp', '.gif'):
                try:
                    texture = Gdk.Texture.new_from_filename(filepath)
                    # Convert to base64 for LLM
                    with open(filepath, 'rb') as f:
                        b64 = base64.b64encode(f.read()).decode('utf-8')
                    page_data = {
                        "path": filepath,
                        "b64": b64,
                        "texture": texture,
                        "is_snip": False,
                        "is_file": True,
                        "name": filename,
                        "file_type": "image"
                    }
                except Exception as e:
                    log(f"Failed to load image file {filepath}: {e}")

            elif ext == '.pdf':
                thumb_base = f"/tmp/lexi_pdf_{int(time.time() * 1000)}"
                thumb_img = f"{thumb_base}-1.png"
                texture = None
                try:
                    subprocess.run(
                        ['pdftoppm', '-png', '-r', '90', '-f', '1', '-l', '1', filepath, thumb_base],
                        check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
                    )
                    if os.path.exists(thumb_img):
                        texture = Gdk.Texture.new_from_filename(thumb_img)
                except Exception:
                    pass

                if not texture:
                    texture = self.make_doc_icon_texture(filename, "PDF")

                text_content = ""
                try:
                    res = subprocess.run(['pdftotext', '-f', '1', '-l', '6', filepath, '-'],
                                         capture_output=True, text=True)
                    text_content = res.stdout[:35000]
                except Exception:
                    pass

                b64 = ""
                if os.path.exists(thumb_img):
                    try:
                        with open(thumb_img, 'rb') as f:
                            b64 = base64.b64encode(f.read()).decode('utf-8')
                    except Exception:
                        pass

                page_data = {
                    "path": filepath,
                    "b64": b64,
                    "texture": texture,
                    "is_snip": False,
                    "is_file": True,
                    "name": filename,
                    "file_type": "pdf",
                    "text_content": text_content
                }

            else:
                # Text, code, config files
                text_content = ""
                try:
                    with open(filepath, 'r', errors='ignore') as f:
                        text_content = f.read(35000)
                except Exception as e:
                    log(f"Failed to read file {filepath}: {e}")

                badge = ext[1:].upper() if ext else "TXT"
                texture = self.make_doc_icon_texture(filename, badge[:4])
                page_data = {
                    "path": filepath,
                    "b64": "",
                    "texture": texture,
                    "is_snip": False,
                    "is_file": True,
                    "name": filename,
                    "file_type": "text",
                    "text_content": text_content
                }

            if page_data:
                self.captured_pages.append(page_data)
                self.update_pages_tray_ui()
                count = len(self.captured_pages)
                self.status_pill.set_text(f"✨ Ate {filename}! ({count}/5)")
                self.entry.set_placeholder_text(f"Ask anything about {filename}...")
                self.entry.grab_focus()

        self.mascot.feed_file(filename, on_finished=on_eaten)

    def on_feed_file_clicked(self, _):
        try:
            file_dialog = Gtk.FileDialog()
            file_dialog.set_title("Feed a file to Lexi 🐟✨")

            all_filter = Gtk.FileFilter()
            all_filter.set_name("All Supported (Images, Code, Documents, PDFs)")
            for pattern in ["*.png", "*.jpg", "*.jpeg", "*.webp", "*.svg", "*.bmp", "*.pdf",
                            "*.py", "*.js", "*.ts", "*.json", "*.md", "*.txt", "*.html",
                            "*.css", "*.rs", "*.go", "*.c", "*.cpp", "*.sh", "*.yml", "*.yaml"]:
                all_filter.add_pattern(pattern)

            filters = Gio.ListStore.new(Gtk.FileFilter)
            filters.append(all_filter)
            file_dialog.set_filters(filters)

            def on_file_selected(dialog, result):
                try:
                    gfile = dialog.open_finish(result)
                    if gfile:
                        p = gfile.get_path()
                        if p and os.path.exists(p):
                            self.feed_file(p)
                except Exception as e:
                    log(f"File dialog selection error: {e}")

            file_dialog.open(self, None, on_file_selected)
            return
        except Exception as e:
            log(f"Gtk.FileDialog error: {e}, falling back to zenity...")

        try:
            res = subprocess.run(['zenity', '--file-selection', '--title=Feed a file to Lexi 🐟✨'],
                                 capture_output=True, text=True, check=False)
            if res.returncode == 0 and res.stdout.strip():
                p = res.stdout.strip()
                if os.path.exists(p):
                    self.feed_file(p)
        except Exception as err:
            log(f"Zenity fallback error: {err}")

    def on_drag_enter(self, target, x, y):
        self.mascot.set_mood("hungry")
        self.stage_box.add_css_class('lexi-stage-hover')
        self.status_pill.set_text("😋 Feed Lexi! Drop file here...")
        return Gdk.DragAction.COPY

    def on_drag_motion(self, target, x, y):
        return Gdk.DragAction.COPY

    def on_drag_leave(self, target):
        self.stage_box.remove_css_class('lexi-stage-hover')
        if self.mascot.mood == "hungry":
            self.mascot.reset_mood()
        count = len(self.captured_pages)
        self.status_pill.set_text(f"● {count}/5 Pages" if count > 0 else "● Ready")

    def on_drag_drop(self, target, value, x, y):
        self.stage_box.remove_css_class('lexi-stage-hover')
        paths = []
        log(f"Drag drop received value: {value} (type: {type(value)})")
        if value is not None:
            if hasattr(value, 'get_files'):
                for gf in value.get_files():
                    p = gf.get_path()
                    if p and os.path.exists(p):
                        paths.append(p)
            elif isinstance(value, Gio.File):
                p = value.get_path()
                if p and os.path.exists(p):
                    paths.append(p)
            elif isinstance(value, str):
                for line in value.strip().splitlines():
                    line = line.strip()
                    if line.startswith('file://'):
                        parsed = urllib.parse.urlparse(line)
                        p = urllib.parse.unquote(parsed.path)
                        if os.path.exists(p):
                            paths.append(p)
                    elif os.path.exists(line):
                        paths.append(line)

        if paths:
            for p in paths:
                self.feed_file(p)
            return True
        return False

    def capture_screen_and_reveal(self, interactive=False, append_mode=False):
        portal = Xdp.Portal()
        flags = Xdp.ScreenshotFlags.INTERACTIVE if interactive else Xdp.ScreenshotFlags.NONE

        def on_done(source, result, data):
            shot_path = None
            try:
                uri = portal.take_screenshot_finish(result)
                if uri:
                    parsed = urllib.parse.urlparse(uri)
                    p = urllib.parse.unquote(parsed.path)
                    if os.path.exists(p):
                        shot_path = p
            except Exception as e:
                log(f"Portal take_screenshot error: {e}")

            if shot_path:
                self.process_screenshot(shot_path, is_snip=interactive, append_mode=append_mode)
            else:
                self.status_pill.set_text("⚠️ Cancelled")
                if not self.captured_pages:
                    self.text_buffer.set_text("⚠️ Capture was not completed. Click '✂️ + Snip' or '🖥️ + Screen' above to try again!")

            # Completely restore window and focus entry
            self.set_visible(True)
            self.present()
            if self.saved_pos:
                surface = self.get_surface()
                if surface and hasattr(surface, 'get_xid'):
                    try:
                        xid = surface.get_xid()
                        X11Helper.get().move_window(xid, self.saved_pos[0], self.saved_pos[1])
                    except Exception:
                        pass
            else:
                self.position_at_top()

            self.trigger_drop_animation()
            self.set_focus(self.entry)
            GLib.idle_add(lambda: (self.entry.grab_focus(), GLib.SOURCE_REMOVE)[1])

            pass

        portal.take_screenshot(None, flags, None, on_done, None)
        return GLib.SOURCE_REMOVE

    def on_add_snip(self, _):
        if len(self.captured_pages) >= self.max_pages:
            return
        self.saved_pos = self.get_current_pos()
        # Completely unmap window from Wayland so zero pointer events or pixels are blocked
        self.set_visible(False)
        GLib.timeout_add(150, lambda: self.capture_screen_and_reveal(interactive=True, append_mode=True))

    def on_add_full(self, _):
        if len(self.captured_pages) >= self.max_pages:
            return
        self.status_pill.set_text("⏳ Seeing screen...")
        self.saved_pos = self.get_current_pos()
        self.set_visible(False)
        GLib.timeout_add(250, lambda: self.capture_screen_and_reveal(interactive=False, append_mode=True))

    def on_recapture(self, _):
        self.status_pill.set_text("⏳ Seeing screen...")
        self.saved_pos = self.get_current_pos()
        self.set_visible(False)
        GLib.timeout_add(250, lambda: self.capture_screen_and_reveal(interactive=False, append_mode=False))

    def on_clear_chat(self, _):
        self.conversation = []
        self.last_answer = ""
        count = len(self.captured_pages)
        self.text_buffer.set_text(f"👋 Chat history cleared. What would you like to ask about the {count} attached page(s)?")
        self.entry.set_text("")
        self.entry.grab_focus()

    def process_screenshot(self, shot_path, is_snip=False, append_mode=False):
        try:
            with Image.open(shot_path) as im:
                # 1. Preview thumbnail (compact for up to 5 cards)
                thumb = im.copy()
                thumb.thumbnail((60, 38), Image.Resampling.LANCZOS)
                thumb_bytes = io.BytesIO()
                thumb.save(thumb_bytes, format='PNG')
                gbytes = GLib.Bytes.new(thumb_bytes.getvalue())
                texture = Gdk.Texture.new_from_bytes(gbytes)

                # 2. Scale image for LLM payload (max 800px, JPEG 65)
                max_dim = self.config.get('max_image_dimension', 800)
                quality = self.config.get('image_quality', 65)
                w, h = im.size
                if max(w, h) > max_dim:
                    im.thumbnail((max_dim, max_dim), Image.Resampling.LANCZOS)

                rgb_im = im.convert('RGB')
                buf = io.BytesIO()
                rgb_im.save(buf, format='JPEG', quality=quality)
                b64 = base64.b64encode(buf.getvalue()).decode('utf-8')
                log(f"Processed {'snip' if is_snip else 'fullscreen'} ({im.size}), b64 size: {len(b64)}")

            try:
                os.remove(shot_path)
            except OSError:
                pass

            page_data = {
                "b64": b64,
                "texture": texture,
                "is_snip": is_snip
            }

            if append_mode and len(self.captured_pages) < self.max_pages:
                self.captured_pages.append(page_data)
                welcome = f"📸 Page {len(self.captured_pages)} attached! ({len(self.captured_pages)}/5 pages ready). Type your question or add another."
            else:
                self.captured_pages = [page_data]
                self.conversation = []
                welcome = "✂️ Page 1 snip captured! Click '✂️ + Snip' to attach up to 5 pages, or type your question." if is_snip else "👋 Page 1 screen seen! Click '✂️ + Snip' to attach up to 5 pages, or type your question."

            self.update_pages_tray_ui()
            self.text_buffer.set_text(welcome)
        except Exception as e:
            log(f"Error processing screenshot: {e}")
            self.status_pill.set_text("⚠️ No screen")

    def on_key_pressed(self, controller, keyval, keycode, state):
        lower_kv = Gdk.keyval_to_lower(keyval)
        if lower_kv == Gdk.KEY_Escape:
            self.collapse_to_notch()
            return True
        if state & Gdk.ModifierType.CONTROL_MASK:
            if lower_kv == Gdk.KEY_m:
                self.collapse_to_notch()
                return True
            elif lower_kv == Gdk.KEY_d:
                self.detach_to_desktop()
                return True
            elif lower_kv in (Gdk.KEY_q, Gdk.KEY_w):
                self.animate_and_close()
                return True
            elif lower_kv == Gdk.KEY_o:
                self.on_feed_file_clicked(None)
                return True
            elif lower_kv == Gdk.KEY_b:
                self.toggle_mode()
                return True
            elif lower_kv == Gdk.KEY_l:
                self.on_clear_chat(None)
                return True
        return False

    def on_quick_prompt(self, prompt_text):
        self.entry.set_text(prompt_text)
        self.on_submit(None)

    def scroll_to_end(self):
        mark = self.text_buffer.create_mark(None, self.text_buffer.get_end_iter(), False)
        self.text_view.scroll_to_mark(mark, 0.0, False, 0.0, 0.0)
        self.text_buffer.delete_mark(mark)

    def handle_direct_command(self, raw_text):
        cmd = raw_text.strip()
        if not cmd:
            return

        if len(self.conversation) == 0:
            self.text_buffer.set_text("")
        else:
            self.text_buffer.insert_with_tags_by_name(
                self.text_buffer.get_end_iter(),
                "\n────────────────────────────────────────\n\n",
                "divider"
            )

        self.text_buffer.insert_with_tags_by_name(self.text_buffer.get_end_iter(), "👤 You: ", "user_header")
        self.text_buffer.insert_with_tags_by_name(self.text_buffer.get_end_iter(), f"{cmd}\n\n", "bold")
        self.text_buffer.insert_with_tags_by_name(self.text_buffer.get_end_iter(), "✨ Lexi:\n", "lexi_header")

        # Check for /help
        if cmd.lower() in ('/help', 'help'):
            help_text = (
                "🌟 **Lexi System Access & Action Shortcuts**\n\n"
                "• `$ <command>` : Execute terminal command (`$ uname -a`, `$ df -h`, `$ git status`)\n"
                "• `/open <app|url|path>` : Launch app, website, or folder (`/open chrome`, `/open https://youtube.com`)\n"
                "• `/vol <0-100%|mute|+10%|-10%>` : Control system volume (`/vol 80%`, `/vol mute`)\n"
                "• `/bright <0-100%|+10%|-10%>` : Control screen brightness (`/bright 60%`)\n"
                "• `/media <play-pause|next|prev>` : Control music/media player (`/media next`)\n"
                "• `/wa <contact|phone> <msg>` : Send WhatsApp message (`/wa Mom Hello!`, `/wa +1234567890 Meeting now`)\n"
                "• `/lock` : Lock desktop session\n"
                "• `/snip` : Interactive screen region snip\n"
                "• `/screen` : Full screen capture\n"
                "• `/clear` : Clear chat history\n\n"
                "💡 **Natural Language works seamlessly too!** Just ask Lexi directly:\n"
                "• *\"Can you open Chrome and send hi to Mom on WhatsApp?\"*\n"
                "• *\"Turn volume up to 80% and play music\"*\n"
                "• *\"What is using the most disk space on my system?\"*\n"
            )
            render_markdown_to_buffer(self.text_buffer, help_text)
            self.scroll_to_end()
            self.update_mascot_mood("happy")
            return

        # Check for /clear
        if cmd.lower() in ('/clear', 'clear'):
            self.on_clear_chat(None)
            return

        # Check for /snip
        if cmd.lower() in ('/snip', 'snip'):
            self.on_snip(None)
            return

        # Check for /screen
        if cmd.lower() in ('/screen', 'screen'):
            self.on_fullscreen_snap(None)
            return

        # Check for /qr (Android Phone Scan)
        if cmd.lower() in ('/qr', 'qr'):
            qr_win = LexiQrPopoverWindow()
            qr_win.present()
            self.update_mascot_mood("happy")
            return

        # Check for /beam or /share
        if cmd.lower().startswith(('/beam', '/share')):
            parts = cmd.split(maxsplit=1)
            target_path = parts[1].strip() if len(parts) > 1 else None
            if not target_path and self.captured_pages:
                target_path = self.captured_pages[-1].get("path")

            if lexi_drop:
                server = lexi_drop.get_lexi_drop_instance()
                server_url = server.get_server_url()
                peers = server.get_all_peers()
                peer_desc = f"Connected ({len(peers)}): " + ", ".join([f"**{p['device']}** ({p['ip']})" for p in peers]) if peers else "No peers discovered yet"

                if target_path and os.path.exists(target_path):
                    try:
                        info = None
                        try:
                            import urllib.request
                            req = urllib.request.Request(
                                'http://127.0.0.1:7890/api/share',
                                data=json.dumps({'filepath': os.path.abspath(target_path)}).encode('utf-8'),
                                headers={'Content-Type': 'application/json'},
                                method='POST'
                            )
                            with urllib.request.urlopen(req, timeout=1.5) as resp:
                                info = json.loads(resp.read().decode('utf-8'))
                        except Exception:
                            info = server.share_file(target_path)
                        sz_str = converter.format_size(info['size']) if hasattr(converter, 'format_size') else f"{info['size']}B"
                        beam_resp = (
                            f"⚡ **Lexi Beam — Zero-Copy Share**\n\n"
                            f"• **File**: `{info['name']}` ({sz_str})\n"
                            f"• **Direct Download**: [{info['url']}]({info['url']})\n"
                            f"• **LAN Portal**: [{server_url}]({server_url})\n"
                            f"• **Peers**: {peer_desc}\n\n"
                            f"*Streamed over local Wi-Fi with kernel zero-copy os.sendfile at wire speed.*"
                        )
                    except Exception as e:
                        beam_resp = f"⚠️ Failed to beam `{target_path}`: {e}"
                else:
                    beam_resp = (
                        f"⚡ **Lexi Beam — High-Speed Local LAN File Sharing**\n\n"
                        f"• **Web & PWA Receiver**: [{server_url}]({server_url})\n"
                        f"• **Peers**: {peer_desc}\n"
                        f"• **Usage**: `/beam <path/to/file.zip>` or drag any file onto Dynamic Island\n"
                        f"• **Windows Support**: Open link in Edge to install 1-click desktop app"
                    )
            else:
                beam_resp = "⚠️ Lexi Drop server is not running."
            render_markdown_to_buffer(self.text_buffer, beam_resp)
            self.scroll_to_end()
            self.update_mascot_mood("happy")
            return

        # Parse action from direct shortcut
        action = None
        if cmd.startswith('$'):
            bash_cmd = cmd[1:].strip()
            action = {'type': 'command', 'attrs': {}, 'body': bash_cmd}
        elif cmd.startswith('/open'):
            target = cmd[5:].strip()
            action = {'type': 'open', 'attrs': {}, 'body': target}
        elif cmd.startswith('/vol'):
            level = cmd[4:].strip()
            action = {'type': 'volume', 'attrs': {'level': level}, 'body': level}
        elif cmd.startswith('/bright'):
            level = cmd[7:].strip()
            action = {'type': 'brightness', 'attrs': {'level': level}, 'body': level}
        elif cmd.startswith('/media'):
            m_cmd = cmd[6:].strip() or 'play-pause'
            action = {'type': 'media', 'attrs': {'command': m_cmd}, 'body': m_cmd}
        elif cmd.startswith('/wa') or cmd.startswith('/whatsapp'):
            parts = cmd.split(maxsplit=2)
            target = parts[1] if len(parts) > 1 else ''
            msg = parts[2] if len(parts) > 2 else ''
            action = {'type': 'whatsapp', 'attrs': {'contact': target, 'message': msg}, 'body': msg}
        elif cmd.startswith('/lock'):
            action = {'type': 'lock', 'attrs': {}, 'body': ''}

        if action:
            self.update_mascot_mood("working")
            def worker():
                res = SystemActionEngine.get().execute_action(action)
                def on_done():
                    render_action_result_to_buffer(self.text_buffer, action, res)
                    self.scroll_to_end()
                    self.update_mascot_mood("happy" if res.get('success', True) else "alert")
                    self.status_pill.set_text("⚡ Action completed ✨")
                    def reset_status():
                        count = len(self.captured_pages)
                        st = f"✨ {count}/{self.max_pages} Pages" if count > 0 else "⚡ System Access Active"
                        self.status_pill.set_text(st)
                        return GLib.SOURCE_REMOVE
                    GLib.timeout_add(2500, reset_status)
                GLib.idle_add(on_done)
            threading.Thread(target=worker, daemon=True).start()

    def on_submit(self, _):
        text = self.entry.get_text().strip()
        if not text or self.is_streaming:
            return

        # Direct system commands check
        if text.startswith('$') or text.startswith('/'):
            self.entry.set_text("")
            self.handle_direct_command(text)
            return

        self.is_streaming = True
        self.send_btn.set_sensitive(False)
        self.update_mascot_mood("working")
        pages_count = len(self.captured_pages)
        if pages_count > 0:
            self.thinking_label.set_text(f"✨ Lexi is analyzing {pages_count} page(s)... ✦ ✧ ✧")
        else:
            self.thinking_label.set_text("✨ Lexi exploring & executing... ✦ ✧ ✧")
        self.thinking_box.set_visible(True)
        self.spinner.start()
        self.query_start_time = time.time()

        if self.timer_source_id:
            GLib.source_remove(self.timer_source_id)
        self.timer_source_id = GLib.timeout_add(450, self.update_thinking_timer)

        # Multi-turn formatting in text buffer
        if len(self.conversation) == 0:
            self.text_buffer.set_text("")
        else:
            self.text_buffer.insert_with_tags_by_name(
                self.text_buffer.get_end_iter(),
                "\n────────────────────────────────────────\n\n",
                "divider"
            )

        self.text_buffer.insert_with_tags_by_name(self.text_buffer.get_end_iter(), "👤 You: ", "user_header")
        self.text_buffer.insert_with_tags_by_name(self.text_buffer.get_end_iter(), f"{text}\n\n", "bold")
        self.text_buffer.insert_with_tags_by_name(self.text_buffer.get_end_iter(), "✨ Lexi:\n", "lexi_header")

        if self.turn_start_mark:
            self.text_buffer.delete_mark(self.turn_start_mark)
        self.turn_start_mark = self.text_buffer.create_mark("turn_start", self.text_buffer.get_end_iter(), True)

        self.scroll_to_end()
        self.entry.set_text("")

        # Run query in background thread
        thread = threading.Thread(target=self.run_query, args=(text,))
        thread.daemon = True
        thread.start()

    def update_thinking_timer(self):
        if not self.is_streaming:
            return GLib.SOURCE_REMOVE
        elapsed = time.time() - self.query_start_time
        pages_count = len(self.captured_pages)

        # Animated stardust sparkle dots
        dot_cycles = ["✦ ✧ ✧", "✧ ✦ ✧", "✧ ✧ ✦", "✦ ✦ ✦"]
        step = int(elapsed * 2.2)
        dots = dot_cycles[step % len(dot_cycles)]

        if pages_count > 0:
            self.thinking_label.set_text(f"✨ Lexi exploring {pages_count} page(s)... {dots} ({int(elapsed)}s)")
        else:
            self.thinking_label.set_text(f"✨ Lexi working... {dots} ({int(elapsed)}s)")
        return GLib.SOURCE_CONTINUE

    def get_payload_messages(self, current_user_question):
        msgs = []
        if self.config.get('system_prompt'):
            msgs.append({"role": "system", "content": self.config.get('system_prompt')})

        # Keep history bounded (turn 0 + last 14 messages)
        conv = list(self.conversation)
        if len(conv) > 16:
            conv = conv[:2] + conv[-14:]
        msgs.extend(conv)

        # Turn 0 includes all attached pages and fed documents
        if not self.conversation:
            if self.captured_pages:
                content = [{"type": "text", "text": current_user_question}]
                num_pages = len(self.captured_pages)
                for idx, page in enumerate(self.captured_pages, 1):
                    if page.get("text_content"):
                        content.append({
                            "type": "text",
                            "text": f"--- [Attached Document {idx}: {page.get('name', 'file')}] ---\n{page['text_content']}\n--- [End of Document] ---"
                        })
                    if page.get("b64"):
                        if num_pages > 1 and not page.get("text_content"):
                            content.append({
                                "type": "text",
                                "text": f"--- [Page {idx} of {num_pages}] ---"
                            })
                        content.append({
                            "type": "image_url",
                            "image_url": {"url": f"data:image/jpeg;base64,{page['b64']}"}
                        })
                user_turn = {
                    "role": "user",
                    "content": content
                }
            else:
                user_turn = {
                    "role": "user",
                    "content": current_user_question
                }
        else:
            user_turn = {
                "role": "user",
                "content": current_user_question
            }
        msgs.append(user_turn)
        return msgs, user_turn

    def run_query(self, user_question):
        full_response = []
        user_turn = None
        try:
            timeout = self.config.get('request_timeout', 35)
            messages, user_turn = self.get_payload_messages(user_question)
            max_tokens = self.config.get('max_tokens', 2048)

            has_images = any(
                isinstance(m.get('content'), list) and any(item.get('type') == 'image_url' for item in m.get('content', []))
                for m in messages
            )

            success = False
            deepseek_key = self.config.get('deepseek_api_key', '').strip()
            deepseek_base = self.config.get('deepseek_api_base', 'https://api.deepseek.com').rstrip('/')
            deepseek_model = self.config.get('deepseek_model', 'deepseek-chat')

            # 1. Primary: Direct DeepSeek API (if key is set and not a vision image query)
            if deepseek_key and not has_images:
                try:
                    ds_payload = {
                        "model": deepseek_model,
                        "messages": messages,
                        "stream": True,
                        "max_tokens": max_tokens
                    }
                    ds_headers = {
                        "Content-Type": "application/json",
                        "Authorization": f"Bearer {deepseek_key}"
                    }
                    req = urllib.request.Request(f"{deepseek_base}/chat/completions", data=json.dumps(ds_payload).encode('utf-8'), headers=ds_headers)
                    with DIRECT_OPENER.open(req, timeout=timeout) as resp:
                        GLib.idle_add(self.on_query_started)
                        GLib.idle_add(self.status_pill.set_text, f"● {deepseek_model} (direct)")
                        for line in resp:
                            line = line.decode('utf-8').strip()
                            if line.startswith('data: ') and line != 'data: [DONE]':
                                try:
                                    chunk = json.loads(line[6:])
                                    delta = chunk['choices'][0]['delta'].get('content', '')
                                    if delta:
                                        full_response.append(delta)
                                        GLib.idle_add(self.append_stream_token, delta)
                                except Exception:
                                    pass
                        success = True
                except Exception as e_ds:
                    log(f"Direct DeepSeek call failed: {e_ds}. Falling back to OpenRouter...")

            # 2. Fallback / Primary (OpenRouter)
            if not success:
                api_base = self.config.get('api_base', DEFAULT_CONFIG['api_base']).rstrip('/')
                api_key = self.config.get('api_key', '').strip()
                if not api_key:
                    err_msg = (
                        "🔑 **No API Key Configured**\n\n"
                        "To chat with Lexi, please provide an API key:\n"
                        "• Edit `~/.local/share/lexi-copilot/config.json` and set `\"api_key\": \"sk-or-v1-...\"`\n"
                        "• Or export in your terminal profile: `export OPENROUTER_API_KEY=\"sk-or-v1-...\"`\n\n"
                        "*(Get an API key at [openrouter.ai](https://openrouter.ai) or [deepseek.com](https://deepseek.com))*"
                    )
                    full_response.append(err_msg)
                    GLib.idle_add(self.on_query_started)
                    GLib.idle_add(self.append_stream_token, err_msg)
                    return
                models_list = self.config.get('models')
                model = self.config.get('model', 'deepseek/deepseek-v4.1-flash')

                payload = {
                    "messages": messages,
                    "stream": True,
                    "max_tokens": max_tokens,
                    "route": "fallback"
                }
                if models_list and isinstance(models_list, list):
                    payload["models"] = models_list
                else:
                    payload["model"] = model

                url = f"{api_base}/chat/completions"
                headers = {
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {api_key}",
                    "HTTP-Referer": "https://lexi.desktop",
                    "X-Title": "Lexi Screen Copilot"
                }

                req = urllib.request.Request(url, data=json.dumps(payload).encode('utf-8'), headers=headers)
                with DIRECT_OPENER.open(req, timeout=timeout) as resp:
                    GLib.idle_add(self.on_query_started)
                    for line in resp:
                        line = line.decode('utf-8').strip()
                        if line.startswith('data: ') and line != 'data: [DONE]':
                            try:
                                chunk = json.loads(line[6:])
                                served_model = chunk.get('model')
                                if served_model:
                                    short_name = served_model.split('/')[-1]
                                    GLib.idle_add(self.status_pill.set_text, f"● {short_name}")
                                delta = chunk['choices'][0]['delta'].get('content', '')
                                if delta:
                                    full_response.append(delta)
                                    GLib.idle_add(self.append_stream_token, delta)
                            except Exception:
                                pass
        except Exception as e:
            log(f"run_query error: {e}")
            err_msg = f"\n⚠️ Error connecting: {e}\n(Tip: Press Enter to retry or check your connection)"
            full_response.append(err_msg)
            GLib.idle_add(self.append_stream_token, err_msg)
        finally:
            full_text = "".join(full_response)
            # Execute any actions detected in LLM response in this background worker thread!
            actions = SystemActionEngine.get().parse_actions(full_text)
            action_results = []
            if actions:
                action_results = SystemActionEngine.get().execute_action_sequence(actions, user_question=user_question)
            GLib.idle_add(self.on_query_finished, full_text, user_turn, action_results)

    def on_query_started(self):
        if self.timer_source_id:
            GLib.source_remove(self.timer_source_id)
            self.timer_source_id = None
        self.spinner.stop()
        self.thinking_box.set_visible(False)

    def append_stream_token(self, delta):
        end_iter = self.text_buffer.get_end_iter()
        self.text_buffer.insert(end_iter, delta)
        self.scroll_to_end()

    def on_query_finished(self, full_text, user_turn, action_results=None):
        if self.timer_source_id:
            GLib.source_remove(self.timer_source_id)
            self.timer_source_id = None
        self.spinner.stop()
        self.thinking_box.set_visible(False)
        self.is_streaming = False
        self.send_btn.set_sensitive(True)

        if full_text and self.turn_start_mark:
            # Replace raw stream with beautiful formatted markdown
            start_iter = self.text_buffer.get_iter_at_mark(self.turn_start_mark)
            end_iter = self.text_buffer.get_end_iter()
            self.text_buffer.delete(start_iter, end_iter)

            clean_text = SystemActionEngine.get().strip_actions(full_text)
            if clean_text:
                render_markdown_to_buffer(self.text_buffer, clean_text)

            if action_results:
                for act, res in action_results:
                    render_action_result_to_buffer(self.text_buffer, act, res)

            self.text_buffer.delete_mark(self.turn_start_mark)
            self.turn_start_mark = None

            if user_turn and not full_text.startswith("\n⚠️ Error"):
                self.conversation.append(user_turn)
                self.conversation.append({"role": "assistant", "content": full_text})
                self.last_answer = clean_text or full_text

        self.scroll_to_end()
        self.entry.grab_focus()

        # Celebratory mascot reaction when answer completes
        if full_text and not full_text.startswith("\n⚠️ Error"):
            self.update_mascot_mood("happy")
            if action_results:
                self.status_pill.set_text("⚡ Actions executed! ✨")
            else:
                self.status_pill.set_text("🌟 Cosmic answer ready! ✨")
            def reset_celebration():
                self.update_mascot_mood("happy")
                if not self.is_streaming:
                    count = len(self.captured_pages)
                    st = f"✨ {count}/{self.max_pages} Pages" if count > 0 else "⚡ System Access Active"
                    self.status_pill.set_text(st)
                    if hasattr(self, 'header_status_pill'):
                        self.header_status_pill.set_text(st)
                return GLib.SOURCE_REMOVE
            GLib.timeout_add(2500, reset_celebration)

    def on_copy(self, _):
        text_to_copy = self.last_answer
        if not text_to_copy:
            start = self.text_buffer.get_start_iter()
            end = self.text_buffer.get_end_iter()
            text_to_copy = self.text_buffer.get_text(start, end, True)

        if text_to_copy:
            display = Gdk.Display.get_default()
            clipboard = display.get_clipboard()
            clipboard.set(text_to_copy)
            self.copy_btn.set_label("Copied! ✨🚀")
            self.update_mascot_mood("happy")
            def reset_copy():
                self.copy_btn.set_label("📋 Copy Answer")
                if not self.is_streaming:
                    self.update_mascot_mood("happy")
                return GLib.SOURCE_REMOVE
            GLib.timeout_add_seconds(2, reset_copy)


class LexiApp(Adw.Application):
    def __init__(self, config):
        super().__init__(
            application_id='org.antigravity.LexiCompanion',
            flags=gi.repository.Gio.ApplicationFlags.HANDLES_COMMAND_LINE
        )
        self.config = config
        self.active_window = None

    def do_startup(self):
        Adw.Application.do_startup(self)
        self.hold()  # Keep primary companion daemon permanently alive
        self.load_css()

    def load_css(self):
        if os.path.exists(STYLES_FILE):
            provider = Gtk.CssProvider()
            provider.load_from_path(STYLES_FILE)
            display = Gdk.Display.get_default()
            if display:
                Gtk.StyleContext.add_provider_for_display(
                    display,
                    provider,
                    Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
                )

    def do_activate(self):
        if self.active_window and hasattr(self.active_window, 'captured_pages'):
            win = self.active_window
            if win.get_visible():
                win.collapse_to_notch()
            else:
                win.expand_from_notch()
        else:
            win = LexiWindow(self, self.config, is_snip=False)
            self.active_window = win
            win.set_visible(False)
            GLib.timeout_add(150, lambda: win.capture_screen_and_reveal(interactive=False, append_mode=False))

    def do_command_line(self, command_line):
        def finish_cmd(code=0):
            try:
                if hasattr(command_line, 'set_exit_status'):
                    command_line.set_exit_status(code)
                if hasattr(command_line, 'done'):
                    command_line.done()
            except Exception:
                pass
            return code

        args = command_line.get_arguments()
        if '--help' in args or '-h' in args:
            print("Lexi Copilot - Cosmic Screen Companion & Filee Converter\nUsage:\n  lexi-copilot [options]\n\nOptions:\n  -s, --snip                 Interactive region snip\n  -i, --island, -n, --notch  Dock as Lexi Dynamic Island at top of screen\n  -b, --sidepanel, --sidebar Dock as vertical Side Panel\n  -p, --pet                  Launch as floating Desktop Pet\n  -d, --donut [file]         Open circular Filee Donut converter at cursor\n  -f, --feed <file>          Feed a file to Lexi Copilot\n  --convert <file> --to <fmt> Convert file offline\n  -h, --help                 Show this help message")
            return finish_cmd(0)

        if '--convert' in args:
            try:
                idx = args.index('--convert')
                if idx < len(args) - 1:
                    in_f = args[idx + 1]
                    to_fmt = "webp"
                    if '--to' in args:
                        to_idx = args.index('--to')
                        if to_idx < len(args) - 1:
                            to_fmt = args[to_idx + 1]
                    res = converter.convert_file(in_f, to_fmt)
                    if res.get('success'):
                        print(f"✨ Converted: {res['output_path']} ({res.get('summary', '')})")
                        return finish_cmd(0)
                    else:
                        print(f"❌ Failed: {res.get('error')}")
                        return finish_cmd(1)
            except Exception as e:
                print(f"❌ Error: {e}")
                return finish_cmd(1)

        is_donut = ('--donut' in args) or ('-d' in args)
        if is_donut:
            target_f = None
            for i, a in enumerate(args[1:], 1):
                if a in ('--donut', '-d') and i < len(args) - 1 and not args[i + 1].startswith('-'):
                    target_f = args[i + 1]
                    break
                elif not a.startswith('-') and os.path.exists(a):
                    target_f = a
                    break
            donut = LexiRadialDonutWindow(file_path=target_f)
            donut.present()
            return finish_cmd(0)

        is_snip = ('--snip' in args) or ('-s' in args)
        is_notch = ('--notch' in args) or ('-n' in args) or ('--island' in args) or ('-i' in args)
        is_pet = ('--pet' in args) or ('-p' in args)
        is_sidepanel = ('--sidepanel' in args) or ('--sidebar' in args) or ('-b' in args)
        is_card = ('--card' in args) or ('-c' in args)

        feed_file = None
        for i, a in enumerate(args[1:], 1):
            if a in ('--feed', '-f') and i < len(args) - 1:
                feed_file = args[i + 1]
                break
            elif not a.startswith('-') and os.path.exists(a):
                feed_file = a
                break

        if feed_file:
            if self.active_window and hasattr(self.active_window, 'feed_file'):
                self.active_window.feed_file(feed_file)
                return finish_cmd(0)

        if self.active_window and hasattr(self.active_window, 'captured_pages'):
            win = self.active_window
            if is_notch:
                win.collapse_to_notch()
            elif is_pet:
                win.detach_to_desktop()
            elif is_sidepanel:
                win.set_mode('side_panel')
                win.expand_from_notch()
            elif is_card:
                win.set_mode('card')
                win.expand_from_notch()
            elif is_snip:
                if len(win.captured_pages) < win.max_pages:
                    win.saved_pos = win.get_current_pos()
                    win.set_visible(False)
                    if hasattr(win, 'revealer'):
                        win.revealer.set_reveal_child(False)
                    GLib.timeout_add(150, lambda: win.capture_screen_and_reveal(interactive=True, append_mode=True))
                else:
                    win.set_visible(True)
                    win.present()
            else:
                if win.get_visible():
                    win.collapse_to_notch()
                else:
                    win.set_mode('card')
                    win.expand_from_notch()
        else:
            win = LexiWindow(self, self.config, is_snip=is_snip)
            self.active_window = win
            win.set_visible(False)
            if is_sidepanel:
                win.set_mode('side_panel')
                GLib.timeout_add(150, lambda: win.capture_screen_and_reveal(interactive=is_snip, append_mode=False))
            elif is_notch:
                nw = win.get_notch_window()
                nw.set_visible(True)
                nw.present()
                GLib.timeout_add(50, nw.position_at_top)
            elif is_pet:
                pw = win.get_pet_window()
                pw.set_visible(True)
                pw.present()
                GLib.timeout_add(50, pw.position_on_desktop)
            else:
                GLib.timeout_add(150, lambda: win.capture_screen_and_reveal(interactive=is_snip, append_mode=False))

        return finish_cmd(0)


def main():
    log("=== Lexi invoked ===")
    config = load_config()
    app = LexiApp(config)
    return app.run(sys.argv)

if __name__ == '__main__':
    sys.exit(main())



