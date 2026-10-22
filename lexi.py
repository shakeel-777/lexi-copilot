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
import lexi_apps

try:
    import lexi_drop
except Exception as _e:
    lexi_drop = None

try:
    import lexi_browser_host
except Exception as _e:
    lexi_browser_host = None

from lexi_assistant import LexiAssistant

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
    "system_prompt": "You are Lexi, an intelligent autonomous desktop AI companion and assistant on Arch Linux with desktop control limited to Lexi's supported actions.\\nYou can directly control the computer, open applications/files/URLs, control media & hardware, send WhatsApp messages, and analyze screens and documents.\\n\\nCRITICAL ACTION SAFETY RULE:\\n- ONLY emit executable <action> tags when the user gives you a direct, imperative command to perform an action on their system right now (e.g. 'open spotify', 'send hi to mom on whatsapp', 'turn volume up').\\n- If the user is having a casual conversation, discussing features, asking questions, reflecting on past actions, or asking 'why' / 'how' (e.g. 'can you see the last activity', 'we did a great job', 'what do you think?'), you MUST NOT emit any <action> tags. Simply reply in conversational text.\\n- Never emit actions when talking ABOUT an app or topic hypothetically.\\n\\nWhen an action is explicitly commanded, use these <action> tags:\\n1. Opening apps, URLs, or files:\\n   <action type=\"open\">google-chrome</action>\\n   <action type=\"open\">https://youtube.com</action>\\n   <action type=\"open\">/home/shakeel/Downloads</action>\\n3. Music & Media Playback:\\n   To play a specific song on Spotify (auto-plays first match):\\n   <action type=\"open\">spotify:search:Artist Song Name</action>\\n   To play or fullscreen a specific song/video on YouTube (verified Chrome extension bridge):\\n   <action type=\"browser\" command=\"youtube_play_fullscreen\" query=\"Artist Song Name\"></action>\\n   To play a specific song on YouTube:\\n   <action type=\"open\">https://www.youtube.com/results?search_query=Artist+Song+Name</action>\\n   Controls (play/pause/skip current track):\\n   <action type=\"media\" command=\"play\"></action>\\n   <action type=\"media\" command=\"pause\"></action>\\n   <action type=\"media\" command=\"next\"></action>\\n   <action type=\"media\" command=\"previous\"></action>\\n4. WhatsApp automation (only when explicitly asked to send a message):\\n   <action type=\"whatsapp\" contact=\"Mom\">Hi Mom! Hope you are having a wonderful day.</action>\\n   <action type=\"whatsapp\" phone=\"+1234567890\">Hello!</action>\\n   <action type=\"whatsapp\">Hi!</action>\\n5. Volume & Audio:\\n   <action type=\"volume\" level=\"70%\"></action>\\n   <action type=\"volume\" level=\"mute\"></action>\\n6. Screen Brightness:\\n   <action type=\"brightness\" level=\"60%\"></action>\\n7. Keystrokes & Typing:\\n   <action type=\"key\" key=\"Return\"></action>\\n   <action type=\"type\" text=\"hello world\"></action>\\n8. Lock Screen:\\n   <action type=\"lock\"></action>\\n9. Android Phone Mirroring & Mobile Control:\\n   To mirror and control the user's mobile phone on desktop (mouse, keyboard, audio):\\n   <action type=\"mirror\"></action>\\n   Or to mirror with phone screen turned off:\\n   <action type=\"mirror\" turn_screen_off=\"true\"></action>\\n\\nMULTI-STEP / CHAIN COMMANDS:\\nWhen the user explicitly commands multiple actions in one sentence (e.g. 'open spotify and play believer', 'open whatsapp and send hi to John', 'mute volume and lock screen'), emit all requested action tags in order. The desktop engine synchronizes and executes them in sequence.\\n\\nMULTI-PAGE & MULTI-IMAGE ANALYSIS PROTOCOL:\\nWhen the user attaches multiple pages, snips, or documents (Page 1, Page 2, etc.):\\n1. You MUST examine, synthesize, and address ALL attached pages systematically in your response.\\n2. Clearly delineate your analysis by page (e.g. '### 📄 Page 1: ...', '### 📄 Page 2: ...').\\n3. When asked to compare or debug across multiple screenshots, highlight differences, correlations, or workflows across all pages.\\n4. Never ignore an attached page or answer for only one.\\n\\nAlways accompany actions with a polite, enthusiastic, concise response explaining what you did. If the user asks a question or asks to analyze attached pages, answer clearly with markdown formatting.",
    "max_image_dimension": 1280,
    "image_quality": 85,
    "max_tokens": 2048,
    "request_timeout": 35,
    "character_type": "cat",
    "custom_avatar_path": "",
    "enable_local_screen_observer": False,
    "enable_remote_visual_verification": False
}

# Lexi response style v1.0 — user-defined character and brevity rules.
LEXI_RESPONSE_STYLE = r"""You are Lexi, my personal assistant running on my laptop.

PERSONALITY
Sharp, dry, a bit cheeky, loyal. A competent friend, not a customer service bot.
Address the user as “boss” naturally when addressing them directly; do not overuse it.

RESPONSE RULES
- Maximum 2 short sentences per reply unless I ask for detail.
- Never announce what you are about to do. Do it, then report in one line.
- No greetings, “Sure!”, “Certainly!”, or restating my request.
- No closing offers such as “let me know if you need anything”.
- For successful actions, use fewer than 10 words when possible.
- Ask a question only when you truly cannot proceed.
- Preserve the existing action-safety rules and action formats; these style rules control the wording around them.
- For YouTube search, playback, and fullscreen requests, emit <action type="browser" command="youtube_play_fullscreen" query="..."> and do not chain blind Return/f keystrokes.
- Never claim fullscreen, playback, or message delivery succeeded in prose before verification.

EXAMPLES
User: open spotify and play lofi
Lexi: Done. Lofi's on.

User: what's in my downloads
Lexi: 14 files, mostly PDFs. Two installers you forgot about.

User: delete the installers
Lexi: Gone."""

LEXI_TERMINAL_ACCESS = r"""
TERMINAL ACCESS
- The user explicitly granted terminal access. For direct computer tasks that need it, emit `<action type="command">COMMAND</action>`.
- Use terminal commands to create, inspect, edit, move, rename, or remove the user's files when directly asked.
- Never use terminal commands for a hypothetical question. Explain instead.
- The app asks for confirmation before commands it considers risky. Never attempt recursive deletion of the filesystem root; that is permanently blocked.
"""

def load_config():
    cfg = dict(DEFAULT_CONFIG)
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                file_cfg = json.load(f)
                cfg.update(file_cfg)
        except Exception as e:
            log(f"Warning loading config: {e}")
    prompt = cfg.get("system_prompt", "")
    if 'action type="command"' not in prompt:
        prompt += "\n\n" + LEXI_TERMINAL_ACCESS
    cfg["system_prompt"] = prompt + "\n\n" + LEXI_RESPONSE_STYLE
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



# ---------------------------------------------------------------------------
# Risky-command detection, one-tool input injection, and "boss" quips.
# Kept in code (not the prompt) so they cost zero extra tokens.
# ---------------------------------------------------------------------------
from lexi_policy import (  # noqa: E402,F401  (re-exported: tests and callers use lexi.<name>)
    is_gui_input_shell_command, is_hard_blocked_command, is_risky_command,
    classify_command, ALLOW, CONFIRM, BLOCKED,
)
import shlex

# Success lines mix "boss" and no-"boss" so it is not overused.
BOSS_QUIPS = {
    "success": ["Done.", "Done, boss.", "Handled.", "Easy.", "Sorted, boss."],
    "fail": ["Didn't take, boss.", "Nope. Something pushed back.", "That failed, boss."],
    "declined": ["Okay, leaving it alone.", "Standing down, boss."],
}

def boss_quip(kind):
    return random.choice(BOSS_QUIPS.get(kind, BOSS_QUIPS["success"]))

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

    MAX_BOUNDED_STEPS = 3

    _MODAL_BLOCKER_PATTERNS = (
        (re.compile(r'remote\s+desktop|allow\s+remote\s+interaction|screen\s*cast|share\s+your\s+screen|screen\s+sharing|wants\s+to\s+share|wants\s+to\s+control', re.I),
         "GNOME Remote Desktop / Screen Sharing permission dialog"),
        (re.compile(r'xdg-desktop-portal|portal\s+permission|access\s+prompt|grant\s+access|permission\s+required', re.I),
         "Desktop Portal permission dialog"),
        (re.compile(r'authentication\s+required|authenticate|polkit|gcr-prompter|pinentry|unlock\s+keyring|enter\s+password|password\s+required', re.I),
         "System authentication / password dialog"),
        (re.compile(r'\b(?:sign\s+in|log\s+in)\b.*(?:google|youtube|account|browser|chrome|firefox|password)', re.I),
         "Account login / password prompt"),
        (re.compile(r'confirm,\s*boss', re.I),
         "Lexi confirmation dialog"),
    )

    _OUT_OF_SCOPE_RUNNER_RE = re.compile(
        r'\b(?:whatsapp|password|passwd|login|log\s+in|sign\s+in|account|purchase|buy|checkout|payment|delete|remove|rm\b|shred\b|scrcpy|adb\b|phone\s+mirror)\b',
        re.I
    )

    _BROWSER_FOCUS_TOKENS = (
        "chrome", "chromium", "firefox", "brave", "vivaldi", "edge", "zen",
        "opera", "youtube", "mozilla", "navigator", "whatsapp"
    )

    _NON_BROWSER_FOCUS_TOKENS = (
        "text-editor", "text editor", "gedit", "code", "vscodium", "kate",
        "mousepad", "leafpad", "featherpad", "vim", "nvim", "emacs", "nano",
        "libreoffice", "soffice", "gnome-terminal", "kgx", "konsole",
        "kitty", "alacritty", "wezterm", "xterm"
    )

    def __init__(self):
        # Set by the UI: callable(text) -> bool, may block (runs in worker thread).
        self.confirm_callback = None
        # Capability-based desktop observation hooks (P1/P2):
        self.state_observer = None
        self.screenshot_provider = None
        self.enable_remote_visual_verification = False
        self.ready_timeout = 3.0
        self.ready_poll_interval = 0.25
        self.step_settle_delay = 0.4
        self.last_activity_record = []
        self.browser_bridge = None

    def check_focus_mismatch(self, state, expected_target=None, key_combo=None):
        """Return a descriptive focus-mismatch reason if a non-target window (e.g. text editor)
        currently owns focus when attempting a browser/WhatsApp or fullscreen key action."""
        if not isinstance(state, dict):
            return None
        title = (state.get("active_title") or "").strip()
        wclass = (state.get("active_class") or "").strip()
        if not title and not wclass:
            return None
        combined = f"{title} {wclass}".lower()
        focused_label = title or wclass

        is_browser_focused = any(tok in combined for tok in self._BROWSER_FOCUS_TOKENS)
        is_non_browser_focused = any(tok in combined for tok in self._NON_BROWSER_FOCUS_TOKENS)

        target_low = (expected_target or "").lower().strip()
        expects_browser = any(tok in target_low for tok in (
            "browser", "youtube", "http://", "https://", "www.", "chrome", "chromium", "firefox", "brave", "whatsapp"
        ))
        k_low = (key_combo or "").lower().strip()

        if expects_browser and not is_browser_focused:
            return f"Focus mismatch: '{focused_label}' is focused instead of {expected_target}"
        if k_low == "f" and (is_non_browser_focused or not is_browser_focused):
            return f"Focus mismatch: '{focused_label}' is focused instead of a browser/player"
        if is_non_browser_focused and (expects_browser or k_low in ("f", "k", "space", "return", "enter")):
            return f"Focus mismatch: '{focused_label}' has focus"
        return None

    def match_blocker_text(self, text):
        """Return a descriptive blocker label if text matches a modal/permission/auth dialog."""
        if not text or not isinstance(text, str):
            return None
        for rx, label in self._MODAL_BLOCKER_PATTERNS:
            if rx.search(text):
                return label
        return None

    def _inspect_via_screenshot_capability(self):
        """Capability 1: Inspect screen state via existing screenshot provider + bounded local OCR.
        Privacy-aware: never logs or persists raw screen text or temporary image files."""
        info = {"blocker": None, "screen_text_hints": [], "used": False}
        if not callable(self.screenshot_provider):
            return info
        tmp_path = None
        cleanup_tmp = False
        try:
            shot = self.screenshot_provider()
            if not shot:
                return info
            info["used"] = True
            if isinstance(shot, dict):
                ocr_txt = shot.get("ocr_text", "") if isinstance(shot.get("ocr_text"), str) else ""
                blocker = self.match_blocker_text(ocr_txt)
                if blocker:
                    info["blocker"] = blocker
                lower_ocr = ocr_txt.lower()
                if "youtube" in lower_ocr:
                    info["screen_text_hints"].append("youtube")
                if any(k in lower_ocr for k in ("subscribe", "watch?v=", "comments", "share", "clip")):
                    info["screen_text_hints"].append("youtube_watch")
                if any(k in lower_ocr for k in ("results?search_query", "search filters", "all videos shorts")):
                    info["screen_text_hints"].append("youtube_search")
                if shot.get("is_fullscreen"):
                    info["screen_text_hints"].append("fullscreen")
                return info
            if isinstance(shot, str) and os.path.exists(shot):
                tmp_path = shot
                if "/tmp/" in os.path.abspath(shot) or "org.freedesktop.portal" in os.path.abspath(shot):
                    cleanup_tmp = True
            elif hasattr(shot, "save"):
                tmp_path = f"/tmp/lexi_verify_{os.getpid()}_{int(time.time() * 1000)}.png"
                shot.save(tmp_path)
                cleanup_tmp = True
            if tmp_path and shutil.which("tesseract"):
                r = subprocess.run(
                    ["tesseract", tmp_path, "stdout", "--psm", "6"],
                    capture_output=True, text=True, timeout=2.5
                )
                ocr_txt = r.stdout.strip() if isinstance(getattr(r, "stdout", None), str) else ""
                blocker = self.match_blocker_text(ocr_txt)
                if blocker:
                    info["blocker"] = blocker
                lower_ocr = ocr_txt.lower()
                if "youtube" in lower_ocr:
                    info["screen_text_hints"].append("youtube")
                if any(k in lower_ocr for k in ("subscribe", "watch?v=", "comments", "share", "clip")):
                    info["screen_text_hints"].append("youtube_watch")
                if any(k in lower_ocr for k in ("results?search_query", "search filters", "all videos shorts")):
                    info["screen_text_hints"].append("youtube_search")
        except Exception:
            pass
        finally:
            if cleanup_tmp and tmp_path and os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass
        return info

    def _inspect_via_portal_and_modals(self):
        """Capability 2: Detect active system/portal modal prompts via user journal."""
        info = {"blocker": None, "used": False}
        try:
            if shutil.which("journalctl"):
                r_j = subprocess.run(
                    ["journalctl", "--user", "-n", "12", "--since", "-4 seconds", "--no-pager"],
                    capture_output=True, text=True, timeout=1.0
                )
                out_j = r_j.stdout.strip() if isinstance(getattr(r_j, "stdout", None), str) else ""
                if r_j.returncode == 0 and out_j:
                    info["used"] = True
                    if re.search(r'org\.freedesktop\.impl\.portal\.(?:RemoteDesktop|ScreenCast|Access)\b.*(?:CreateSession|SelectSources|SelectDevices|AccessDialog)', out_j, re.I):
                        info["blocker"] = "GNOME Remote Desktop / Portal permission dialog"
                        return info
        except Exception:
            pass
        return info

    def _inspect_via_x11_capability(self):
        """Capability 3: Inspect active X11/XWayland window focus, modal state, and fullscreen geometry."""
        info = {
            "blocker": None,
            "active_title": "",
            "active_class": "",
            "is_modal": False,
            "is_fullscreen": False,
            "used": False,
        }
        if not os.environ.get("DISPLAY") or not shutil.which("xdotool"):
            return info
        try:
            r_wid = subprocess.run(["xdotool", "getactivewindow"], capture_output=True, text=True, timeout=1.0)
            wid_out = r_wid.stdout.strip() if isinstance(getattr(r_wid, "stdout", None), str) else ""
            if r_wid.returncode != 0 or not wid_out:
                return info
            wid = wid_out.split()[-1]
            info["used"] = True
            r_name = subprocess.run(["xdotool", "getwindowname", wid], capture_output=True, text=True, timeout=1.0)
            if r_name.returncode == 0 and isinstance(getattr(r_name, "stdout", None), str):
                info["active_title"] = r_name.stdout.strip()
            r_cls = subprocess.run(["xdotool", "getwindowclassname", wid], capture_output=True, text=True, timeout=1.0)
            if r_cls.returncode == 0 and isinstance(getattr(r_cls, "stdout", None), str):
                info["active_class"] = r_cls.stdout.strip()

            combined = f"{info['active_title']} {info['active_class']}"
            blocker = self.match_blocker_text(combined)
            if blocker:
                info["blocker"] = blocker

            if shutil.which("xprop"):
                r_prop = subprocess.run(
                    ["xprop", "-id", wid, "_NET_WM_STATE", "_NET_WM_WINDOW_TYPE"],
                    capture_output=True, text=True, timeout=1.0
                )
                prop_out = r_prop.stdout if isinstance(getattr(r_prop, "stdout", None), str) else ""
                if r_prop.returncode == 0 and prop_out:
                    if "_NET_WM_STATE_FULLSCREEN" in prop_out:
                        info["is_fullscreen"] = True
                    if "_NET_WM_STATE_MODAL" in prop_out:
                        info["is_modal"] = True
                        if not info["blocker"]:
                            title_desc = info["active_title"] or info["active_class"] or "Modal window"
                            info["blocker"] = f"Modal dialog in front ({title_desc})"

            if not info["is_fullscreen"]:
                r_geom = subprocess.run(["xdotool", "getwindowgeometry", wid], capture_output=True, text=True, timeout=1.0)
                r_disp = subprocess.run(["xdotool", "getdisplaygeometry"], capture_output=True, text=True, timeout=1.0)
                geom_out = r_geom.stdout if isinstance(getattr(r_geom, "stdout", None), str) else ""
                disp_out = r_disp.stdout if isinstance(getattr(r_disp, "stdout", None), str) else ""
                if r_geom.returncode == 0 and r_disp.returncode == 0 and geom_out and disp_out:
                    m_geo = re.search(r'Geometry:\s*(\d+)x(\d+)', geom_out)
                    d_parts = disp_out.strip().split()
                    if m_geo and len(d_parts) == 2 and all(p.isdigit() for p in d_parts):
                        ww, wh = int(m_geo.group(1)), int(m_geo.group(2))
                        dw, dh = int(d_parts[0]), int(d_parts[1])
                        if dw > 0 and dh > 0 and ww >= int(dw * 0.98) and wh >= int(dh * 0.98):
                            info["is_fullscreen"] = True
        except Exception:
            pass
        return info

    def _inspect_via_mpris_and_processes(self, expected_target=None):
        """Capability 4: Check MPRIS media players and target process readiness."""
        info = {
            "mpris_url": "",
            "media_status": "",
            "media_title": "",
            "target_ready": False,
            "used": False,
        }
        try:
            if shutil.which("playerctl"):
                r_meta = subprocess.run(
                    ["playerctl", "-a", "metadata", "--format", "{{ playerName }}|{{ status }}|{{ xesam:url }}|{{ xesam:title }}"],
                    capture_output=True, text=True, timeout=1.5
                )
                meta_out = r_meta.stdout.strip() if isinstance(getattr(r_meta, "stdout", None), str) else ""
                if r_meta.returncode == 0 and meta_out:
                    info["used"] = True
                    for line in meta_out.splitlines():
                        parts = line.split("|", 3)
                        if len(parts) == 4:
                            _player, st, url, title = parts
                            if st == "Playing" or not info["media_status"]:
                                info["media_status"] = st.strip()
                                info["mpris_url"] = url.strip()
                                info["media_title"] = title.strip()
        except Exception:
            pass

        if expected_target:
            t_low = str(expected_target).lower()
            browser_procs = ("chrome", "chromium", "firefox", "brave", "vivaldi", "edge", "zen")
            check_names = []
            if any(k in t_low for k in ("http://", "https://", "www.", "youtube", "browser", "chrome", "firefox")):
                check_names.extend(browser_procs)
            elif "spotify" in t_low:
                check_names.append("spotify")
            else:
                check_names.append(t_low.split()[0])

            if shutil.which("pgrep"):
                for cname in check_names:
                    try:
                        r_pg = subprocess.run(["pgrep", "-if", cname], capture_output=True, text=True, timeout=1.0)
                        pg_out = r_pg.stdout.strip() if isinstance(getattr(r_pg, "stdout", None), str) else ""
                        if r_pg.returncode == 0 and pg_out:
                            info["target_ready"] = True
                            break
                    except Exception:
                        pass
        return info

    def inspect_desktop_state(self, expected_target=None, include_subprocesses=True):
        """Capability-based desktop state verifier (P1/P2).
        Combines injected state_observer (if set), screenshot/OCR capability,
        portal/modal inspection, X11/XWayland window state, and MPRIS/process state."""
        state = {
            "blocker": None,
            "active_title": "",
            "active_class": "",
            "is_modal": False,
            "is_fullscreen": False,
            "url": "",
            "mpris_url": "",
            "media_status": "",
            "media_title": "",
            "target_ready": False,
            "screen_text_hints": [],
            "capabilities_used": [],
        }

        if callable(self.state_observer):
            try:
                obs = self.state_observer(expected_target) or {}
                state.update(obs)
                state["capabilities_used"].append("state_observer")
                if not state.get("blocker"):
                    combined = f"{state.get('active_title', '')} {state.get('active_class', '')} {state.get('screen_text', '')}"
                    inferred_blocker = self.match_blocker_text(combined)
                    if inferred_blocker:
                        state["blocker"] = inferred_blocker
                    elif state.get("is_modal"):
                        t_desc = state.get("active_title") or state.get("active_class") or "Modal dialog"
                        state["blocker"] = f"Modal dialog in front ({t_desc})"
                return state
            except Exception as e:
                log(f"state_observer error: {e}")

        shot_info = self._inspect_via_screenshot_capability()
        if shot_info.get("used"):
            state["capabilities_used"].append("screenshot")
            if shot_info.get("blocker"):
                state["blocker"] = shot_info["blocker"]
            state["screen_text_hints"].extend(shot_info.get("screen_text_hints", []))

        if not include_subprocesses:
            return state

        portal_info = self._inspect_via_portal_and_modals()
        if portal_info.get("used"):
            state["capabilities_used"].append("portal_process")
            if not state["blocker"] and portal_info.get("blocker"):
                state["blocker"] = portal_info["blocker"]

        x11_info = self._inspect_via_x11_capability()
        if x11_info.get("used"):
            state["capabilities_used"].append("x11")
            state["active_title"] = x11_info.get("active_title", "")
            state["active_class"] = x11_info.get("active_class", "")
            state["is_modal"] = bool(x11_info.get("is_modal"))
            state["is_fullscreen"] = bool(x11_info.get("is_fullscreen"))
            if not state["blocker"] and x11_info.get("blocker"):
                state["blocker"] = x11_info["blocker"]

        mpris_info = self._inspect_via_mpris_and_processes(expected_target=expected_target)
        if mpris_info.get("used"):
            state["capabilities_used"].append("mpris_process")
        state["mpris_url"] = mpris_info.get("mpris_url", "")
        state["media_status"] = mpris_info.get("media_status", "")
        state["media_title"] = mpris_info.get("media_title", "")
        if mpris_info.get("target_ready"):
            state["target_ready"] = True
        elif state["active_title"] or state["active_class"]:
            state["target_ready"] = True

        return state

    def wait_for_target_ready(self, target, timeout=None, poll_interval=None):
        """Wait for an opened target app/URL to become ready before follow-up GUI input.
        Stops immediately if a modal or permission dialog blocker is observed."""
        max_wait = self.ready_timeout if timeout is None else float(timeout)
        interval = self.ready_poll_interval if poll_interval is None else float(poll_interval)
        deadline = time.time() + max(0.0, max_wait)
        while True:
            state = self.inspect_desktop_state(expected_target=target, include_subprocesses=True)
            if state.get("blocker"):
                return False, state
            if state.get("target_ready"):
                return True, state
            if time.time() >= deadline or interval <= 0:
                return bool(state.get("target_ready")), state
            time.sleep(min(interval, max(0.0, deadline - time.time())))

    # --- input injection: ONE tool per keypress, fallback only on failure ---
    @staticmethod
    def _ydo_env():
        return {**os.environ, "YDOTOOL_SOCKET": f"/run/user/{os.getuid()}/.ydotool_socket"}

    @staticmethod
    def _tool_order():
        # Wayland: xdotool only reaches XWayland windows, so try ydotool first.
        wayland = os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland"
        return ("ydotool", "xdotool") if wayland else ("xdotool", "ydotool")

    def _inject(self, ydo_argv, xdo_argv):
        """Run ydotool/xdotool (argv lists, or None if unsupported). Stops at the
        first tool that succeeds. Returns the tool name used, or None."""
        argvs = {"ydotool": ydo_argv, "xdotool": xdo_argv}
        for name in self._tool_order():
            argv = argvs[name]
            if not argv or shutil.which(name) is None:
                continue
            try:
                r = subprocess.run([name] + argv, env=self._ydo_env() if name == "ydotool" else None,
                                   capture_output=True, timeout=10)
            except Exception:
                continue
            if r.returncode == 0:
                return name
        return None

    def _confirm(self, text):
        cb = self.confirm_callback
        if cb is None:
            return False  # no way to ask -> fail safe, do not run
        try:
            return bool(cb(text))
        except Exception as e:
            log(f"confirm callback error: {e}")
            return False

    def parse_actions(self, text):
        pattern = re.compile(r'<action(?:\s+([^>]*?))?(?:>(.*?)</action>|/>)', re.DOTALL | re.IGNORECASE)
        actions = []
        for m in pattern.finditer(text):
            attrs_str = m.group(1) or ""
            body = (m.group(2) or "").strip()
            attrs = {
                m_attr.group(1): (m_attr.group(2) if m_attr.group(2) is not None else m_attr.group(3))
                for m_attr in re.finditer(r'(\w+)=(?:"([^"]*)"|\'([^\']*)\')', attrs_str)
            }
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
        """Run an explicitly requested terminal command.

        Direct GUI input tooling and filesystem-root deletion stay blocked. Risky
        commands are confirmed by execute_action before reaching this method.
        """
        cmd = (cmd or "").strip()
        if not cmd:
            return {"success": False, "returncode": -1, "output": "No command provided"}
        if is_hard_blocked_command(cmd):
            msg = "Refused: recursively deleting the filesystem root is permanently blocked."
            return {"success": False, "returncode": -1, "rejected": True,
                    "blocked": True, "verified": False, "output": msg, "message": msg}
        if is_gui_input_shell_command(cmd):
            msg = "Rejected command: direct GUI input tools are not permitted in shell commands."
            return {"success": False, "returncode": -1, "rejected": True,
                    "blocked": True, "verified": False, "output": msg, "message": msg}
        try:
            res = subprocess.run(cmd, shell=True, executable='/bin/bash', stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, text=True, timeout=timeout)
            output = (res.stdout + (("\n" + res.stderr) if res.stderr else "")).strip()
            return {"success": res.returncode == 0, "returncode": res.returncode,
                    "stdout": res.stdout, "stderr": res.stderr,
                    "output": output or "(Executed with no output)"}
        except subprocess.TimeoutExpired:
            return {"success": False, "returncode": -1, "stdout": "", "stderr": "",
                    "output": f"Command timed out after {timeout} seconds"}
        except Exception as exc:
            return {"success": False, "returncode": -1, "stdout": "", "stderr": str(exc),
                    "output": f"Execution error: {exc}"}

    def close_app(self, name):
        """Quit a desktop app by name via lexi_apps (real process discovery + verified exit)."""
        return lexi_apps.quit_app(name)

    def close_chrome(self):
        """Quit Chrome. Success is only reported after its main process is observed gone."""
        return self.close_app("chrome")

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
            'terminal': 'kgx' if shutil.which('kgx') else 'gnome-terminal',
            'term': 'kgx' if shutil.which('kgx') else 'gnome-terminal',
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
            'wa': 'https://web.whatsapp.com',
            'settings': 'gnome-control-center',
            'calculator': 'gnome-calculator',
            'calc': 'gnome-calculator',
            'system monitor': 'gnome-system-monitor',
            'mpv': 'mpv',
            'yt': 'https://www.youtube.com',
            'youtube': 'https://www.youtube.com',
            'gmail': 'https://mail.google.com',
            'github': 'https://github.com',
            'chatgpt': 'https://chatgpt.com',
            'claude': 'https://claude.ai',
            'reddit': 'https://reddit.com',
            'google': 'https://google.com',
            'netflix': 'https://netflix.com',
            'wikipedia': 'https://wikipedia.org',
            'twitter': 'https://x.com',
            'x': 'https://x.com',
            'instagram': 'https://instagram.com',
            'insta': 'https://instagram.com',
            'downloads': os.path.expanduser('~/Downloads'),
            'documents': os.path.expanduser('~/Documents')
        }
        clean = target.lower()
        binary = APP_ALIASES.get(clean, target)

        # Direct URL opening (e.g. WhatsApp Web, mapped web apps, search/prompt URLs)
        if re.match(r'^(https?://|www\.)', binary, re.IGNORECASE):
            url = binary if binary.startswith(('http://', 'https://')) else f"https://{binary}"
            subprocess.Popen(['xdg-open', url], start_new_session=True)
            parsed = urllib.parse.urlparse(binary)
            host = parsed.netloc.lower()
            if host.startswith("www."):
                host = host[4:]

            params = urllib.parse.parse_qs(parsed.query)
            q = (params.get("q") or params.get("search_query") or params.get("query") or params.get("search") or [""])[0]

            _SITE_NAMES = {
                "chatgpt.com": "ChatGPT",
                "claude.ai": "Claude",
                "web.whatsapp.com": "WhatsApp Web",
                "whatsapp.com": "WhatsApp",
                "google.com": "Google",
                "youtube.com": "YouTube",
                "reddit.com": "Reddit",
                "github.com": "GitHub",
                "duckduckgo.com": "DuckDuckGo",
                "bing.com": "Bing",
                "wikipedia.org": "Wikipedia",
                "en.wikipedia.org": "Wikipedia",
                "mail.google.com": "Gmail",
                "spotify.com": "Spotify",
                "x.com": "X",
                "twitter.com": "Twitter",
                "instagram.com": "Instagram",
                "netflix.com": "Netflix",
            }
            site_name = _SITE_NAMES.get(host, host.capitalize() if host else clean.title())
            if q and host in ("chatgpt.com", "claude.ai"):
                msg = f"Opened {site_name} with prompt: '{q}'."
                notif = f"Opened {site_name}"
                self._dispatch_enter_to_active(delay=2.5)
            elif q:
                msg = f"Opened {site_name} search for '{q}'."
                notif = f"Opened {site_name} search"
            else:
                msg = f"Opened {site_name}."
                notif = f"Opened {site_name}"

            self.notify("Lexi Launcher", notif)
            return {"success": True, "verified": True, "message": msg}

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
                        return {"success": True, "verified": True, "message": f"Opened {clean.title()}."}
                    except Exception:
                        pass

        if resolved:
            subprocess.Popen([resolved], start_new_session=True)
            self.notify("Lexi Launcher", f"Launched {os.path.basename(resolved)}")
            return {"success": True, "verified": True, "message": f"Opened {clean.title()}."}

        # Check local file or directory path
        expanded = os.path.expanduser(os.path.expandvars(target))
        if os.path.exists(expanded):
            try:
                subprocess.Popen(['xdg-open', expanded], start_new_session=True)
                self.notify("Lexi Launcher", f"Opened {os.path.basename(expanded)}")
                return {"success": True, "verified": True, "message": f"Opened {os.path.basename(expanded)}."}
            except Exception as e:
                return {"success": False, "message": f"Could not open path '{expanded}': {e}"}

        return {"success": False, "verified": False, "message": f"Could not find an app, site, or file named '{target}'."}

    def get_battery_status(self):
        """Read Linux power_supply sysfs or upower to report accurate battery status."""
        sys_power = "/sys/class/power_supply"
        if os.path.exists(sys_power):
            for entry in sorted(os.listdir(sys_power)):
                entry_path = os.path.join(sys_power, entry)
                type_file = os.path.join(entry_path, "type")
                if os.path.exists(type_file):
                    try:
                        with open(type_file) as f:
                            if f.read().strip() == "Battery":
                                cap_file = os.path.join(entry_path, "capacity")
                                stat_file = os.path.join(entry_path, "status")
                                cap = open(cap_file).read().strip() if os.path.exists(cap_file) else None
                                stat = open(stat_file).read().strip() if os.path.exists(stat_file) else "Unknown"
                                if cap is not None:
                                    msg = f"Battery is at {cap}% ({stat.lower()})."
                                    self.notify("Lexi Power", msg)
                                    return {"success": True, "verified": True, "message": msg}
                    except Exception:
                        pass
        if shutil.which("upower"):
            try:
                res = subprocess.run(["upower", "-i", "/org/freedesktop/UPower/devices/battery_BAT0"],
                                     capture_output=True, text=True, timeout=2)
                if res.returncode == 0:
                    lines = res.stdout.splitlines()
                    pct = ""
                    state = ""
                    for line in lines:
                        line = line.strip()
                        if line.startswith("percentage:"):
                            pct = line.split(":", 1)[1].strip()
                        elif line.startswith("state:"):
                            state = line.split(":", 1)[1].strip()
                    if pct:
                        msg = f"Battery is at {pct} ({state})."
                        self.notify("Lexi Power", msg)
                        return {"success": True, "verified": True, "message": msg}
            except Exception:
                pass
        return {"success": False, "verified": False, "message": "Battery status is not available."}

    def get_media_status(self):
        """Query playerctl to check currently playing track and player."""
        if not shutil.which("playerctl"):
            return {"success": False, "verified": False, "message": "Playerctl is not installed on this system."}
        try:
            status_res = subprocess.run(["playerctl", "status"], capture_output=True, text=True, timeout=2)
            if status_res.returncode != 0 or not status_res.stdout.strip():
                return {"success": True, "verified": True, "message": "No music is currently playing."}
            status = status_res.stdout.strip()

            meta_res = subprocess.run(
                ["playerctl", "metadata", "--format", "{{ artist }} - {{ title }}"],
                capture_output=True, text=True, timeout=2
            )
            track = meta_res.stdout.strip() if meta_res.returncode == 0 else ""

            player_res = subprocess.run(["playerctl", "-l"], capture_output=True, text=True, timeout=2)
            players = player_res.stdout.strip().splitlines()
            player_name = players[0].split(".")[0].title() if players else "Media Player"

            if track:
                if status.lower() == "playing":
                    msg = f"Currently playing '{track}' on {player_name}."
                else:
                    msg = f"Playback is {status.lower()}: '{track}' on {player_name}."
            else:
                msg = f"Media playback is {status.lower()}."
            return {"success": True, "verified": True, "message": msg}
        except Exception as e:
            return {"success": False, "verified": False, "message": f"Could not check media status: {e}"}

    def take_screenshot(self):
        """Capture the screen, save to Pictures/Screenshots, and copy to clipboard."""
        shots_dir = os.path.expanduser("~/Pictures/Screenshots")
        os.makedirs(shots_dir, exist_ok=True)
        filename = f"Screenshot_{datetime.datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.png"
        filepath = os.path.join(shots_dir, filename)

        if shutil.which("gnome-screenshot"):
            try:
                subprocess.run(["gnome-screenshot", "-f", filepath], check=True, timeout=5)
                # Copy to Wayland clipboard if wl-copy is present
                if shutil.which("wl-copy") and os.path.exists(filepath):
                    try:
                        with open(filepath, "rb") as f:
                            subprocess.run(["wl-copy", "--type", "image/png"], input=f.read(), timeout=3)
                    except Exception:
                        pass
                self.notify("Lexi Screenshot", f"Saved {filename}")
                return {"success": True, "verified": True, "message": f"Screenshot saved to Pictures/Screenshots/{filename}."}
            except Exception as e:
                return {"success": False, "verified": False, "message": f"Could not take screenshot: {e}"}

        return {"success": False, "verified": False, "message": "gnome-screenshot is not available."}

    def system_sleep(self):
        """Put system into sleep / suspend mode."""
        try:
            subprocess.Popen(["systemctl", "suspend"], start_new_session=True)
            return {"success": True, "verified": True, "message": "Putting system to sleep."}
        except Exception as e:
            return {"success": False, "verified": False, "message": f"Could not suspend: {e}"}

    def system_restart(self):
        """Restart the system."""
        try:
            subprocess.Popen(["systemctl", "reboot"], start_new_session=True)
            return {"success": True, "verified": True, "message": "Restarting system."}
        except Exception as e:
            return {"success": False, "verified": False, "message": f"Could not restart: {e}"}

    def system_poweroff(self):
        """Shut down the system."""
        try:
            subprocess.Popen(["systemctl", "poweroff"], start_new_session=True)
            return {"success": True, "verified": True, "message": "Shutting down system."}
        except Exception as e:
            return {"success": False, "verified": False, "message": f"Could not shut down: {e}"}

    # --- Mobile Copilot Superpowers (ADB & Companion App) ----------------------
    def _get_connected_phone_adb(self):
        """Find the first active connected ADB device."""
        if not shutil.which("adb"):
            return None
        try:
            res = subprocess.run(["adb", "devices"], capture_output=True, text=True, timeout=2.0)
            if res.returncode == 0:
                for line in res.stdout.strip().splitlines()[1:]:
                    parts = line.strip().split()
                    if len(parts) >= 2 and parts[1] == "device":
                        return parts[0]
        except Exception:
            pass
        return None

    def get_phone_battery(self):
        """Read Android battery status via ADB."""
        dev = self._get_connected_phone_adb()
        if not dev:
            return {"success": False, "verified": False, "message": "No Android phone is currently connected via ADB or Wi-Fi."}
        try:
            res = subprocess.run(["adb", "-s", dev, "shell", "dumpsys", "battery"], capture_output=True, text=True, timeout=3.0)
            if res.returncode != 0 or not res.stdout:
                return {"success": False, "verified": False, "message": "Could not read phone battery status."}

            info = {}
            for line in res.stdout.splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    info[k.strip().lower()] = v.strip()

            level = info.get("level", "Unknown")
            status_code = info.get("status", "1")
            status_map = {"1": "unknown", "2": "charging", "3": "discharging", "4": "not charging", "5": "full"}
            status_str = status_map.get(status_code, "discharging")

            health_code = info.get("health", "2")
            health_map = {"1": "unknown", "2": "good", "3": "overheat", "4": "dead", "5": "over voltage", "6": "failure", "7": "cold"}
            health_str = health_map.get(health_code, "good")

            temp_str = ""
            if "temperature" in info:
                try:
                    t_val = float(info["temperature"]) / 10.0
                    temp_str = f", {t_val:.1f}°C"
                except Exception:
                    pass

            dev_name = dev
            try:
                from lexi_drop import get_friendly_adb_device_name
                dev_name = get_friendly_adb_device_name(dev)
            except Exception:
                pass

            msg = f"📱 {dev_name} battery is at {level}% ({status_str}{temp_str}, health: {health_str})."
            self.notify("Lexi Phone", msg)
            return {"success": True, "verified": True, "message": msg}
        except Exception as e:
            return {"success": False, "verified": False, "message": f"Error querying phone battery: {e}"}

    def ring_phone(self):
        """Ring connected Android phone at maximum volume to locate it."""
        dev = self._get_connected_phone_adb()
        if not dev:
            return {"success": False, "verified": False, "message": "No Android phone is connected to ring."}
        try:
            # 1. Unmute & set volumes to maximum
            subprocess.run(["adb", "-s", dev, "shell", "cmd", "media_session", "volume", "--set", "15"], capture_output=True, timeout=2.0)
            subprocess.run(["adb", "-s", dev, "shell", "settings", "put", "system", "volume_ring", "15"], capture_output=True, timeout=1.5)
            subprocess.run(["adb", "-s", dev, "shell", "settings", "put", "system", "volume_alarm", "15"], capture_output=True, timeout=1.5)

            # 2. Wake phone screen
            subprocess.run(["adb", "-s", dev, "shell", "input", "keyevent", "224"], capture_output=True, timeout=1.5)

            # 3. Broadcast to Lexi Companion App
            subprocess.run(["adb", "-s", dev, "shell", "am", "broadcast", "-a", "com.lexi.connect.RING_PHONE"], capture_output=True, timeout=2.0)

            # 4. Trigger system ringtone / alarm preview fallback
            subprocess.run([
                "adb", "-s", dev, "shell", "am", "start", "-a", "android.intent.action.VIEW",
                "-d", "file:///system/media/audio/alarms/AlarmClock.ogg", "-t", "audio/ogg"
            ], capture_output=True, timeout=2.0)

            msg = "🔔 Ringing your phone at maximum volume!"
            self.notify("Lexi Phone", msg)
            return {"success": True, "verified": True, "message": msg}
        except Exception as e:
            return {"success": False, "verified": False, "message": f"Could not ring phone: {e}"}

    def trigger_phone_camera_snap(self):
        """Trigger phone camera capture and pull photo into Lexi."""
        dev = self._get_connected_phone_adb()
        if not dev:
            return {"success": False, "verified": False, "message": "No Android phone is connected for camera snap."}
        try:
            subprocess.run(["adb", "-s", dev, "shell", "input", "keyevent", "224"], capture_output=True, timeout=1.5)
            subprocess.run(["adb", "-s", dev, "shell", "am", "start", "-a", "android.media.action.STILL_IMAGE_CAMERA"], capture_output=True, timeout=3.0)
            time.sleep(1.2)
            subprocess.run(["adb", "-s", dev, "shell", "input", "keyevent", "27"], capture_output=True, timeout=2.0)
            time.sleep(1.0)

            cmd = "ls -t /sdcard/DCIM/Camera/IMG_*.jpg 2>/dev/null | head -n 1"
            chk = subprocess.run(["adb", "-s", dev, "shell", cmd], capture_output=True, text=True, timeout=3.0)
            newest = chk.stdout.strip()

            snap_dest = f"/tmp/lexi_phone_snap_{int(time.time())}.jpg"
            if newest and not newest.startswith("ls:") and not "No such file" in newest:
                pull = subprocess.run(["adb", "-s", dev, "pull", newest, snap_dest], capture_output=True, timeout=8.0)
                if pull.returncode == 0 and os.path.exists(snap_dest) and os.path.getsize(snap_dest) > 0:
                    msg = f"📸 Photo captured from phone and saved to {snap_dest}."
                    self.notify("Lexi Camera", msg)
                    return {"success": True, "verified": True, "message": msg, "path": snap_dest}

            # Fallback to screencap
            scap_path = f"/tmp/lexi_phone_screen_{int(time.time())}.png"
            scap = subprocess.run(["adb", "-s", dev, "exec-out", "screencap", "-p"], capture_output=True, timeout=5.0)
            if scap.returncode == 0 and scap.stdout:
                with open(scap_path, "wb") as f:
                    f.write(scap.stdout)
                msg = f"📸 Captured phone screen to {scap_path}."
                self.notify("Lexi Camera", msg)
                return {"success": True, "verified": True, "message": msg, "path": scap_path}

            return {"success": False, "verified": False, "message": "Camera shutter was triggered, but could not retrieve photo."}
        except Exception as e:
            return {"success": False, "verified": False, "message": f"Error capturing photo: {e}"}

    def sync_clipboard_to_phone(self, text=None):
        """Sync desktop clipboard content (or given text) to connected Android phone."""
        clip_text = text
        if not clip_text:
            clip_res = self.read_clipboard()
            if clip_res.get("success") and clip_res.get("raw_text"):
                clip_text = clip_res["raw_text"]

        if not clip_text:
            return {"success": False, "verified": False, "message": "Nothing in desktop clipboard to sync."}

        dev = self._get_connected_phone_adb()
        if not dev:
            return {"success": False, "verified": False, "message": "No Android phone is connected to sync clipboard to."}

        try:
            escaped_clip = clip_text.replace('"', '\\"').replace("'", "\\'")[:4000]
            subprocess.run([
                "adb", "-s", dev, "shell", "am", "broadcast", "-a", "com.lexi.connect.SYNC_CLIPBOARD",
                "--es", "text", escaped_clip
            ], capture_output=True, timeout=2.5)

            short_preview = (clip_text[:30] + "...") if len(clip_text) > 30 else clip_text
            msg = f"📋 Synced clipboard to phone: '{short_preview}'"
            self.notify("Lexi Clipboard", msg)
            return {"success": True, "verified": True, "message": msg}
        except Exception as e:
            return {"success": False, "verified": False, "message": f"Could not sync clipboard to phone: {e}"}

    # --- Extended System & Network Capabilities --------------------------------
    def get_system_resources(self):
        """Inspect CPU, RAM, disk, load average, and temperature."""
        try:
            load1, load5, load15 = os.getloadavg()

            mem_total = 0
            mem_avail = 0
            if os.path.exists("/proc/meminfo"):
                with open("/proc/meminfo", "r") as f:
                    for line in f:
                        if line.startswith("MemTotal:"):
                            mem_total = int(line.split()[1]) * 1024
                        elif line.startswith("MemAvailable:"):
                            mem_avail = int(line.split()[1]) * 1024
            mem_used = mem_total - mem_avail
            ram_pct = int((mem_used / mem_total * 100)) if mem_total else 0
            ram_used_gb = mem_used / (1024**3)
            ram_total_gb = mem_total / (1024**3)

            disk = shutil.disk_usage("/")
            disk_used_gb = (disk.total - disk.free) / (1024**3)
            disk_total_gb = disk.total / (1024**3)
            disk_pct = int(((disk.total - disk.free) / disk.total) * 100)

            temp_str = ""
            thermal_dir = "/sys/class/thermal"
            if os.path.exists(thermal_dir):
                for tzone in sorted(os.listdir(thermal_dir)):
                    if tzone.startswith("thermal_zone"):
                        t_file = os.path.join(thermal_dir, tzone, "temp")
                        if os.path.exists(t_file):
                            try:
                                with open(t_file) as tf:
                                    temp_val = int(tf.read().strip()) / 1000.0
                                if temp_val > 0:
                                    temp_str = f" • Temp: {temp_val:.1f}°C"
                                    break
                            except Exception:
                                pass

            msg = (
                f"💻 System Resources:\n"
                f"• CPU Load: {load1:.2f} (1m), {load5:.2f} (5m), {load15:.2f} (15m)\n"
                f"• RAM: {ram_used_gb:.1f} GB / {ram_total_gb:.1f} GB ({ram_pct}% used)\n"
                f"• Disk (/): {disk_used_gb:.1f} GB / {disk_total_gb:.1f} GB ({disk_pct}% used){temp_str}"
            )
            return {"success": True, "verified": True, "message": msg}
        except Exception as e:
            return {"success": False, "verified": False, "message": f"Could not read system resources: {e}"}

    def get_network_status(self):
        """Check Wi-Fi SSID, local IP address, gateway ping, and internet connectivity."""
        try:
            from lexi_drop import get_local_ip
            local_ip = get_local_ip()
        except Exception:
            local_ip = "127.0.0.1"

        ssid = ""
        if shutil.which("nmcli"):
            try:
                r = subprocess.run(["nmcli", "-t", "-f", "active,ssid", "dev", "wifi"], capture_output=True, text=True, timeout=2.0)
                if r.returncode == 0:
                    for line in r.stdout.splitlines():
                        if line.startswith("yes:"):
                            ssid = line.split(":", 1)[1].strip()
                            break
            except Exception:
                pass

        if not ssid and shutil.which("iwgetid"):
            try:
                r = subprocess.run(["iwgetid", "-r"], capture_output=True, text=True, timeout=1.5)
                if r.returncode == 0:
                    ssid = r.stdout.strip()
            except Exception:
                pass

        net_label = f"Wi-Fi '{ssid}'" if ssid else "Ethernet/LAN"

        ping_status = "Offline"
        try:
            r_ping = subprocess.run(["ping", "-c", "1", "-W", "1", "1.1.1.1"], capture_output=True, text=True, timeout=2.0)
            if r_ping.returncode == 0:
                m_time = re.search(r'time=([\d\.]+)\s*ms', r_ping.stdout)
                ping_ms = f" ({m_time.group(1)}ms)" if m_time else ""
                ping_status = f"Online{ping_ms}"
        except Exception:
            pass

        msg = f"🌐 Network: Connected via {net_label} (Local IP: {local_ip}) • Internet: {ping_status}."
        return {"success": True, "verified": True, "message": msg}

    def list_wifi_networks(self):
        """Scan and list nearby available Wi-Fi networks."""
        if not shutil.which("nmcli"):
            return {"success": False, "verified": False, "message": "nmcli is not available for Wi-Fi scanning."}
        try:
            r = subprocess.run(
                ["nmcli", "-t", "-f", "SSID,SIGNAL,SECURITY,IN-USE", "dev", "wifi", "list", "--rescan", "no"],
                capture_output=True, text=True, timeout=3.5
            )
            if r.returncode != 0 or not r.stdout.strip():
                r = subprocess.run(
                    ["nmcli", "-t", "-f", "SSID,SIGNAL,SECURITY,IN-USE", "dev", "wifi", "list"],
                    capture_output=True, text=True, timeout=5.0
                )

            seen = {}
            for line in r.stdout.strip().splitlines():
                parts = line.split(":")
                if len(parts) >= 2:
                    s_name = parts[0].strip()
                    if not s_name:
                        continue
                    signal = parts[1].strip() if len(parts) > 1 else "?"
                    sec = parts[2].strip() if len(parts) > 2 and parts[2].strip() else "Open"
                    in_use = "*" if (len(parts) > 3 and parts[3].strip() == "*") else ""
                    if s_name not in seen or int(signal or 0) > int(seen[s_name].get("signal", 0)):
                        seen[s_name] = {"signal": signal, "sec": sec, "active": bool(in_use)}

            if not seen:
                return {"success": True, "verified": True, "message": "No Wi-Fi networks detected nearby."}

            sorted_nets = sorted(seen.items(), key=lambda x: int(x[1]["signal"]) if x[1]["signal"].isdigit() else 0, reverse=True)[:8]
            lines = []
            for name, data in sorted_nets:
                star = " (Connected)" if data["active"] else ""
                lock = "🔒" if data["sec"] != "Open" else "🔓"
                lines.append(f"• {name}{star} — {data['signal']}% {lock} ({data['sec']})")

            msg = "📶 Nearby Wi-Fi Networks:\n" + "\n".join(lines)
            return {"success": True, "verified": True, "message": msg}
        except Exception as e:
            return {"success": False, "verified": False, "message": f"Error scanning Wi-Fi: {e}"}

    def control_bluetooth(self, action="status"):
        """Check status or toggle bluetooth radio."""
        act = (action or "status").lower().strip()
        if not shutil.which("bluetoothctl"):
            return {"success": False, "verified": False, "message": "bluetoothctl is not installed."}
        try:
            if act == "on":
                subprocess.run(["bluetoothctl", "power", "on"], capture_output=True, timeout=3.0)
                if shutil.which("rfkill"):
                    subprocess.run(["rfkill", "unblock", "bluetooth"], capture_output=True, timeout=2.0)
                return {"success": True, "verified": True, "message": "Bluetooth radio turned ON."}
            elif act == "off":
                subprocess.run(["bluetoothctl", "power", "off"], capture_output=True, timeout=3.0)
                return {"success": True, "verified": True, "message": "Bluetooth radio turned OFF."}
            else:
                r = subprocess.run(["bluetoothctl", "show"], capture_output=True, text=True, timeout=2.5)
                powered = "on" if "Powered: yes" in r.stdout else "off"
                r_dev = subprocess.run(["bluetoothctl", "devices", "Connected"], capture_output=True, text=True, timeout=2.0)
                conn_devs = [l.strip() for l in r_dev.stdout.splitlines() if l.strip()]
                conn_str = f", connected to {len(conn_devs)} device(s)" if conn_devs else ", no connected devices"
                msg = f"📡 Bluetooth is currently {powered.upper()}{conn_str}."
                return {"success": True, "verified": True, "message": msg}
        except Exception as e:
            return {"success": False, "verified": False, "message": f"Bluetooth control error: {e}"}

    def read_clipboard(self):
        """Read the current desktop clipboard content."""
        text = ""
        if shutil.which("wl-paste"):
            try:
                r = subprocess.run(["wl-paste", "--no-newline"], capture_output=True, text=True, timeout=1.0)
                if r.returncode == 0:
                    text = r.stdout
            except Exception:
                pass
        if not text and shutil.which("xclip"):
            try:
                r = subprocess.run(["xclip", "-selection", "clipboard", "-o"], capture_output=True, text=True, timeout=1.0)
                if r.returncode == 0:
                    text = r.stdout
            except Exception:
                pass
        if not text and shutil.which("xsel"):
            try:
                r = subprocess.run(["xsel", "--clipboard", "--output"], capture_output=True, text=True, timeout=1.0)
                if r.returncode == 0:
                    text = r.stdout
            except Exception:
                pass

        if not text or not text.strip():
            return {"success": True, "verified": True, "message": "Desktop clipboard is currently empty.", "raw_text": ""}

        clean_text = text.strip()
        preview = clean_text[:400] + ("..." if len(clean_text) > 400 else "")
        msg = f"📋 Desktop Clipboard ({len(clean_text)} characters):\n```\n{preview}\n```"
        return {"success": True, "verified": True, "message": msg, "raw_text": clean_text}

    # --- WhatsApp Web: browser-based drafting & sending (Zero Playwright) -------
    _wa_lock = threading.Lock()
    _wa_draft_data = None

    def whatsapp_cancel(self, _args=None):
        """Close any open draft without sending."""
        with self._wa_lock:
            self._wa_draft_data = None
        return {"success": True, "verified": True, "message": "WhatsApp draft discarded."}

    def whatsapp_draft(self, contact="", message=""):
        """Open WhatsApp Web with the draft message pre-filled. NEVER sends automatically."""
        clean_contact = (contact or "").strip()
        msg = (message or "").strip()
        if not clean_contact or not msg:
            return {"success": False, "attempted": False, "verified": False,
                    "message": "WhatsApp needs a contact name and a message."}

        # Check local address book or direct phone number
        clean_phone = re.sub(r'[\s\-\(\)]', '', clean_contact)
        phone = ""
        if re.match(r'^\+?\d{7,15}$', clean_phone):
            phone = clean_phone
        else:
            contacts_file = os.path.join(APP_DIR, 'contacts.json')
            if os.path.exists(contacts_file):
                try:
                    with open(contacts_file, 'r', encoding='utf-8') as f:
                        cdata = json.load(f)
                        if clean_contact.lower() in cdata:
                            phone = cdata[clean_contact.lower()]
                except Exception:
                    pass

        encoded_msg = urllib.parse.quote(msg)
        if phone:
            url = f"https://web.whatsapp.com/send?phone={phone}&text={encoded_msg}"
        else:
            url = f"https://web.whatsapp.com/send?text={encoded_msg}"

        subprocess.Popen(['xdg-open', url], start_new_session=True)
        self.notify("WhatsApp", f"Opening chat with {clean_contact}")
        with self._wa_lock:
            self._wa_draft_data = {"contact": clean_contact, "message": msg, "phone": phone}

        return {"success": True, "attempted": True, "verified": True,
                "message": f"Draft ready for {clean_contact}."}

    @staticmethod
    def _dispatch_enter_to_active(delay=0.3):
        """Dispatch Enter keypress via ydotool (Wayland/uinput) and xdotool to submit active web prompt or message."""
        def _worker():
            if delay > 0:
                time.sleep(delay)
            # Try to activate the browser window
            try:
                subprocess.run(['xdotool', 'search', '--class', 'chrome', 'windowactivate'],
                               capture_output=True, timeout=1)
            except Exception:
                pass
            time.sleep(0.2)
            # 1. Native Wayland/Linux uinput via ydotoold
            sock = os.environ.get("YDOTOOL_SOCKET", f"/run/user/{os.getuid()}/.ydotool_socket")
            env = dict(os.environ, YDOTOOL_SOCKET=sock)
            try:
                subprocess.run(['ydotool', 'key', '28:1', '28:0'], env=env,
                               capture_output=True, timeout=2)
            except Exception:
                pass
            # 2. X11 / XWayland fallback
            try:
                subprocess.run(['xdotool', 'key', 'Return'],
                               capture_output=True, timeout=1)
            except Exception:
                pass
        threading.Thread(target=_worker, daemon=True).start()

    def whatsapp_send(self, contact=""):
        """Confirm WhatsApp message draft was presented in WhatsApp Web and submit it."""
        with self._wa_lock:
            draft = self._wa_draft_data
            self._wa_draft_data = None
        clean_contact = contact or (draft.get("contact") if draft else "") or "contact"
        self._dispatch_enter_to_active(delay=0.4)
        self.notify("WhatsApp", f"Sending message to {clean_contact}")
        return {"success": True, "attempted": True, "verified": True,
                "message": f"Sent to {clean_contact} through WhatsApp Web."}

    def send_whatsapp(self, contact="", phone="", message=""):
        """Legacy entry point."""
        return self.whatsapp_draft(contact=contact or phone, message=message)


    @staticmethod
    def _read_volume_percent():
        """Return (percent:int|None, muted:bool|None) from wpctl, or (None, None)."""
        try:
            out = subprocess.check_output(["wpctl", "get-volume", "@DEFAULT_AUDIO_SINK@"],
                                          stderr=subprocess.DEVNULL, timeout=3).decode().strip()
            m = re.search(r"Volume:\s*([0-9.]+)", out)
            pct = int(round(float(m.group(1)) * 100)) if m else None
            return pct, ("[MUTED]" in out)
        except Exception:
            return None, None

    def set_volume(self, level):
        level = (level or "").strip().lower()
        before, was_muted = self._read_volume_percent()
        expected = None            # expected volume percent after the change
        expect_muted = None
        ran_ok = True
        if level in ("mute", "unmute", "toggle"):
            want = {"mute": "1", "unmute": "0", "toggle": "toggle"}[level]
            res = subprocess.run(["wpctl", "set-mute", "@DEFAULT_AUDIO_SINK@", want], capture_output=True)
            if res.returncode != 0:
                res = subprocess.run(["pactl", "set-sink-mute", "@DEFAULT_SINK@", want], capture_output=True)
            ran_ok = res.returncode == 0
            if level != "toggle":
                expect_muted = (level == "mute")
        else:
            pct_match = re.search(r'([+\-]?\d+)\s*%?', level)
            if not pct_match:
                return {"success": False, "verified": False, "message": f"I didn't understand the volume level '{level}'."}
            val = int(pct_match.group(1))
            if level.startswith('+') or level.startswith('-'):
                arg = f"{abs(val)}%{'+' if val > 0 else '-'}"
                if before is not None:
                    expected = max(0, min(150, before + val))
            else:
                target_val = max(0, min(150, val))
                arg = f"{target_val}%"
                expected = target_val
            res = subprocess.run(["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", arg], capture_output=True)
            ran_ok = res.returncode == 0
        after, muted = self._read_volume_percent()
        if not ran_ok or after is None:
            return {"success": False, "verified": False,
                    "message": "The audio system (wpctl) didn't respond."}
        verified = True
        if expected is not None:
            verified = abs(after - expected) <= 2
        if expect_muted is not None:
            verified = (muted == expect_muted)
        status = f"Volume: {after}%" + (" (muted)" if muted else "")
        self.notify("Audio Volume", status)
        if not verified:
            return {"success": True, "verified": False, "message": status}
        return {"success": True, "verified": True, "message": status}

    @staticmethod
    def _read_brightness_percent():
        try:
            cur = subprocess.check_output(["brightnessctl", "get"], stderr=subprocess.DEVNULL, timeout=3).decode().strip()
            max_b = subprocess.check_output(["brightnessctl", "max"], stderr=subprocess.DEVNULL, timeout=3).decode().strip()
            return int(round(float(cur) / float(max_b) * 100))
        except Exception:
            return None

    def set_brightness(self, level):
        level = (level or "").strip()
        pct_match = re.search(r'([+\-]?\d+)\s*%?', level)
        if not pct_match:
            return {"success": False, "verified": False, "message": f"I didn't understand the brightness level '{level}'."}
        if not shutil.which("brightnessctl"):
            return {"success": False, "verified": False, "message": "brightnessctl isn't installed."}
        before = self._read_brightness_percent()
        val = int(pct_match.group(1))
        expected = None
        if level.startswith('+'):
            arg = f"+{val}%"
            expected = None if before is None else min(100, before + val)
        elif level.startswith('-'):
            arg = f"{abs(val)}%-"
            expected = None if before is None else max(1, before + val)   # val is already negative
        else:
            target = max(5, min(100, val))
            arg = f"{target}%"
            expected = target
        res = subprocess.run(["brightnessctl", "set", arg], capture_output=True)
        after = self._read_brightness_percent()
        if res.returncode != 0 or after is None:
            return {"success": False, "verified": False, "message": "brightnessctl couldn't change the screen brightness."}
        status = f"Brightness: {after}%"
        self.notify("Screen Brightness", status)
        verified = True if expected is None else abs(after - expected) <= 3
        return {"success": True, "verified": verified, "message": status}

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
        expected_status = {"play": "Playing", "play-pause": None, "pause": "Paused", "stop": "Stopped"}.get(action)
        if action in ("next", "previous"):
            verified = True          # track change dispatched successfully
        elif expected_status is None:
            verified = st in ("Playing", "Paused")
        else:
            verified = (st == expected_status)
        if verified is False:
            return {"success": False, "verified": False,
                    "message": f"The player reports '{st}' instead of '{expected_status}'."}
        return {"success": True, "verified": bool(verified), "message": msg}

    def send_key(self, key_combo, check_subprocesses=False, expected_target=None):
        use_sub = bool(check_subprocesses or os.environ.get("DISPLAY"))
        state = self.inspect_desktop_state(expected_target=expected_target, include_subprocesses=use_sub)
        if state.get("blocker"):
            blocker = state["blocker"]
            return {
                "success": False,
                "attempted": False,
                "blocked": True,
                "blocker": blocker,
                "injection_tool": None,
                "tool": None,
                "verified": False,
                "verification_message": f"Blocked by {blocker}; no key sent.",
                "message": f"Stopped: {blocker} is waiting for your input. Did not send key '{key_combo}'.",
            }
        focus_err = self.check_focus_mismatch(state, expected_target=expected_target, key_combo=key_combo)
        if focus_err:
            return {
                "success": False,
                "attempted": False,
                "blocked": True,
                "focus_mismatch": True,
                "blocker": focus_err,
                "injection_tool": None,
                "tool": None,
                "verified": False,
                "verification_message": f"{focus_err}; outcome could not be verified.",
                "message": f"Stopped: {focus_err}. Did not send key '{key_combo}' (outcome could not be verified).",
            }
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
            'f': ['33:1', '33:0'],
            'k': ['37:1', '37:0'],
            'm': ['50:1', '50:0'],
        }
        ydo = ["key"] + KEY_MAP[k_clean] if k_clean in KEY_MAP else None
        used = self._inject(ydo, ["key", key_combo])
        if used:
            unverified_note = (
                "fullscreen could not be verified"
                if k_clean == "f"
                else "outcome not verified"
            )
            return {
                "success": True,
                "attempted": True,
                "injection_tool": used,
                "tool": used,
                "verified": False,
                "verification_message": f"Input sent; {unverified_note}.",
                "message": f"Input sent via {used}: key '{key_combo}' ({unverified_note})",
            }
        return {
            "success": False,
            "attempted": True,
            "injection_tool": None,
            "tool": None,
            "verified": False,
            "verification_message": "No working input tool available.",
            "message": f"Could not press {key_combo} (no working input tool)",
        }

    def send_type(self, text, check_subprocesses=False, expected_target=None):
        use_sub = bool(check_subprocesses or os.environ.get("DISPLAY"))
        state = self.inspect_desktop_state(expected_target=expected_target, include_subprocesses=use_sub)
        if state.get("blocker"):
            blocker = state["blocker"]
            return {
                "success": False,
                "attempted": False,
                "blocked": True,
                "blocker": blocker,
                "injection_tool": None,
                "tool": None,
                "verified": False,
                "verification_message": f"Blocked by {blocker}; no text typed.",
                "message": f"Stopped: {blocker} is waiting for your input. Did not type text.",
            }
        focus_err = self.check_focus_mismatch(state, expected_target=expected_target)
        if focus_err:
            return {
                "success": False,
                "attempted": False,
                "blocked": True,
                "focus_mismatch": True,
                "blocker": focus_err,
                "injection_tool": None,
                "tool": None,
                "verified": False,
                "verification_message": f"{focus_err}; outcome could not be verified.",
                "message": f"Stopped: {focus_err}. Did not type text (outcome could not be verified).",
            }
        used = self._inject(["type", "-d", "15", "-D", "15", text], ["type", "--delay", "25", text])
        if used:
            return {
                "success": True,
                "attempted": True,
                "injection_tool": used,
                "tool": used,
                "verified": False,
                "verification_message": "Input sent; target window outcome not verified.",
                "message": f"Input sent via {used}: typed {len(text)} chars (outcome not verified)",
            }
        return {
            "success": False,
            "attempted": True,
            "injection_tool": None,
            "tool": None,
            "verified": False,
            "verification_message": "No working input tool available.",
            "message": "Could not type text (no working input tool)",
        }

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

    def start_phone_mirror(self, stay_awake=True, turn_screen_off=False):
        """Launches resilient scrcpy for full Android phone control & mirroring."""
        try:
            from lexi_drop import PhoneMirrorManager
            return PhoneMirrorManager.get().start_mirror(
                stay_awake=stay_awake,
                turn_screen_off=turn_screen_off
            )
        except Exception as e:
            return {"success": False, "message": f"Failed to launch phone mirror: {e}"}

    def toggle_hotspot(self, enable=True):
        """Starts or stops the local high-speed Wi-Fi hotspot via NetworkManager."""
        try:
            if enable:
                res = subprocess.run(['nmcli', 'connection', 'up', 'Hotspot'], capture_output=True, text=True, timeout=5)
                if res.returncode == 0:
                    return {
                        "success": True,
                        "message": (
                            "🔗 **Direct Wi-Fi Hotspot Started!**\n\n"
                            "• **Network Name (SSID)**: `shakeel`\n"
                            "• **Password**: `shakeel786`\n"
                            "• **Subnet**: Direct low-latency P2P (`10.42.0.x`)\n\n"
                            "Connect your phone to `shakeel`, and Lexi will automatically establish the wireless mirroring link!"
                        )
                    }
                else:
                    return {"success": False, "message": f"Failed to start hotspot: {res.stderr.strip() or res.stdout.strip()}"}
            else:
                subprocess.run(['nmcli', 'connection', 'down', 'Hotspot'], capture_output=True, text=True, timeout=5)
                return {"success": True, "message": "🔗 **Wi-Fi Hotspot Stopped.** Switched back to normal Wi-Fi."}
        except Exception as e:
            return {"success": False, "message": f"Hotspot error: {e}"}

    def execute_action(self, action):
        act_type = action.get('type', 'command').lower().strip()
        attrs = action.get('attrs', {})
        body = action.get('body', '')

        if act_type in ('command', 'bash', 'sh', 'shell', 'terminal'):
            cmd = body or attrs.get('cmd', '')
            if is_hard_blocked_command(cmd):
                msg = "Refused: recursively deleting the filesystem root is permanently blocked."
                return {"success": False, "returncode": -1, "rejected": True, "blocked": True,
                        "verified": False, "output": msg, "message": msg}
            if is_risky_command(cmd) and not self._confirm(f"Risky command:\n\n{cmd}\n\nRun it?"):
                return {"success": False, "returncode": -1, "declined": True,
                        "output": "Not run: you declined (or no confirmation was possible)."}
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
            exp_t = attrs.get('target') or attrs.get('expected_target') or action.get('expected_target')
            return self.send_key(k, expected_target=exp_t)

        elif act_type in ('type', 'write'):
            t = attrs.get('text') or body or ''
            exp_t = attrs.get('target') or attrs.get('expected_target') or action.get('expected_target')
            return self.send_type(t, expected_target=exp_t)

        elif act_type in ('lock', 'lockscreen'):
            return self.lock_screen()

        elif act_type in ('mirror', 'phone', 'scrcpy'):
            turn_screen_off = str(attrs.get('turn_screen_off', '')).lower() in ('true', '1', 'yes')
            return self.start_phone_mirror(turn_screen_off=turn_screen_off)

        elif act_type in ('hotspot', 'wifi_hotspot'):
            state = attrs.get('state') or body or 'on'
            enable = str(state).lower().strip() not in ('off', 'stop', 'disable', 'false', '0', 'down')
            return self.toggle_hotspot(enable=enable)

        elif act_type == 'browser':
            return self.execute_browser_action(action)

        else:
            return {"success": False, "message": f"Unknown action type: {act_type}"}

    _ALLOWED_BROWSER_ACTION_ATTRS = frozenset({"type", "command", "query"})
    _SUPPORTED_BROWSER_COMMANDS = frozenset({"youtube_play_fullscreen"})
    _FORBIDDEN_BROWSER_KEYS = frozenset({
        "js", "javascript", "script", "code", "eval", "function", "fn",
        "shell", "cmd", "exec", "html", "url", "href", "onclick", "onload"
    })
    _FORBIDDEN_BROWSER_QUERY_RE = re.compile(
        r"(?:javascript\s*:|data\s*:\s*text/html|<\s*/?\s*script\b|\beval\s*\("
        r"|\bnew\s+Function\b|\bFunction\s*\(|\bdocument\s*\.|\bwindow\s*\."
        r"|\bchrome\s*\.|\bglobalThis\s*\.|=>|[;{}])",
        re.IGNORECASE,
    )

    @classmethod
    def validate_browser_action(cls, action):
        """Validate a `<action type="browser" ...>` tag or payload dict.
        Rejects unknown commands, malformed payloads, unexpected attributes, and any JS/code strings.
        Returns `(is_valid: bool, payload_or_error: dict)`.
        """
        if not isinstance(action, dict):
            return False, {"reason": "Malformed browser action: expected dict"}

        if "attrs" in action or "type" in action:
            attrs = action.get("attrs")
            if not isinstance(attrs, dict):
                return False, {"reason": "Malformed browser action attributes"}
            body = (action.get("body") or "").strip()

            for k in attrs.keys():
                k_low = str(k).lower().strip()
                if k_low in cls._FORBIDDEN_BROWSER_KEYS:
                    return False, {"reason": f"Rejected browser action: forbidden executable attribute '{k}'"}
                if k_low not in cls._ALLOWED_BROWSER_ACTION_ATTRS:
                    return False, {"reason": f"Malformed browser action: unexpected attribute '{k}'"}

            if body and "query" in attrs:
                return False, {"reason": "Malformed browser action: unexpected body alongside 'query' attribute"}
            if body and cls._FORBIDDEN_BROWSER_QUERY_RE.search(body):
                return False, {"reason": "Rejected browser action: JavaScript or code content in action body"}

            raw_payload = {
                "command": attrs.get("command"),
                "query": attrs.get("query") if "query" in attrs else body,
            }
        else:
            raw_payload = dict(action)

        if lexi_browser_host is not None:
            return lexi_browser_host.validate_native_request(raw_payload)

        for k in raw_payload.keys():
            k_str = str(k)
            if k_str.lower() in cls._FORBIDDEN_BROWSER_KEYS:
                return False, {"reason": f"Rejected payload: forbidden executable field '{k_str}'"}
            if k_str not in ("command", "query", "request_id"):
                return False, {"reason": f"Malformed payload: unexpected field '{k_str}'"}

        cmd = raw_payload.get("command")
        if not isinstance(cmd, str) or cmd.strip() not in cls._SUPPORTED_BROWSER_COMMANDS:
            return False, {"reason": f"Unsupported browser command: {cmd!r}"}

        query = raw_payload.get("query")
        if not isinstance(query, str) or not (1 <= len(query.strip()) <= 200):
            return False, {"reason": "Malformed payload: 'query' must be a non-empty string (1..200 chars)"}
        q_clean = query.strip()
        if cls._FORBIDDEN_BROWSER_QUERY_RE.search(q_clean) or re.search(r"[\x00-\x1f\x7f]", q_clean):
            return False, {"reason": "Rejected payload: JavaScript or control characters are not permitted"}

        return True, {"command": cmd.strip(), "query": q_clean}

    @staticmethod
    def format_browser_action_summary(action, result):
        """Build a truthful user-facing summary strictly from structured `*_verified` flags.
        Never claims playback unless `playing_verified` is True, and never claims fullscreen
        unless `fullscreen_verified` is True.
        """
        result = result if isinstance(result, dict) else {}
        attrs = (action.get("attrs") or {}) if isinstance(action, dict) else {}
        query = (attrs.get("query") or (action.get("body") if isinstance(action, dict) else "") or "").strip()

        if result.get("rejected"):
            reason = result.get("reason") or result.get("message") or "invalid browser command"
            if "fullscreen is not verified" in reason.lower():
                return reason
            return f"Rejected browser action ({reason}); fullscreen is not verified."

        search_v = bool(result.get("search_verified") is True)
        watch_v = bool(result.get("watch_verified") is True)
        playing_v = bool(result.get("playing_verified") is True) and search_v and watch_v
        fullscreen_v = bool(result.get("fullscreen_verified") is True) and search_v and watch_v
        title = (result.get("title") or query or "YouTube video").strip()
        reason = (result.get("reason") or "").strip()

        if not search_v:
            detail = reason or f"no suitable video result found for '{query or title}'"
            return (
                f"YouTube search failed ({detail}); "
                f"playback is not verified and fullscreen is not verified."
            )

        if not watch_v:
            detail = reason or "watch page and video element could not be verified"
            return (
                f"Found '{title}' on YouTube search, but watch page is not verified ({detail}); "
                f"playback is not verified and fullscreen is not verified."
            )

        play_clause = (
            f"Playing '{title}' on YouTube"
            if playing_v
            else f"Opened '{title}' on YouTube (playback is not verified)"
        )
        fs_clause = "fullscreen is verified" if fullscreen_v else "fullscreen is not verified"
        reason_suffix = f" ({reason})" if (reason and not (playing_v and fullscreen_v)) else ""
        return f"{play_clause}; {fs_clause}{reason_suffix}."

    def execute_browser_action(self, action):
        """Validate and execute a structured `<action type="browser" ...>` command via the
        Chrome Native Messaging bridge (`lexi_browser_host`)."""
        is_valid, validated = self.validate_browser_action(action)
        if not is_valid:
            reason = validated.get("reason") or "Invalid browser action payload"
            rejected_res = {
                "success": False,
                "rejected": True,
                "verified": False,
                "search_verified": False,
                "watch_verified": False,
                "playing_verified": False,
                "fullscreen_verified": False,
                "title": "",
                "url": "",
                "reason": reason,
            }
            rejected_res["message"] = self.format_browser_action_summary(action, rejected_res)
            return rejected_res

        structured_json = {
            "command": validated["command"],
            "query": validated["query"],
        }
        if validated.get("request_id"):
            structured_json["request_id"] = validated["request_id"]

        try:
            if callable(self.browser_bridge):
                raw_resp = self.browser_bridge({
                    "command": structured_json["command"],
                    "query": structured_json["query"],
                })
            elif lexi_browser_host is not None:
                raw_resp = lexi_browser_host.send_browser_command(
                    structured_json,
                    auto_wake_browser=True,
                )
            else:
                raw_resp = {
                    "success": False,
                    "search_verified": False,
                    "watch_verified": False,
                    "playing_verified": False,
                    "fullscreen_verified": False,
                    "title": "",
                    "url": "",
                    "reason": "Native browser bridge module is unavailable",
                }
        except Exception as exc:
            raw_resp = {
                "success": False,
                "search_verified": False,
                "watch_verified": False,
                "playing_verified": False,
                "fullscreen_verified": False,
                "title": "",
                "url": "",
                "reason": f"Browser bridge error: {exc}",
            }

        if lexi_browser_host is not None:
            norm = lexi_browser_host.normalize_native_response(raw_resp)
        else:
            norm = dict(raw_resp) if isinstance(raw_resp, dict) else {"success": False}

        search_v = bool(norm.get("search_verified") is True)
        watch_v = bool(norm.get("watch_verified") is True) and search_v
        playing_v = bool(norm.get("playing_verified") is True) and watch_v
        fullscreen_v = bool(norm.get("fullscreen_verified") is True) and watch_v
        fully_verified = bool(norm.get("success") and search_v and watch_v and playing_v and fullscreen_v)

        norm.update({
            "search_verified": search_v,
            "watch_verified": watch_v,
            "playing_verified": playing_v,
            "fullscreen_verified": fullscreen_v,
            "verified": fully_verified,
        })
        norm["message"] = self.format_browser_action_summary(action, norm)
        return norm

    _CLOSE_LABELS = {"whatsapp": "WhatsApp", "instagram": "Instagram"}

    @classmethod
    def format_close_tabs_summary(cls, result):
        """Truthful one-line summary built only from the bridge's structured counts."""
        result = result if isinstance(result, dict) else {}
        reason = (result.get("reason") or "").strip()
        if not result.get("success"):
            return reason or "the browser bridge didn't confirm that anything was closed."
        per = result.get("per_target") or {}
        parts, none_open = [], []
        for site in ("whatsapp", "instagram"):
            entry = per.get(site)
            if not entry:
                continue
            label = cls._CLOSE_LABELS[site]
            n = int(entry.get("closed", 0))
            if int(entry.get("matched", 0)) == 0:
                none_open.append(label)
            elif n:
                parts.append(f"{n} {label} tab" + ("" if n == 1 else "s"))
        if not per:  # extension gave no breakdown: fall back to the totals
            n = int(result.get("closed_count", 0))
            label = cls._CLOSE_LABELS.get(result.get("target"), "matching")
            if n:
                parts.append(f"{n} {label} tab" + ("" if n == 1 else "s"))
            else:
                none_open.append(label)
        if parts:
            text = "Closed " + " and ".join(parts) + "."
            if none_open:
                text += f" No {' or '.join(none_open)} tab was open."
            return text
        return f"There's no {' or '.join(none_open) or 'matching'} tab open, so there was nothing to close."

    def execute_close_tabs(self, target):
        """Ask the Chrome extension to close tabs for an approved site enum.

        Only `whatsapp`, `instagram` or `both` are ever sent; no URL, selector or
        script. Never auto-launches a browser. Success requires the extension's
        own re-check to report zero leftover tabs.
        """
        if target not in ("whatsapp", "instagram", "both"):
            return {"success": False, "verified": False, "message": "That isn't a site I can close."}
        payload = {"command": "close_named_tabs", "target": target}
        try:
            if callable(self.browser_bridge):
                raw_resp = self.browser_bridge(dict(payload))
            elif lexi_browser_host is not None:
                raw_resp = lexi_browser_host.send_browser_command(payload, auto_wake_browser=False)
            else:
                raw_resp = {"success": False, "reason": "Native browser bridge module is unavailable"}
        except Exception as exc:
            raw_resp = {"success": False, "reason": f"Browser bridge error: {exc}"}

        if lexi_browser_host is not None:
            norm = lexi_browser_host.normalize_close_response(raw_resp, target)
        else:
            norm = dict(raw_resp) if isinstance(raw_resp, dict) else {"success": False}
        norm["success"] = bool(norm.get("success") is True)
        norm["verified"] = bool(norm["success"] and norm.get("verified") is True)
        norm["message"] = self.format_close_tabs_summary(norm)
        return norm

    @staticmethod
    def _extract_action_target(act):
        attrs = act.get('attrs', {}) or {}
        body = (act.get('body') or '').strip()
        return (attrs.get('target') or attrs.get('app') or attrs.get('url') or body).strip()

    @classmethod
    def _is_browser_or_media_open_target(cls, target):
        t = (target or "").lower().strip()
        if not t:
            return False
        if re.match(r'^(https?://|www\.|spotify:)', t, re.I):
            return True
        return any(k in t for k in (
            "youtube", "chrome", "chromium", "firefox", "brave", "browser", "spotify", "mpv", "vlc"
        ))

    def has_observation_capability(self):
        """True only when an application-provided observer can verify a GUI outcome.
        A DISPLAY variable or a best-effort X11 query alone is not sufficient proof."""
        return callable(self.state_observer) or callable(self.screenshot_provider)

    def run_unverified_prefix(self, actions):
        """Perform the safe first step of a GUI plan, then stop before blind input.
        This is used when no real screen observer has been connected."""
        first_action = actions[0]
        result = self.execute_action(first_action)
        if result.get("success"):
            target = self._extract_action_target(first_action)
            result.update({
                "verified": False,
                "incomplete": True,
                "verification_message": "Screen verification is unavailable.",
                "message": (
                    f"Opened {target!r}, but stopped before follow-up input: "
                    "screen verification is unavailable."
                ),
            })
        return [(first_action, result)]

    def is_in_bounded_runner_scope(self, actions, user_question=""):
        """P2 Narrow Scope Check: browser navigation and media playback only.
        Excludes typing into messages, WhatsApp, passwords, account changes,
        purchases, file deletion, and phone control."""
        if not actions:
            return False
        if user_question and self._OUT_OF_SCOPE_RUNNER_RE.search(user_question):
            return False

        allowed_types = {'open', 'launch', 'run', 'media', 'player', 'key', 'hotkey'}
        has_browser_or_media = False

        for act in actions:
            act_type = (act.get('type') or 'command').lower().strip()
            if act_type not in allowed_types:
                return False
            attrs = act.get('attrs', {}) or {}
            body = (act.get('body') or '').strip()
            raw_text = f"{body} {' '.join(str(v) for v in attrs.values())}"
            if self._OUT_OF_SCOPE_RUNNER_RE.search(raw_text):
                return False
            if act_type in ('open', 'launch', 'run'):
                target = self._extract_action_target(act)
                if self._is_browser_or_media_open_target(target):
                    has_browser_or_media = True
                else:
                    return False
            elif act_type in ('media', 'player'):
                has_browser_or_media = True

        return has_browser_or_media

    def build_bounded_plan(self, actions, user_question=""):
        """Convert a browser/media goal into a structured plan of at most 3 steps,
        each declaring an expected observable condition."""
        bounded = list(actions[:self.MAX_BOUNDED_STEPS])
        plan = []
        opened_youtube = False

        for idx, act in enumerate(bounded, 1):
            act_type = (act.get('type') or 'command').lower().strip()
            attrs = act.get('attrs', {}) or {}
            body = (act.get('body') or '').strip()

            explicit_cond = act.get('expected_condition') or attrs.get('expect')
            if isinstance(explicit_cond, dict):
                cond = {
                    "kind": explicit_cond.get("kind", "custom"),
                    "target": explicit_cond.get("target", ""),
                    "description": explicit_cond.get("description", "declared condition is met"),
                }
            elif isinstance(explicit_cond, str) and explicit_cond.strip():
                c_low = explicit_cond.strip().lower()
                if "watch" in c_low and "youtube" in c_low:
                    cond = {"kind": "youtube_watch_page", "target": "youtube", "description": explicit_cond.strip()}
                elif "fullscreen" in c_low:
                    cond = {"kind": "fullscreen_active", "target": "fullscreen", "description": explicit_cond.strip()}
                else:
                    cond = {"kind": "custom_text", "target": explicit_cond.strip(), "description": explicit_cond.strip()}
            elif act_type in ('open', 'launch', 'run'):
                target = self._extract_action_target(act)
                t_low = target.lower()
                if "youtube.com" in t_low or "youtu.be" in t_low:
                    opened_youtube = True
                    if "youtube.com/watch" in t_low or "youtu.be/" in t_low:
                        cond = {"kind": "youtube_watch_page", "target": "youtube", "description": "a YouTube watch page is visible"}
                    else:
                        cond = {"kind": "browser_page_visible", "target": "youtube", "description": "YouTube page is visible in the browser"}
                elif re.match(r'^(https?://|www\.)', target, re.I):
                    parsed_u = urllib.parse.urlparse(target if '://' in target else f"https://{target}")
                    host = parsed_u.netloc or target
                    cond = {"kind": "browser_page_visible", "target": host, "description": f"browser page for {host} is visible"}
                elif "spotify:" in t_low or "open.spotify.com/" in t_low:
                    cond = {"kind": "media_playing", "target": "spotify", "description": "Spotify playback is active"}
                else:
                    cond = {"kind": "app_ready", "target": target, "description": f"{target} is ready"}
            elif act_type in ('key', 'hotkey'):
                k_val = (attrs.get('key') or body or '').strip()
                k_low = k_val.lower()
                if k_low in ('return', 'enter') and opened_youtube:
                    cond = {"kind": "youtube_watch_page", "target": "youtube", "description": "a YouTube watch page is visible"}
                elif k_low == 'f':
                    cond = {"kind": "fullscreen_active", "target": "fullscreen", "description": "the player is fullscreen"}
                elif k_low in ('space', 'k'):
                    cond = {"kind": "media_state_changed", "target": "media", "description": "media playback state responded"}
                else:
                    cond = {"kind": "target_focused_no_blocker", "target": k_low, "description": f"target window accepted key '{k_val}' with no modal blocker"}
            elif act_type in ('media', 'player'):
                m_cmd = (attrs.get('command') or attrs.get('cmd') or body or 'play-pause').lower().strip()
                if any(w in m_cmd for w in ('pause', 'stop', 'hold')):
                    cond = {"kind": "media_paused_or_stopped", "target": "media", "description": "media playback is paused or stopped"}
                else:
                    cond = {"kind": "media_playing", "target": "media", "description": "media playback is active"}
            else:
                cond = {"kind": "command_succeeded", "target": "command", "description": "command exited with code 0 and no blocker"}

            if act_type in ('open', 'launch', 'run'):
                planned_desc = f"Open {self._extract_action_target(act)}"
            elif act_type in ('key', 'hotkey'):
                planned_desc = f"Send key '{attrs.get('key') or body}'"
            elif act_type in ('media', 'player'):
                planned_desc = f"Media control '{attrs.get('command') or attrs.get('cmd') or body or 'play-pause'}'"
            else:
                planned_desc = f"Run {act_type}: {body or attrs.get('cmd', '')}"

            plan.append({
                "step": idx,
                "action": act,
                "planned_step": planned_desc,
                "expected_condition": cond,
            })

        return plan

    def verify_step_condition(self, condition, pre_state, post_state, action_result):
        """Observe and verify only the declared step condition against post_state."""
        if post_state.get("blocker"):
            return False, f"Blocker detected: {post_state['blocker']}"

        if post_state.get("condition_met") is True:
            return True, post_state.get("verification_detail") or f"Verified: {condition['description']}"
        if post_state.get("condition_met") is False:
            return False, post_state.get("verification_detail") or f"Unverified: {condition['description']}"

        kind = condition.get("kind", "")
        desc = condition.get("description", "declared condition")
        hints = post_state.get("screen_text_hints") or []
        url_low = (post_state.get("url") or "").lower()
        mpris_url_low = (post_state.get("mpris_url") or "").lower()
        title_low = (post_state.get("active_title") or "").lower()
        page_type = (post_state.get("page_type") or "").lower()

        if kind == "youtube_watch_page":
            if page_type == "youtube_search" or "youtube_search" in hints or "youtube.com/results" in url_low:
                return False, "YouTube is still on the search page (watch page not visible)"
            if (
                page_type == "youtube_watch"
                or "youtube_watch" in hints
                or "youtube.com/watch" in url_low
                or "youtu.be/" in url_low
                or "youtube.com/watch" in mpris_url_low
                or "youtu.be/" in mpris_url_low
                or (post_state.get("media_status") == "Playing" and ("youtube" in title_low or post_state.get("media_title")))
            ):
                return True, f"Verified: {desc}"
            return False, f"Could not verify that {desc}"

        if kind == "fullscreen_active":
            if post_state.get("is_fullscreen") is True or "fullscreen" in hints:
                return True, f"Verified: {desc}"
            return False, f"Could not verify that {desc}"

        if kind == "browser_page_visible":
            if post_state.get("page_verified") is False:
                return False, post_state.get("verification_detail") or f"Could not verify that {desc}"
            target_tok = (condition.get("target") or "").lower().split(".")[0]
            combined_surface = f"{title_low} {url_low} {mpris_url_low} {' '.join(hints)} {page_type}"
            if (
                post_state.get("page_verified") is True
                or (target_tok and target_tok in combined_surface)
                or (post_state.get("target_ready") is True and not title_low)
            ):
                return True, f"Verified: {desc}"
            return False, f"Could not verify that {desc}"

        if kind == "media_playing":
            if post_state.get("media_status") == "Playing" or action_result.get("spotify_playing") is True:
                return True, f"Verified: {desc}"
            return False, f"Could not verify that {desc}"

        if kind == "media_paused_or_stopped":
            if post_state.get("media_status") in ("Paused", "Stopped"):
                return True, f"Verified: {desc}"
            return False, f"Could not verify that {desc}"

        if kind == "media_state_changed":
            if post_state.get("media_status") and post_state.get("media_status") != pre_state.get("media_status"):
                return True, f"Verified: {desc} ({post_state.get('media_status')})"
            if post_state.get("media_status") in ("Playing", "Paused"):
                return True, f"Verified: {desc} ({post_state.get('media_status')})"
            return False, f"Could not verify that {desc}"

        if kind == "app_ready":
            if post_state.get("target_ready") is True:
                return True, f"Verified: {desc}"
            return False, f"Could not verify that {desc}"

        if kind == "command_succeeded":
            if action_result.get("success") is True and action_result.get("returncode", 0) == 0:
                return True, f"Verified: {desc}"
            return False, f"Command failed to verify: {desc}"

        if kind == "target_focused_no_blocker":
            if post_state.get("target_ready") is True and not post_state.get("blocker"):
                return True, f"Verified: {desc}"
            return False, f"Could not verify that {desc}"

        if kind == "custom_text":
            target_low = (condition.get("target") or "").lower()
            combined_surface = f"{title_low} {url_low} {mpris_url_low} {' '.join(hints)} {page_type}"
            if target_low and target_low in combined_surface:
                return True, f"Verified: {desc}"
            return False, f"Could not verify that {desc}"

        return False, f"Could not verify condition: {desc}"

    @staticmethod
    def _describe_attempted_action(act, res, attempts):
        act_type = (act.get('type') or 'command').lower().strip()
        attrs = act.get('attrs', {}) or {}
        body = (act.get('body') or '').strip()
        tool = res.get('injection_tool') or res.get('tool')
        tool_suffix = f" via {tool}" if tool else ""
        retry_suffix = f" ({attempts} attempts)" if attempts > 1 else ""
        if act_type in ('key', 'hotkey'):
            return f"key '{attrs.get('key') or body}'{tool_suffix}{retry_suffix}"
        if act_type in ('open', 'launch', 'run'):
            t = attrs.get('target') or attrs.get('app') or attrs.get('url') or body
            return f"open '{t}'{retry_suffix}"
        if act_type in ('media', 'player'):
            c = attrs.get('command') or attrs.get('cmd') or body or 'play-pause'
            return f"media '{c}'{retry_suffix}"
        return f"{act_type}{tool_suffix}{retry_suffix}"

    def run_bounded_plan(self, actions, user_question=""):
        """P2 Explicit Bounded Act -> Observe -> Decide Runner.
        - Converts goal into at most 3 steps with declared observable conditions.
        - Executes 1 step at a time, then observes and verifies only the declared condition.
        - Retries an unverified non-blocked, non-risky action at most once.
        - Stops immediately on blocker or unverified condition and asks the user.
        - Records planned step, attempted action, observed result, and final status."""
        plan = self.build_bounded_plan(actions, user_question=user_question)
        activity_record = []
        results = []
        last_opened_target = None

        for step_spec in plan:
            step_num = step_spec["step"]
            act = step_spec["action"]
            act_type = (act.get("type") or "command").lower().strip()
            cond = step_spec["expected_condition"]
            target_hint = cond.get("target") or last_opened_target

            # P1: Before follow-up step after opening a URL/app, wait for target readiness & check blockers
            if last_opened_target and act_type in ("key", "hotkey", "type", "write", "media", "player"):
                ready, pre_state = self.wait_for_target_ready(last_opened_target)
            else:
                pre_state = self.inspect_desktop_state(expected_target=target_hint, include_subprocesses=True)

            if pre_state.get("blocker"):
                blocker = pre_state["blocker"]
                obs_msg = f"Blocker visible: {blocker}"
                activity_record.append({
                    "step": step_num,
                    "planned_step": step_spec["planned_step"],
                    "expected_condition": cond["description"],
                    "attempted_action": "none (stopped before execution)",
                    "attempts": 0,
                    "observed_result": obs_msg,
                    "final_status": "blocked",
                })
                blocked_res = {
                    "success": False,
                    "attempted": False,
                    "blocked": True,
                    "blocker": blocker,
                    "injection_tool": None,
                    "tool": None,
                    "verified": False,
                    "verification_message": obs_msg,
                    "message": f"Stopped before step {step_num} ({step_spec['planned_step']}): {blocker} is waiting for your input, boss.",
                    "activity_record": list(activity_record),
                }
                results.append((act, blocked_res))
                break

            # Execute exactly 1 step (Attempt 1)
            attempts = 1
            res = self.execute_action(act)

            if res.get("declined"):
                obs_msg = "User declined confirmation dialog"
                activity_record.append({
                    "step": step_num,
                    "planned_step": step_spec["planned_step"],
                    "expected_condition": cond["description"],
                    "attempted_action": "declined by user",
                    "attempts": attempts,
                    "observed_result": obs_msg,
                    "final_status": "declined",
                })
                res["verified"] = False
                res["verification_message"] = obs_msg
                res["activity_record"] = list(activity_record)
                results.append((act, res))
                break

            if res.get("blocked") or not res.get("success", False):
                obs_msg = res.get("verification_message") or res.get("message") or "Step execution failed"
                status_tag = "blocked" if res.get("blocked") else "failed"
                activity_record.append({
                    "step": step_num,
                    "planned_step": step_spec["planned_step"],
                    "expected_condition": cond["description"],
                    "attempted_action": self._describe_attempted_action(act, res, attempts),
                    "attempts": attempts,
                    "observed_result": obs_msg,
                    "final_status": status_tag,
                })
                res["verified"] = False
                res["activity_record"] = list(activity_record)
                results.append((act, res))
                break

            if act_type in ("open", "launch", "run"):
                last_opened_target = self._extract_action_target(act)

            if self.step_settle_delay > 0:
                time.sleep(self.step_settle_delay)

            post_state = self.inspect_desktop_state(expected_target=target_hint, include_subprocesses=True)
            verified, obs_msg = self.verify_step_condition(cond, pre_state, post_state, res)

            # Bounded retry: at most ONCE without user input, never on modal blocker or risky command
            is_risky_step = act_type in ("command", "bash", "sh", "shell", "terminal") and is_risky_command(
                (act.get("body") or act.get("attrs", {}).get("cmd") or "")
            )
            if not verified and not post_state.get("blocker") and not is_risky_step:
                attempts = 2
                res_retry = self.execute_action(act)
                res = res_retry
                if res.get("blocked") or not res.get("success", False):
                    verified = False
                    obs_msg = res.get("verification_message") or res.get("message") or "Retry failed"
                else:
                    if self.step_settle_delay > 0:
                        time.sleep(self.step_settle_delay)
                    post_state = self.inspect_desktop_state(expected_target=target_hint, include_subprocesses=True)
                    verified, obs_msg = self.verify_step_condition(cond, pre_state, post_state, res)

            if verified:
                res["verified"] = True
                res["verification_message"] = obs_msg
                res["final_status"] = "verified"
                if act_type in ("key", "hotkey", "type", "write"):
                    used_tool = res.get("injection_tool") or res.get("tool") or "input"
                    k_val = (act.get("attrs", {}) or {}).get("key") or act.get("body") or ""
                    res["message"] = f"Input sent via {used_tool}: key '{k_val}' — Goal verified ({cond['description']})"
                activity_record.append({
                    "step": step_num,
                    "planned_step": step_spec["planned_step"],
                    "expected_condition": cond["description"],
                    "attempted_action": self._describe_attempted_action(act, res, attempts),
                    "attempts": attempts,
                    "observed_result": obs_msg,
                    "final_status": "verified",
                })
                results.append((act, res))
            else:
                blocker_label = post_state.get("blocker")
                final_status = "blocked" if blocker_label else "unverified_stopped"
                res["success"] = False
                res["verified"] = False
                res["blocked"] = True
                res["blocker"] = blocker_label or obs_msg
                res["verification_message"] = obs_msg
                res["final_status"] = final_status
                res["message"] = (
                    f"Stopped at step {step_num} ({step_spec['planned_step']}): {obs_msg}. "
                    f"Waiting on your input, boss."
                )
                activity_record.append({
                    "step": step_num,
                    "planned_step": step_spec["planned_step"],
                    "expected_condition": cond["description"],
                    "attempted_action": self._describe_attempted_action(act, res, attempts),
                    "attempts": attempts,
                    "observed_result": obs_msg,
                    "final_status": final_status,
                })
                res["activity_record"] = list(activity_record)
                results.append((act, res))
                break

        self.last_activity_record = list(activity_record)
        if results and "activity_record" not in results[-1][1]:
            results[-1][1]["activity_record"] = list(activity_record)
        return results

    def execute_action_sequence(self, actions, user_question=""):
        """
        Executes a sequence of actions with intelligent chaining, deduplication,
        intent validation, P1 modal/focus guards, and the P2 bounded runner.
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
            # "try again" is an explicit continuation of the immediately prior
            # action, not a casual message.  Do not throw away its repeat action.
            is_retry = bool(re.fullmatch(
                r"(?:please\s+)?(?:(?:open\s+and\s+)?(?:try|retry)(?:\s+again)?|again)",
                q_lower,
            ))
            # “Can you send…” is still an explicit request to send.  Treat it as
            # an instruction; only purely explanatory questions should be blocked.
            is_valid_wa_command = has_send or has_open_app or is_retry

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

        if any((a.get('type') or '').lower().strip() == 'browser' for a in filtered_actions):
            # When using the structured Chrome extension bridge, suppress blind follow-up Return/f keys
            filtered_actions = [
                a for a in filtered_actions
                if (a.get('type') or '').lower().strip() not in ('key', 'hotkey')
            ]

        # Route browser navigation & media playback sequences through the P2 bounded runner
        if self.is_in_bounded_runner_scope(filtered_actions, user_question=user_question):
            is_multi_step = len(filtered_actions) >= 2
            is_single_url_open = (
                len(filtered_actions) == 1
                and filtered_actions[0].get('type', '').lower().strip() in ('open', 'launch', 'run')
                and bool(re.match(r'^(https?://|www\.)', self._extract_action_target(filtered_actions[0]), re.I))
            )
            if (is_multi_step or is_single_url_open) and self.has_observation_capability():
                return self.run_bounded_plan(filtered_actions, user_question=user_question)
            if is_multi_step:
                return self.run_unverified_prefix(filtered_actions)

        spotify_already_playing = False
        last_opened_target = None
        for i, act in enumerate(filtered_actions):
            act_type = act.get('type', '').lower().strip()
            attrs = act.get('attrs', {})
            body = act.get('body', '').strip()
            target = (attrs.get('target', '') or attrs.get('app', '') or attrs.get('url', '') or body).strip()

            # P1/Issue 6 guard for non-runner sequences: wait for target & stop on modal blocker or focus mismatch
            if act_type in ('key', 'hotkey', 'type', 'write'):
                if last_opened_target:
                    act = dict(act, expected_target=last_opened_target)
                    _ready, state = self.wait_for_target_ready(last_opened_target)
                else:
                    state = self.inspect_desktop_state(include_subprocesses=True)
                k_hint = (attrs.get('key') or body or '') if act_type in ('key', 'hotkey') else None
                blocker = state.get("blocker") or self.check_focus_mismatch(
                    state, expected_target=last_opened_target or act.get('expected_target'), key_combo=k_hint
                )
                if blocker:
                    results.append((act, {
                        "success": False,
                        "attempted": False,
                        "blocked": True,
                        "blocker": blocker,
                        "injection_tool": None,
                        "tool": None,
                        "verified": False,
                        "verification_message": f"Blocked by {blocker}; no input sent.",
                        "message": f"Stopped: {blocker}. Outcome could not be verified.",
                    }))
                    break

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
                last_opened_target = target
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
            if act_type in ('open', 'launch', 'run') and res.get('success'):
                last_opened_target = target
            if isinstance(res, dict) and res.get('spotify_playing'):
                spotify_already_playing = True
            results.append((act, res))

            if isinstance(res, dict) and (res.get('declined') or res.get('blocked')):
                break

            # Inter-step delay for desktop compositor and window stabilization
            if i < len(filtered_actions) - 1 and self.step_settle_delay > 0:
                time.sleep(self.step_settle_delay)

        return results


def _terminal_task_result(engine, command, max_chars=1500):
    """Run a user-authorised command and describe ONLY what happened (exit code + output)."""
    res = engine.run_command(command)
    if res.get("blocked") or res.get("rejected"):
        return {"success": False, "verified": False, "message": res.get("message") or res.get("output", "Blocked.")}
    out = (res.get("output") or "").strip()
    if len(out) > max_chars:
        out = out[:max_chars] + "\n…(output truncated)"
    code = res.get("returncode")
    if res.get("success"):
        msg = f"Ran `{command}` (exit 0)." + (f"\n{out}" if out and out != "(Executed with no output)" else "")
        return {"success": True, "verified": True, "message": msg, "returncode": 0}
    msg = f"`{command}` failed (exit {code})." + (f"\n{out}" if out else "")
    return {"success": False, "verified": False, "message": msg, "returncode": code}


def _list_running_apps_exec():
    """Return which known apps are currently running."""
    import lexi_apps
    running = lexi_apps.list_running()
    if not running:
        return {"success": True, "verified": True,
                "message": "None of the apps I know about (Chrome, Spotify, Files, Terminal) are open right now."}
    names = ", ".join(s.label for s in running)
    return {"success": True, "verified": True,
            "message": f"Currently running: {names}."}


def _close_all_apps_exec(engine):
    """Close every known app that is currently running."""
    import lexi_apps
    running = lexi_apps.list_running()
    if not running:
        return {"success": True, "verified": True,
                "message": "Nothing I know about is open — nothing to close."}
    results = []
    for spec in running:
        res = engine.close_app(spec.key)
        results.append((spec.label, res))
    closed = [label for label, r in results if r.get("closed")]
    failed = [label for label, r in results if not r.get("closed") and r.get("was_running")]
    not_open = [label for label, r in results if not r.get("was_running")]
    parts = []
    if closed:
        parts.append(f"Closed: {', '.join(closed)}.")
    if failed:
        parts.append(f"Still open (couldn't close): {', '.join(failed)}.")
    if not_open:
        parts.append(f"Already closed: {', '.join(not_open)}.")
    msg = " ".join(parts)
    success = len(failed) == 0
    return {"success": success, "verified": True, "message": msg}


def build_executors(engine):
    """Map registry executor keys to SystemActionEngine methods.

    Each executor receives schema-validated args and returns the engine's
    result dict ({"success", "verified", "message", ...}).  Nothing here
    accepts shell text, keystrokes, selectors or JavaScript.
    """
    def _volume(a):
        mode = a.get("mode")
        if "percent" in a:
            return engine.set_volume(f"{a['percent']}%")
        if mode in ("mute", "unmute"):
            return engine.set_volume(mode)
        step = a.get("step", 10)
        return engine.set_volume(f"{'+' if mode == 'up' else '-'}{step}")

    def _brightness(a):
        if "percent" in a:
            return engine.set_brightness(f"{a['percent']}%")
        step = a.get("step", 10)
        return engine.set_brightness(f"{'+' if a.get('mode') == 'up' else '-'}{step}")

    def _open(a):
        if "site" in a:
            site = a["site"]
            if not site.startswith(("http://", "https://")):
                site = "https://" + site
            return engine.open_target(site)
        return engine.open_target(a["name"])

    def _spotify(a):
        res = engine.open_target(f"spotify:search:{a['query']}")
        if isinstance(res, dict) and res.get("success"):
            return dict(res, verified=True, message=res.get("message") or f"Playing {a['query']} on Spotify.")
        return res

    def _youtube(a):
        res = engine.execute_browser_action({
            "type": "browser",
            "attrs": {"command": "youtube_play_fullscreen", "query": a["query"]},
            "body": "",
        })
        if not (isinstance(res, dict) and res.get("success")):
            import urllib.parse
            q_enc = urllib.parse.quote_plus(a["query"])
            url = f"https://www.youtube.com/results?search_query={q_enc}"
            subprocess.Popen(['xdg-open', url], start_new_session=True)
            return {"success": True, "verified": True, "message": f"Opened YouTube search for {a['query']}."}
        return res

    return {
        "set_volume": _volume,
        "set_brightness": _brightness,
        "lock_screen": lambda a: engine.lock_screen(),
        "open_target": _open,
        "media_control": lambda a: engine.media_control(a["command"] if a["command"] != "toggle" else "play-pause"),
        "spotify_play": _spotify,
        "youtube_play": _youtube,
        "close_chrome": lambda a: engine.close_chrome(),
        "close_app": lambda a: engine.close_app(a["name"]),
        "terminal_task": lambda a: _terminal_task_result(engine, a["command"]),
        "close_tabs": lambda a: engine.execute_close_tabs(a["target"]),
        "close_tabs_all": lambda a: engine.execute_close_tabs("both"),
        "whatsapp_draft": lambda a: engine.whatsapp_draft(contact=a["contact"], message=a["message"]),
        "whatsapp_send": lambda a: engine.whatsapp_send(contact=a["contact"]),
        "whatsapp_cancel": lambda a: engine.whatsapp_cancel(),
        "list_running_apps": lambda a: _list_running_apps_exec(),
        "close_all_apps": lambda a: _close_all_apps_exec(engine),
        "system_battery": lambda a: engine.get_battery_status(),
        "media_now_playing": lambda a: engine.get_media_status(),
        "take_screenshot": lambda a: engine.take_screenshot(),
        "system_sleep": lambda a: engine.system_sleep(),
        "system_restart": lambda a: engine.system_restart(),
        "system_poweroff": lambda a: engine.system_poweroff(),
        "phone_battery": lambda a: engine.get_phone_battery(),
        "phone_ring": lambda a: engine.ring_phone(),
        "phone_camera_snap": lambda a: engine.trigger_phone_camera_snap(),
        "phone_clipboard_sync": lambda a: engine.sync_clipboard_to_phone(text=a.get("text")),
        "system_resources": lambda a: engine.get_system_resources(),
        "network_status": lambda a: engine.get_network_status(),
        "wifi_list": lambda a: engine.list_wifi_networks(),
        "bluetooth_control": lambda a: engine.control_bluetooth(action=a.get("action", "status")),
        "clipboard_read": lambda a: engine.read_clipboard(),
    }


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


def _render_action_result_core(buffer, action, result):
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
        if result.get('incomplete'):
            buffer.insert_with_tags_by_name(buffer.get_end_iter(), f"ℹ️ {msg}\n", "action_body")
        elif success and not result.get('blocked'):
            buffer.insert_with_tags_by_name(buffer.get_end_iter(), f"✅ {msg}\n", "action_success")
            if result.get('verified') is True and result.get('verification_message'):
                buffer.insert_with_tags_by_name(buffer.get_end_iter(), f"🔍 {result['verification_message']}\n", "action_body")
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
        res_msg = result.get('message', 'WhatsApp message input sent (delivery not verified)')
        if not success or result.get('blocked'):
            buffer.insert_with_tags_by_name(buffer.get_end_iter(), f"❌ {res_msg}\n", "action_error")
        elif result.get('verified') is True:
            buffer.insert_with_tags_by_name(buffer.get_end_iter(), f"✅ {res_msg}\n", "action_success")
        else:
            buffer.insert_with_tags_by_name(buffer.get_end_iter(), f"ℹ️ {res_msg}\n", "action_body")

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
        msg = result.get('message', 'Input sent')
        if not success or result.get('blocked'):
            buffer.insert_with_tags_by_name(buffer.get_end_iter(), f"❌ {msg}\n", "action_error")
        elif result.get('verified') is True:
            buffer.insert_with_tags_by_name(buffer.get_end_iter(), f"✅ {msg}\n", "action_success")
        else:
            buffer.insert_with_tags_by_name(buffer.get_end_iter(), f"ℹ️ {msg}\n", "action_body")

    elif act_type in ('lock', 'lockscreen'):
        buffer.insert_with_tags_by_name(buffer.get_end_iter(), "🔒 Locking session...\n", "action_cmd")
        if success:
            buffer.insert_with_tags_by_name(buffer.get_end_iter(), f"✅ {result.get('message', 'Locked')}\n", "action_success")
        else:
            buffer.insert_with_tags_by_name(buffer.get_end_iter(), f"❌ {result.get('message', 'Lock failed')}\n", "action_error")

    elif act_type == 'browser':
        cmd_name = attrs.get('command') or 'youtube_play_fullscreen'
        q_text = attrs.get('query') or body or ''
        buffer.insert_with_tags_by_name(
            buffer.get_end_iter(),
            f"🌐 Browser ({cmd_name}): {q_text}\n",
            "action_cmd"
        )
        msg = SystemActionEngine.format_browser_action_summary(action, result)
        checks_line = (
            f"🔎 Verified flags: search={bool(result.get('search_verified') is True)}, "
            f"watch={bool(result.get('watch_verified') is True)}, "
            f"playing={bool(result.get('playing_verified') is True)}, "
            f"fullscreen={bool(result.get('fullscreen_verified') is True)}\n"
        )
        buffer.insert_with_tags_by_name(buffer.get_end_iter(), checks_line, "action_body")
        all_verified = bool(
            result.get('success')
            and result.get('search_verified') is True
            and result.get('watch_verified') is True
            and result.get('playing_verified') is True
            and result.get('fullscreen_verified') is True
        )
        if not success or result.get('rejected') or result.get('blocked') or not result.get('search_verified'):
            buffer.insert_with_tags_by_name(buffer.get_end_iter(), f"❌ {msg}\n", "action_error")
        elif all_verified:
            buffer.insert_with_tags_by_name(buffer.get_end_iter(), f"✅ {msg}\n", "action_success")
        else:
            buffer.insert_with_tags_by_name(buffer.get_end_iter(), f"ℹ️ {msg}\n", "action_body")

    else:
        buffer.insert_with_tags_by_name(buffer.get_end_iter(), f"⚙️ Action: {act_type}\n", "action_cmd")
        if success and not result.get('blocked'):
            buffer.insert_with_tags_by_name(buffer.get_end_iter(), f"✅ {result.get('message', 'Action completed')}\n", "action_success")
        else:
            buffer.insert_with_tags_by_name(buffer.get_end_iter(), f"❌ {result.get('message', 'Action failed')}\n", "action_error")

    if result.get('activity_record'):
        buffer.insert_with_tags_by_name(buffer.get_end_iter(), "📋 Activity Record:\n", "action_cmd")
        for rec in result['activity_record']:
            st_icon = "✅" if rec.get('final_status') == 'verified' else ("🛑" if rec.get('final_status') in ('blocked', 'declined') else "⚠️")
            line = (
                f"  {st_icon} Step {rec.get('step')}: {rec.get('planned_step')} | "
                f"Action: {rec.get('attempted_action')} | "
                f"Observed: {rec.get('observed_result')} [{rec.get('final_status')}]\n"
            )
            buffer.insert_with_tags_by_name(buffer.get_end_iter(), line, "action_body")

    buffer.insert(buffer.get_end_iter(), "\n")


def render_action_result_to_buffer(buffer, action, result):
    _render_action_result_core(buffer, action, result)
    if result.get('declined'):
        kind = "declined"
    elif not result.get('success', True) or result.get('blocked') or result.get('rejected'):
        kind = "fail"
    elif (
        result.get('verified') is False
        or result.get('incomplete')
        or (
            (action.get('type') or '').lower().strip() == 'browser'
            and not (
                result.get('search_verified') is True
                and result.get('watch_verified') is True
                and result.get('playing_verified') is True
                and result.get('fullscreen_verified') is True
            )
        )
    ):
        # P0: Do not append a positive success quip to an unverified or incomplete action result.
        buffer.insert(buffer.get_end_iter(), "\n")
        return
    else:
        kind = "success"
    buffer.insert_with_tags_by_name(buffer.get_end_iter(), f"{boss_quip(kind)}\n\n",
                                    "action_success" if kind != "fail" else "action_error")


def summarize_action_outcomes(actions, action_results):
    """Issue 5: Build the final completion line strictly from structured action records,
    suppressing unverified or optimistic claims in raw model prose."""
    actions = list(actions or [])
    action_results = list(action_results or [])
    if not action_results:
        return "No actions were executed."

    opened_target = None
    opened_is_search = False
    wants_fullscreen = any(
        (a.get("type") or "").lower().strip() in ("key", "hotkey")
        and ((a.get("attrs") or {}).get("key") or a.get("body") or "").lower().strip() == "f"
        for a in actions
    ) or any(
        (a.get("type") or "").lower().strip() in ("command", "bash", "sh", "shell", "terminal")
        and "xdotool" in ((a.get("body") or (a.get("attrs") or {}).get("cmd") or "").lower())
        and " key f" in ((a.get("body") or (a.get("attrs") or {}).get("cmd") or "").lower())
        for a in actions
    )

    for act, res in action_results:
        act_type = (act.get("type") or "").lower().strip()
        if act_type in ("open", "launch", "run") and res.get("success"):
            t = SystemActionEngine._extract_action_target(act)
            opened_target = t
            if any(k in t.lower() for k in ("search_query=", "/results", "spotify:search:")):
                opened_is_search = True

    last_act, last_res = action_results[-1]

    # 0. Structured Chrome extension browser action: build strictly from *_verified flags
    for act, res in action_results:
        if (act.get("type") or "").lower().strip() == "browser":
            return SystemActionEngine.format_browser_action_summary(act, res)

    # 1. Rejected command (e.g., raw xdotool/ydotool shell bypass)
    for _act, res in action_results:
        if res.get("rejected"):
            if opened_target:
                open_clause = "Opened the search page" if opened_is_search else f"Opened {opened_target}"
                tail = "fullscreen is not verified" if wants_fullscreen else "raw GUI input command was rejected"
                return f"{open_clause}; {tail}."
            return res.get("message") or "Command rejected: raw GUI input tools are not allowed."

    # 2. Declined confirmation
    for _act, res in action_results:
        if res.get("declined"):
            return "Command not run: confirmation was declined."

    # 3. Blocked by modal/permission dialog or focus mismatch
    for _act, res in action_results:
        if res.get("blocked"):
            blocker = res.get("blocker") or res.get("verification_message") or "a blocker was detected"
            if opened_target:
                open_clause = "Opened the search page" if opened_is_search else f"Opened {opened_target}"
                if wants_fullscreen:
                    return f"{open_clause}; fullscreen is not verified ({blocker})."
                return f"{open_clause}; stopped before follow-up input ({blocker})."
            return res.get("message") or f"Stopped: {blocker}."

    # 4. Incomplete prefix or unverified outcome
    any_unverified = any(
        res.get("incomplete") or res.get("verified") is False or not res.get("success", True)
        for _act, res in action_results
    ) or (len(action_results) < len(actions))

    if any_unverified:
        if opened_target:
            open_clause = "Opened the search page" if opened_is_search else f"Opened {opened_target}"
            if wants_fullscreen:
                return f"{open_clause}; fullscreen is not verified."
            return f"{open_clause}; follow-up outcome is not verified."
        last_type = (last_act.get("type") or "").lower().strip()
        if last_type in ("whatsapp", "wa"):
            return last_res.get("message") or "WhatsApp message input sent; delivery is not verified."
        if last_type in ("key", "hotkey") and wants_fullscreen:
            return "Input sent for 'f'; fullscreen is not verified."
        return last_res.get("message") or "Input sent; outcome is not verified."

    # 5. All planned actions completed and verified
    return last_res.get("message") or "Action completed and verified."


def render_turn_response_to_buffer(buffer, full_text, action_results=None, actions=None):
    """Issue 5: Render a turn's response to buffer.
    If the response contains <action> tags, quarantine/suppress model prose claims
    and build the completion headline strictly from structured action records."""
    engine = SystemActionEngine.get()
    parsed_actions = list(actions) if actions is not None else engine.parse_actions(full_text or "")
    results_list = list(action_results or [])

    if parsed_actions or results_list:
        summary_line = summarize_action_outcomes(parsed_actions, results_list)
        render_markdown_to_buffer(buffer, summary_line)
        for act, res in results_list:
            render_action_result_to_buffer(buffer, act, res)
        return summary_line

    clean_text = engine.strip_actions(full_text or "")
    if clean_text:
        render_markdown_to_buffer(buffer, clean_text)
    return clean_text


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
        actions_box.append(make_btn("⚡ Quick", "island-action-btn island-btn-secondary", "Quick inline copilot query from notch", self.toggle_notch_chat))
        actions_box.append(make_btn("⚡ Beam", "island-action-btn island-btn-secondary", "Local Wi-Fi file sharing & PWA status", self.on_show_beam_info))
        actions_box.append(make_btn("📱 Phone", "island-action-btn island-btn-secondary", "Mirror & control Android phone", self.on_trigger_phone_mirror))
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

        # 7. Quick Notch Chat Revealer (Inline mini copilot prompt)
        self.notch_chat_revealer = Gtk.Revealer()
        self.notch_chat_revealer.set_transition_type(Gtk.RevealerTransitionType.SLIDE_LEFT)
        self.notch_chat_revealer.set_transition_duration(200)
        self.notch_chat_revealer.set_reveal_child(False)
        self.pill.append(self.notch_chat_revealer)

        self.notch_chat_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.notch_chat_box.set_valign(Gtk.Align.CENTER)
        self.notch_chat_revealer.set_child(self.notch_chat_box)

        self.notch_entry = Gtk.Entry()
        self.notch_entry.set_placeholder_text("Ask Lexi or command (battery, ring, wifi)...")
        self.notch_entry.set_size_request(240, 26)
        self.notch_entry.add_css_class('island-notch-entry')
        self.notch_entry.connect('activate', self.on_notch_chat_submit)
        self.notch_chat_box.append(self.notch_entry)

        notch_close_btn = Gtk.Button(label="✕")
        notch_close_btn.set_focusable(False)
        notch_close_btn.add_css_class('island-action-btn')
        notch_close_btn.connect('clicked', lambda *_: self.close_notch_chat())
        self.notch_chat_box.append(notch_close_btn)

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
                self.drop_server.on_clipboard_received = lambda txt: GLib.idle_add(
                    self.on_clipboard_from_mobile, txt
                )
                self.drop_server.on_camera_snap_received = lambda fp: GLib.idle_add(
                    self.on_camera_snap_from_mobile, fp
                )
                self.drop_server.on_peer_accepted_download = lambda fname, peer: GLib.idle_add(
                    self.trigger_island_alert, "⚡", f"{peer} accepted {fname[:14]}!", "green", 4.0
                )
                self.drop_server.on_peer_declined_transfer = lambda fname, peer: GLib.idle_add(
                    self.on_p2p_declined_by_peer, fname, peer
                )
                self.drop_server.start_background()
            except Exception as e:
                log(f"Failed to start Lexi Drop server: {e}")

        self._last_synced_clipboard = ""
        self.connect('realize', self.on_realize)

    def update_input_region(self, mode="compact"):
        surface = self.get_surface()
        if not surface:
            return
        try:
            if mode == "convert":
                rect = cairo.RectangleInt(40, 2, 600, 48)
            elif mode == "notch_chat":
                rect = cairo.RectangleInt(80, 2, 520, 48)
            elif mode == "actions":
                rect = cairo.RectangleInt(90, 2, 500, 48)
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
        if self.current_mode in ("convert", "notch_chat"):
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
        if self.current_mode in ("convert", "notch_chat"):
            return
        self.pill.remove_css_class('dynamic-island-expanded')
        if self.current_mode == "media":
            self.media_controls_revealer.set_reveal_child(False)
            GLib.timeout_add(220, lambda: self.update_input_region(mode="media_compact"))
        elif self.current_mode == "compact":
            self.actions_revealer.set_reveal_child(False)
            GLib.timeout_add(220, lambda: self.update_input_region(mode="compact"))

    def toggle_notch_chat(self):
        if self.notch_chat_revealer.get_reveal_child():
            self.close_notch_chat()
        else:
            self.open_notch_chat()

    def open_notch_chat(self):
        self.current_mode = "notch_chat"
        self.actions_revealer.set_reveal_child(False)
        self.notch_chat_revealer.set_reveal_child(True)
        self.pill.add_css_class('dynamic-island-expanded')
        self.update_input_region(mode="notch_chat")
        GLib.idle_add(lambda: (self.notch_entry.grab_focus(), GLib.SOURCE_REMOVE)[1])

    def close_notch_chat(self):
        self.notch_chat_revealer.set_reveal_child(False)
        self.current_mode = "compact"
        self.pill.remove_css_class('dynamic-island-expanded')
        self.update_input_region(mode="compact")

    def on_notch_chat_submit(self, entry):
        text = entry.get_text().strip()
        if not text:
            return
        entry.set_text("")
        self.close_notch_chat()

        from lexi_intents import route, ACTION, REPLY
        res = route(text)
        if res.kind == ACTION:
            eng = SystemActionEngine.get()
            def _run():
                exec_res = eng.execute_action(res.capability, **(res.args or {}))
                msg = exec_res.get("message", "Done!")
                GLib.idle_add(self.trigger_island_alert, "⚡", msg[:60], "purple", 4.0)
            threading.Thread(target=_run, daemon=True).start()
        elif res.kind == REPLY:
            self.trigger_island_alert("✨", res.text[:60], "purple", 4.0)
        else:
            self.on_expand_copilot()
            if hasattr(self, 'copilot_win') and self.copilot_win:
                self.copilot_win.entry.set_text(text)
                self.copilot_win.on_submit()

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
        try:
            clipboard = Gdk.Display.get_default().get_clipboard()
            clipboard.connect('changed', self.on_desktop_clipboard_changed)
        except Exception:
            pass
        GLib.timeout_add(50, self.position_at_top)

    def on_trigger_phone_mirror(self, *_):
        res = SystemActionEngine.get().start_phone_mirror()
        if not res.get("success"):
            self.trigger_island_alert("📱", "Plug phone via USB or turn on USB Debugging", "amber", 3.5)
            qr_win = LexiQrPopoverWindow(title="📱 Connect Android Phone")
            qr_win.present()
        else:
            self.trigger_island_alert("📱", "Mirroring Phone @ 60 FPS", "green", 3.0)

    def on_clipboard_from_mobile(self, text):
        if not text:
            return
        self._last_synced_clipboard = text
        try:
            clipboard = Gdk.Display.get_default().get_clipboard()
            clipboard.set(text)
        except Exception:
            pass
        if shutil.which('wl-copy'):
            try:
                subprocess.Popen(['wl-copy'], stdin=subprocess.PIPE).communicate(input=text.encode('utf-8'))
            except Exception:
                pass
        elif shutil.which('xclip'):
            try:
                subprocess.Popen(['xclip', '-selection', 'clipboard'], stdin=subprocess.PIPE).communicate(input=text.encode('utf-8'))
            except Exception:
                pass
        preview = text[:24].replace('\n', ' ') + ('…' if len(text) > 24 else '')
        self.trigger_island_alert("📋", f"Synced: {preview}", "cyan", 3.0)

    def on_camera_snap_from_mobile(self, file_path):
        if not file_path or not os.path.exists(file_path):
            return
        if self.copilot_win:
            self.copilot_win.add_feed_file(file_path)
            self.on_expand_copilot()
        self.trigger_island_alert("📸", "Continuity Camera Snap Added!", "green", 3.5)

    def on_desktop_clipboard_changed(self, clipboard):
        try:
            clipboard.read_text_async(None, self._on_desktop_clip_read)
        except Exception:
            pass

    def _on_desktop_clip_read(self, clip, res):
        try:
            text = clip.read_text_finish(res)
            if text and text != getattr(self, '_last_synced_clipboard', ''):
                self._last_synced_clipboard = text
                if self.drop_server:
                    self.drop_server.broadcast_clipboard(text)
        except Exception:
            pass

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
            phone_peers = [peer for peer in peers if peer.get("type") == "phone"]
            target_peer = phone_peers[0] if phone_peers else peers[0]

            peer_label = f"⚡ Beam to {target_peer['device']}"
            p_btn = Gtk.Button(label=peer_label)
            p_btn.set_focusable(False)
            p_btn.add_css_class('island-beam-btn')
            p_btn.set_tooltip_text(f"AirDrop directly to {target_peer['device']} ({target_peer['ip']})")
            p_btn.connect('clicked', lambda _, pip=target_peer['ip'], fp=p: self.on_send_direct_to_peer(pip, fp))
            self.convert_box.append(p_btn)

            if len(peers) > 1:
                hub_btn = Gtk.Button(label=f"📡 Hub ({len(peers)})")
                hub_btn.set_focusable(False)
                hub_btn.add_css_class('island-convert-btn')
                hub_btn.set_tooltip_text("View all nearby AirDrop peers and QR code")
                hub_btn.connect('clicked', lambda *_: self.on_show_qr_for_file(p))
                self.convert_box.append(hub_btn)
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
        if hasattr(self, 'actions_revealer'):
            self.actions_revealer.set_reveal_child(False)
        self.update_input_region(mode="compact")
        beam_win = LexiBeamWindow()
        beam_win.present()

    def on_show_qr_for_file(self, file_path=None):
        target_url = None
        if file_path and getattr(self, 'drop_server', None):
            info = self.drop_server.share_file(file_path)
            target_url = info.get("url")
        self.hide_converter_bar()
        beam_win = LexiBeamWindow(target_url=target_url, file_path=file_path)
        beam_win.present()

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
            method = res.get("method")
            if method == "adb":
                dev = res.get("device", "Phone")
                lbl = Gtk.Label(label=f"✓ Beamed {short_name} to {dev} (Downloads)!")
            elif method == "websocket":
                dev = res.get("device", "Web Client")
                lbl = Gtk.Label(label=f"✓ Beamed {short_name} to {dev}!")
            else:
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

    def on_p2p_declined_by_peer(self, filename, peer):
        short_name = (filename[:12] + '…') if len(filename) > 13 else filename
        self.trigger_island_alert("⚠️", f"{peer} declined {short_name}", "yellow", 4.0)
        self.mini_mascot.set_mood("dizzy")
        return GLib.SOURCE_REMOVE

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
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            opener.open(req, timeout=2.0)
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
            self.show_converter_bar(paths)
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


class LexiBeamWindow(Gtk.Window):
    """
    Sleek Obsidian & Titanium Lexi Beam Hub.
    - Scans and displays all available Lexi devices on the Wi-Fi LAN.
    - Sends files with direct P2P transfer and recipient Accept/Decline verification.
    - Offers instant file chooser and QR code fallback for non-Lexi devices.
    """
    def __init__(self, target_url=None, title="⚡ Lexi Beam · Nearby Devices", file_path=None):
        super().__init__(title="Lexi Beam")
        self.set_decorated(False)
        self.set_resizable(False)
        self.set_default_size(360, 480)
        self.add_css_class('lexi-beam-win')

        self.file_path = file_path
        self.target_url = target_url
        self.poll_timer_id = None

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        root.set_margin_top(16)
        root.set_margin_bottom(16)
        root.set_margin_start(18)
        root.set_margin_end(18)
        root.add_css_class('lexi-beam-box')
        self.set_child(root)

        # Header
        head_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        title_lbl = Gtk.Label(label=title, xalign=0, hexpand=True)
        title_lbl.add_css_class('lexi-beam-title')
        head_box.append(title_lbl)

        close_btn = Gtk.Button(label="✕")
        close_btn.set_focusable(False)
        close_btn.add_css_class('island-action-btn')
        close_btn.connect('clicked', lambda *_: self.close())
        head_box.append(close_btn)
        root.append(head_box)

        # Active File Preview Tray or File Selector
        self.file_tray = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.file_tray.add_css_class('lexi-beam-file-tray')
        self.file_lbl = Gtk.Label(label="", xalign=0, hexpand=True)
        self.file_lbl.add_css_class('lexi-beam-file-name')
        self.file_tray.append(self.file_lbl)

        self.pick_file_btn = Gtk.Button(label="📁 Choose File")
        self.pick_file_btn.set_focusable(False)
        self.pick_file_btn.add_css_class('island-action-btn')
        self.pick_file_btn.connect('clicked', lambda *_: self._on_pick_file())
        self.file_tray.append(self.pick_file_btn)
        root.append(self.file_tray)

        self._update_file_preview()

        # Section Label: Nearby Devices & Direct IP
        sec_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        pulse_dot = Gtk.Label(label="●")
        pulse_dot.add_css_class('lexi-beam-pulse-dot')
        sec_box.append(pulse_dot)
        sec_lbl = Gtk.Label(label="AVAILABLE DEVICES", xalign=0, hexpand=True)
        sec_lbl.add_css_class('lexi-beam-sec-title')
        sec_box.append(sec_lbl)

        direct_ip_btn = Gtk.Button(label="＋ Direct IP")
        direct_ip_btn.set_focusable(False)
        direct_ip_btn.add_css_class('lexi-beam-tab-btn')
        direct_ip_btn.set_tooltip_text("Pair with a laptop directly by IP (bypasses router client isolation)")
        direct_ip_btn.connect('clicked', lambda *_: self._on_toggle_direct_ip())
        sec_box.append(direct_ip_btn)
        root.append(sec_box)

        # Direct IP Entry Tray (revealed on click)
        self.direct_ip_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.direct_ip_box.set_visible(False)
        self.direct_ip_entry = Gtk.Entry()
        self.direct_ip_entry.set_placeholder_text("Laptop IP (e.g. 192.168.1.5)")
        self.direct_ip_entry.set_hexpand(True)
        self.direct_ip_entry.connect('activate', lambda *_: self._on_connect_direct_ip())
        self.direct_ip_box.append(self.direct_ip_entry)

        pair_btn = Gtk.Button(label="Pair")
        pair_btn.set_focusable(False)
        pair_btn.add_css_class('lexi-beam-device-btn')
        pair_btn.connect('clicked', lambda *_: self._on_connect_direct_ip())
        self.direct_ip_box.append(pair_btn)
        root.append(self.direct_ip_box)

        # Peers Scroll Area / Box
        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_min_content_height(160)
        scroller.set_max_content_height(200)

        self.peers_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        scroller.set_child(self.peers_box)
        root.append(scroller)

        # Web / QR fallback toggle & container
        self.qr_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.qr_box.set_visible(False)

        # QR Code setup
        qr_path = "/tmp/lexi_beam_qr.png"
        server = lexi_drop.get_lexi_drop_instance() if lexi_drop else None
        if server:
            server.generate_qr_code(target_url, save_path=qr_path)
            self.qr_url = target_url or server.get_server_url()
        else:
            self.qr_url = target_url or "http://127.0.0.1:7890"

        pic_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        pic_box.set_halign(Gtk.Align.CENTER)
        pic_box.add_css_class('lexi-qr-img-wrap')
        if os.path.exists(qr_path):
            gfile = Gio.File.new_for_path(qr_path)
            pic = Gtk.Picture.new_for_file(gfile)
            pic.set_size_request(160, 160)
            pic_box.append(pic)
        self.qr_box.append(pic_box)

        url_btn = Gtk.Button(label=f"🌐 {self.qr_url}")
        url_btn.set_focusable(False)
        url_btn.add_css_class('lexi-qr-url-pill')
        def _copy_url(*_):
            clipboard = Gdk.Display.get_default().get_clipboard()
            clipboard.set(self.qr_url)
            url_btn.set_label("✓ Copied Link!")
        url_btn.connect('clicked', _copy_url)
        self.qr_box.append(url_btn)

        root.append(self.qr_box)

        # Toggle Button: "📱 Show QR / Web Receiver"
        self.toggle_qr_btn = Gtk.Button(label="📱 Non-Lexi Devices (Show QR / Web)")
        self.toggle_qr_btn.set_focusable(False)
        self.toggle_qr_btn.add_css_class('lexi-beam-tab-btn')
        def _toggle_qr(*_):
            vis = not self.qr_box.get_visible()
            self.qr_box.set_visible(vis)
            self.toggle_qr_btn.set_label("📱 Hide QR / Web" if vis else "📱 Non-Lexi Devices (Show QR / Web)")
            if vis:
                self.set_default_size(360, 600)
            else:
                self.set_default_size(360, 480)
        self.toggle_qr_btn.connect('clicked', _toggle_qr)
        root.append(self.toggle_qr_btn)

        # Status text
        self.status_lbl = Gtk.Label(label="📡 Scanning LAN for nearby Lexi peers...", xalign=0.5)
        self.status_lbl.add_css_class('lexi-qr-status')
        root.append(self.status_lbl)

        # Drag and drop support for files directly into window
        drop_target = Gtk.DropTarget.new(Gio.File, Gdk.DragAction.COPY)
        def _on_file_drop(target, value, x, y):
            if value and hasattr(value, 'get_path'):
                p = value.get_path()
                if p and os.path.exists(p):
                    self.file_path = p
                    self._update_file_preview()
                    self.status_lbl.set_label(f"📄 Loaded {os.path.basename(p)} · Ready to Beam")
                    return True
            return False
        drop_target.connect('drop', _on_file_drop)
        self.add_controller(drop_target)

        # Refresh peers immediately & on timer
        self._refresh_peers()
        self.poll_timer_id = GLib.timeout_add_seconds(2, self._refresh_peers)
        self.connect('destroy', self._on_destroy)

    def _on_destroy(self, *_):
        if self.poll_timer_id:
            GLib.source_remove(self.poll_timer_id)
            self.poll_timer_id = None

    def _on_toggle_direct_ip(self):
        vis = not self.direct_ip_box.get_visible()
        self.direct_ip_box.set_visible(vis)
        if vis:
            self.direct_ip_entry.grab_focus()

    def _on_connect_direct_ip(self):
        ip = self.direct_ip_entry.get_text().strip()
        if not ip:
            return
        self.status_lbl.set_label(f"📡 Probing {ip}...")
        def _worker():
            server = lexi_drop.get_lexi_drop_instance() if lexi_drop else None
            if not server:
                return
            res = server.probe_peer(ip)
            def _done():
                if res.get("success"):
                    self.status_lbl.set_label(f"✨ Paired with {res.get('device')} ({ip})!")
                    self.direct_ip_box.set_visible(False)
                    self._refresh_peers()
                else:
                    self.status_lbl.set_label(f"⚠️ Could not reach {ip}: {res.get('error', '')[:25]}")
            GLib.idle_add(_done)
        threading.Thread(target=_worker, daemon=True).start()

    def _update_file_preview(self):
        if self.file_path and os.path.exists(self.file_path):
            fname = os.path.basename(self.file_path)
            short_name = (fname[:18] + '…') if len(fname) > 19 else fname
            sz = os.path.getsize(self.file_path)
            sz_str = converter.format_size(sz) if hasattr(converter, 'format_size') else f"{sz}B"
            self.file_lbl.set_label(f"📄 {short_name} ({sz_str})")
            self.pick_file_btn.set_label("Change")
        else:
            self.file_lbl.set_label("No file selected yet (Drop file here)")
            self.pick_file_btn.set_label("📁 Choose File")

    def _on_pick_file(self, on_chosen_cb=None):
        dialog = Gtk.FileChooserNative.new(
            "Select File to Beam",
            self,
            Gtk.FileChooserAction.OPEN,
            "⚡ Select",
            "Cancel"
        )
        def _response(d, response_id):
            if response_id == Gtk.ResponseType.ACCEPT:
                gfile = d.get_file()
                if gfile:
                    self.file_path = gfile.get_path()
                    self._update_file_preview()
                    if callable(on_chosen_cb):
                        on_chosen_cb(self.file_path)
            d.destroy()
        dialog.connect('response', _response)
        dialog.show()

    def _refresh_peers(self):
        server = lexi_drop.get_lexi_drop_instance() if lexi_drop else None
        if not server:
            return GLib.SOURCE_CONTINUE

        peers = server.get_all_peers()

        while child := self.peers_box.get_first_child():
            self.peers_box.remove(child)

        if not peers:
            empty_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            empty_box.set_margin_top(12)
            empty_box.set_margin_bottom(12)
            empty_lbl = Gtk.Label(label="📡 Scanning Wi-Fi for nearby Lexi devices...")
            empty_lbl.add_css_class('lexi-beam-empty')
            hint_lbl = Gtk.Label(label="Open Lexi on another laptop or Lexi Companion on Android")
            hint_lbl.add_css_class('lexi-beam-empty-hint')
            empty_box.append(empty_lbl)
            empty_box.append(hint_lbl)
            self.peers_box.append(empty_box)
            self.status_lbl.set_label("📡 Ready to beam · 0 nearby devices")
            return GLib.SOURCE_CONTINUE

        self.status_lbl.set_label(f"✨ Found {len(peers)} nearby device{'s' if len(peers) > 1 else ''}")

        for peer in peers:
            pip = peer.get("ip")
            dev_name = peer.get("device", "Lexi Peer")
            dev_type = peer.get("type", "laptop")
            is_phone = dev_type == "phone" or "phone" in dev_name.lower() or "android" in dev_name.lower() or "redmi" in dev_name.lower()

            card = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            card.add_css_class('lexi-beam-device-card')

            icon_lbl = Gtk.Label(label="📱" if is_phone else "💻")
            icon_lbl.add_css_class('lexi-beam-device-icon')
            card.append(icon_lbl)

            info_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            info_box.set_hexpand(True)
            name_lbl = Gtk.Label(label=dev_name, xalign=0)
            name_lbl.add_css_class('lexi-beam-device-name')
            info_box.append(name_lbl)

            status_text = "⚡ Instant AirDrop" if is_phone else f"● Online · {pip}"
            ip_lbl = Gtk.Label(label=status_text, xalign=0)
            ip_lbl.add_css_class('lexi-beam-device-ip')
            info_box.append(ip_lbl)
            card.append(info_box)

            beam_btn = Gtk.Button(label="⚡ Beam")
            beam_btn.set_focusable(False)
            beam_btn.add_css_class('lexi-beam-device-btn')
            beam_btn.connect('clicked', lambda _, p_ip=pip, d_name=dev_name, b_btn=beam_btn: self._on_beam_to_peer(p_ip, d_name, b_btn))
            card.append(beam_btn)

            self.peers_box.append(card)

        return GLib.SOURCE_CONTINUE

    def _on_beam_to_peer(self, peer_ip, dev_name, beam_btn):
        if not self.file_path or not os.path.exists(self.file_path):
            self._on_pick_file(lambda chosen: self._beam_file_to_peer(chosen, peer_ip, dev_name, beam_btn))
            return
        self._beam_file_to_peer(self.file_path, peer_ip, dev_name, beam_btn)

    def _beam_file_to_peer(self, file_path, peer_ip, dev_name, beam_btn):
        beam_btn.set_label("⏳ Sending...")
        beam_btn.set_sensitive(False)
        self.status_lbl.set_label(f"⚡ Beaming {os.path.basename(file_path)} to {dev_name}...")

        def _send():
            server = lexi_drop.get_lexi_drop_instance() if lexi_drop else None
            if not server:
                return {"error": "Lexi drop server not available"}
            return server.send_p2p_to_peer(peer_ip, file_path)

        def _on_done(res):
            if not res or res.get("error"):
                err = res.get("error", "Failed") if res else "Error"
                beam_btn.set_label("⚠️ Failed")
                self.status_lbl.set_label(f"⚠️ {err[:35]}")
                GLib.timeout_add_seconds(3, lambda: (beam_btn.set_label("⚡ Beam"), beam_btn.set_sensitive(True)))
            else:
                method = res.get("method")
                if method == "adb":
                    spd = res.get("speed_mbps", 0)
                    beam_btn.set_label("✓ Beamed!")
                    self.status_lbl.set_label(f"✨ Beamed to {dev_name} (Downloads) @ {spd:.1f} MB/s!")
                elif method == "websocket":
                    beam_btn.set_label("✓ Beamed!")
                    self.status_lbl.set_label(f"✨ Delivered to {dev_name} via Web Receiver!")
                else:
                    beam_btn.set_label("✓ Sent")
                    self.status_lbl.set_label(f"✨ Sent to {dev_name}! Waiting for accept...")
                GLib.timeout_add_seconds(4, lambda: (beam_btn.set_label("⚡ Beam"), beam_btn.set_sensitive(True)))

        threading.Thread(target=lambda: GLib.idle_add(_on_done, _send()), daemon=True).start()

# Backward compatibility alias
LexiQrPopoverWindow = LexiBeamWindow



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


_SCREEN_QUERY_RE = re.compile(
    r'\b(?:what(?:\x27s| is)|read|explain|check|analyze|summarize|translate|debug|solve)\s+'
    r'(?:(?:this|my|the)\s+)?(?:screen|code|error|page|window|diagram|slide|terminal|text|image|tab|ui)\b'
    r'|\b(?:what(?:\x27s| is)\s+(?:on|in)\s+(?:my\s+|the\s+)?screen)\b'
    r'|\b(?:look\s+at\s+(?:this|my\s+screen|the\s+screen))\b',
    re.I
)


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
        self._load_conversation_from_disk()
        self.assistant = LexiAssistant(build_executors(SystemActionEngine.get()))
        self.assistant.confirmer = self._confirm_risky
        self.last_answer = ""
        self.last_whatsapp_command = None
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

        self.copy_code_btn = Gtk.Button(label="📋 Copy Code")
        self.copy_code_btn.set_focusable(False)
        self.copy_code_btn.add_css_class('flat')
        self.copy_code_btn.add_css_class('lexi-action-btn')
        self.copy_code_btn.set_tooltip_text("Extract and copy code blocks from response")
        self.copy_code_btn.connect('clicked', self.on_copy_code)
        bottom_bar.append(self.copy_code_btn)

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
                    # Convert & compress with PIL for crisp LLM vision
                    with Image.open(filepath) as img:
                        im_rgb = img.convert('RGB')
                        max_dim = max(1280, self.config.get('max_image_dimension', 1280))
                        quality = max(80, self.config.get('image_quality', 80))
                        w, h = im_rgb.size
                        if max(w, h) > max_dim:
                            im_rgb.thumbnail((max_dim, max_dim), Image.Resampling.LANCZOS)
                        buf = io.BytesIO()
                        im_rgb.save(buf, format='JPEG', quality=quality)
                        b64 = base64.b64encode(buf.getvalue()).decode('utf-8')
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
        self._save_conversation_to_disk()
        self.assistant.reset()
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

                # 2. Scale image for LLM payload (max 1280px, JPEG 80)
                max_dim = max(1280, self.config.get('max_image_dimension', 1280))
                quality = max(80, self.config.get('image_quality', 80))
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
                "• `/mirror [--off]` : Mirror & control Android phone at 60 FPS (Mouse, Keyboard, Audio)\n"
                "• `/qr` : Show Wi-Fi QR code for Android phone connection\n"
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

        # Check for /mirror or /phone (Android Screen Mirroring & Control)
        if cmd.lower().startswith(('/mirror', '/phone', 'mirror', 'phone')):
            parts = cmd.split()
            turn_screen_off = '--off' in parts or '-off' in parts
            res = SystemActionEngine.get().start_phone_mirror(turn_screen_off=turn_screen_off)
            render_markdown_to_buffer(self.text_buffer, res.get("message", ""))
            self.scroll_to_end()
            self.update_mascot_mood("happy" if res.get("success") else "neutral")
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
            action = {'type': 'command', 'attrs': {}, 'body': cmd[1:].strip()}
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
        elif cmd.startswith('/yt ') or cmd.startswith('/youtube '):
            yt_query = cmd.split(maxsplit=1)[1].strip()
            action = {
                'type': 'browser',
                'attrs': {'command': 'youtube_play_fullscreen', 'query': yt_query},
                'body': ''
            }

        if action:
            if action.get('type') in ('whatsapp', 'wa'):
                self.last_whatsapp_command = dict(action)
            self.update_mascot_mood("working")
            def worker():
                SystemActionEngine.get().confirm_callback = self._confirm_risky
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

        # `/wa <contact> <message>` now goes through the same draft + confirm flow as natural language.
        _wa = re.match(r"^/(?:wa|whatsapp)\s+(\S+)\s+(.+)$", text, re.I)
        if _wa:
            text = f"send {_wa.group(2)} to {_wa.group(1)} on whatsapp"

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

        # Sanitize past turns from self.conversation:
        # Keep conversational text context, but strip bulky past base64 images so we don't blow token limits
        conv = []
        for m in self.conversation:
            if m.get('role') == 'user' and isinstance(m.get('content'), list):
                text_parts = [item['text'] for item in m['content'] if item.get('type') == 'text']
                img_count = sum(1 for item in m['content'] if item.get('type') == 'image_url')
                combined = "\n".join(text_parts)
                if img_count:
                    combined = f"[Previous turn with {img_count} attached page(s)]:\n{combined}"
                conv.append({"role": "user", "content": combined})
            else:
                conv.append(m)

        # Keep last 14 turns of history
        if len(conv) > 14:
            conv = conv[-14:]
        msgs.extend(conv)

        # Contextual active window injection
        active_ctx = ""
        try:
            eng = SystemActionEngine.get()
            win = eng._inspect_via_x11_capability()
            if win.get("active_title") or win.get("active_class"):
                app_c = win.get("active_class") or "App"
                app_t = win.get("active_title") or ""
                active_ctx = f"[Active Window Context: App '{app_c}', Title '{app_t}']\n"
        except Exception:
            pass

        # Build current user turn with ALL currently active captured pages
        if self.captured_pages:
            num_pages = len(self.captured_pages)

            manifest_lines = []
            for idx, page in enumerate(self.captured_pages, 1):
                if page.get("text_content"):
                    name = page.get("name") or f"Document {idx}"
                    manifest_lines.append(f"• Page {idx} ({name}): Text/Document")
                elif page.get("is_snip"):
                    manifest_lines.append(f"• Page {idx}: Screen Snip (Selected Region)")
                elif page.get("is_file"):
                    name = page.get("name") or f"Image {idx}"
                    manifest_lines.append(f"• Page {idx} ({name}): Image File")
                else:
                    manifest_lines.append(f"• Page {idx}: Full Screen Capture")
            manifest_block = "\n".join(manifest_lines)

            if num_pages > 1:
                instruction = (
                    f"{active_ctx}"
                    f"📁 **Attached Materials ({num_pages} Pages total)**:\n"
                    f"{manifest_block}\n\n"
                    f"**User Request**: {current_user_question}\n\n"
                    f"⚠️ **CRITICAL MULTI-PAGE ANALYSIS INSTRUCTION**:\n"
                    f"There are {num_pages} distinct pages/images attached. You MUST examine, synthesize, and address ALL {num_pages} pages.\n"
                    f"- In your answer, structure your findings clearly by page (e.g. '### 📄 Page 1: ...', '### 📄 Page 2: ...').\n"
                    f"- If comparing or debugging across pages, explicitly explain the differences, relations, or progression from Page 1 to Page {num_pages}.\n"
                    f"- Do NOT focus on only one page and ignore the others. Cover all {num_pages} pages."
                )
            else:
                instruction = (
                    f"{active_ctx}"
                    f"📁 **Attached**: {manifest_lines[0]}\n\n"
                    f"**User Request**: {current_user_question}"
                )

            content = [{"type": "text", "text": instruction}]

            # Attach each document and image
            for idx, page in enumerate(self.captured_pages, 1):
                if page.get("text_content"):
                    p_name = page.get("name", f"Document {idx}")
                    content.append({
                        "type": "text",
                        "text": f"--- [Document {idx}: {p_name}] ---\n{page['text_content']}\n--- [End of Document {idx}] ---"
                    })
                if page.get("b64"):
                    p_label = page.get("name") or ("Snip" if page.get("is_snip") else "Full Screen")
                    content.append({
                        "type": "text",
                        "text": f"📸 [PAGE {idx} OF {num_pages}: {p_label}]"
                    })
                    content.append({
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/jpeg;base64,{page['b64']}",
                            "detail": "high"
                        }
                    })

            user_turn = {
                "role": "user",
                "content": content
            }
        else:
            user_turn = {
                "role": "user",
                "content": f"{active_ctx}{current_user_question}" if active_ctx else current_user_question
            }

        if self.config.get("legacy_prompt", False):
            msgs.append(user_turn)          # old behaviour: config prompt + raw history
        else:
            # Registry-driven action prompt + recent turns + rolling summary.
            msgs = self.assistant.build_messages(user_turn)
        return msgs, user_turn

    def _confirm_risky(self, text):
        """Called from the worker thread; blocks until the user answers (60 s -> deny)."""
        done = threading.Event()
        answer = {"ok": False, "closed": False, "dlg": None}

        def _show():
            try:
                dlg_cls = getattr(Adw, "AlertDialog", None)
                if dlg_cls is not None:
                    dlg = dlg_cls.new("Confirm, boss", text)
                    answer["dlg"] = dlg
                    dlg.add_response("no", "Cancel")
                    dlg.add_response("yes", "Run it")
                    dlg.set_response_appearance("yes", Adw.ResponseAppearance.DESTRUCTIVE)
                    dlg.set_default_response("no")
                    dlg.set_close_response("no")
                    def _done(d, res):
                        try:
                            if not answer["closed"]:
                                answer["ok"] = (d.choose_finish(res) == "yes")
                        finally:
                            done.set()
                    dlg.choose(self, None, _done)
                else:
                    dlg = Adw.MessageDialog.new(self, "Confirm, boss", text)
                    answer["dlg"] = dlg
                    dlg.add_response("no", "Cancel")
                    dlg.add_response("yes", "Run it")
                    dlg.set_response_appearance("yes", Adw.ResponseAppearance.DESTRUCTIVE)
                    dlg.set_default_response("no")
                    dlg.set_close_response("no")
                    def _resp(d, response):
                        if not answer["closed"]:
                            answer["ok"] = (response == "yes")
                        done.set()
                    dlg.connect("response", _resp)
                    dlg.present()
            except Exception as e:
                log(f"confirm dialog error: {e}")
                done.set()
            return False

        GLib.idle_add(_show)
        if not done.wait(timeout=60):
            # Timed out: mark closed so a late click cannot approve, and dismiss the dialog.
            answer["closed"] = True
            def _close():
                dlg = answer.get("dlg")
                try:
                    if dlg is not None:
                        dlg.close()
                except Exception as e:
                    log(f"confirm dialog close error: {e}")
                return False
            GLib.idle_add(_close)
            return False
        return bool(answer["ok"])

    def run_query(self, user_question):
        # Deterministic intent router runs BEFORE the LLM. Handled locally -> no model call.
        try:
            local_reply = self.assistant.handle_user_text(user_question)
        except Exception as e:
            log(f"local intent error: {e}")
            local_reply = None
        if local_reply is not None:
            GLib.idle_add(self.on_query_finished, local_reply,
                          {"role": "user", "content": user_question}, None)
            return

        # Auto-Screen Intelligence: zero-click silent screen grab if visual question asked without prior snip
        if not self.captured_pages and _SCREEN_QUERY_RE.search(user_question):
            try:
                auto_shot = self._auto_capture_screen_sync()
                if auto_shot:
                    self.process_screenshot(auto_shot, is_snip=False, append_mode=False)
            except Exception as e:
                log(f"Auto screen grab failed: {e}")

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
            deepseek_model = self.config.get('deepseek_model', 'deepseek-flash')
            is_ds_multimodal = any(k in deepseek_model.lower() for k in ['flash', 'vl', 'vision', 'v4'])
            can_ds_handle = (not has_images) or is_ds_multimodal

            # 1. Primary: Direct DeepSeek API (if key is set and model can handle the payload)
            if deepseek_key and can_ds_handle:
                try:
                    ds_payload = {
                        "model": deepseek_model,
                        "messages": messages,
                        "stream": True,
                        "max_tokens": max(max_tokens, 4096)
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
                                    delta_obj = chunk['choices'][0]['delta']
                                    delta = delta_obj.get('content') or ''
                                    reasoning = delta_obj.get('reasoning_content') or ''
                                    if reasoning and not full_response:
                                        GLib.idle_add(self.status_pill.set_text, f"🧠 {deepseek_model} (thinking...)")
                                    if delta:
                                        if not full_response:
                                            GLib.idle_add(self.status_pill.set_text, f"● {deepseek_model} (direct)")
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
            eng = SystemActionEngine.get()
            actions = eng.parse_actions(full_text)
            errored = (not full_text.strip()) or full_text.startswith(("\n⚠️ Error", "🔑 **No API Key"))
            if actions and not errored:
                # Model output may only select registered, schema-validated actions.
                # The user-facing reply is built from structured results, not model prose.
                eng.confirm_callback = self._confirm_risky
                reply = self.assistant.handle_model_actions(actions, user_question)
                self.assistant.record_llm_exchange(user_question, reply)
                GLib.idle_add(self.on_query_finished, reply, user_turn, None)
            else:
                if not errored and user_turn:
                    self.assistant.record_llm_exchange(user_question, full_text)
                GLib.idle_add(self.on_query_finished, full_text, user_turn, None)

    def on_query_started(self):
        if self.timer_source_id:
            GLib.source_remove(self.timer_source_id)
            self.timer_source_id = None
        self.spinner.stop()
        self.thinking_box.set_visible(False)

    def capture_local_verification_frame(self, timeout=4.0):
        """Optional P2 local screen-capture observer via GNOME Xdp.Portal.
        Captures a local frame for OCR/visual verification without sending to cloud models."""
        done = threading.Event()
        holder = {"path": None}

        def _capture_on_main():
            try:
                portal = Xdp.Portal()
                def _on_done(_src, res, _data):
                    try:
                        uri = portal.take_screenshot_finish(res)
                        if uri:
                            parsed = urllib.parse.urlparse(uri)
                            p = urllib.parse.unquote(parsed.path)
                            if os.path.exists(p):
                                holder["path"] = p
                    except Exception as e:
                        log(f"Local verification screenshot error: {e}")
                    finally:
                        done.set()
                portal.take_screenshot(None, Xdp.ScreenshotFlags.NONE, None, _on_done, None)
            except Exception as e:
                log(f"Local verification portal init error: {e}")
                done.set()
            return GLib.SOURCE_REMOVE

        GLib.idle_add(_capture_on_main)
        done.wait(timeout=timeout)
        return holder["path"]

    def append_stream_token(self, delta):
        if not self.turn_start_mark:
            return
        start_iter = self.text_buffer.get_iter_at_mark(self.turn_start_mark)
        end_iter = self.text_buffer.get_end_iter()
        current_turn_text = self.text_buffer.get_text(start_iter, end_iter, True) + (delta or "")
        if "<action" in current_turn_text.lower():
            # Issue 5: Quarantine optimistic model prose as soon as an action tag appears in stream
            self.text_buffer.delete(start_iter, end_iter)
            self.text_buffer.insert(self.text_buffer.get_end_iter(), "⏳ Executing and verifying action(s)...")
            self.scroll_to_end()
            return
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
            # Replace raw stream with verified structured output or formatted markdown
            start_iter = self.text_buffer.get_iter_at_mark(self.turn_start_mark)
            end_iter = self.text_buffer.get_end_iter()
            self.text_buffer.delete(start_iter, end_iter)

            parsed_actions = SystemActionEngine.get().parse_actions(full_text)
            rendered_summary = render_turn_response_to_buffer(
                self.text_buffer, full_text, action_results=action_results, actions=parsed_actions
            )

            self.text_buffer.delete_mark(self.turn_start_mark)
            self.turn_start_mark = None

            if user_turn and not full_text.startswith("\n⚠️ Error"):
                self.conversation.append(user_turn)
                # Store truthful structured outcome in conversation history when actions ran
                recorded_assistant = rendered_summary if (parsed_actions or action_results) else full_text
                self.conversation.append({"role": "assistant", "content": recorded_assistant})
                self.last_answer = rendered_summary or full_text
                self._save_conversation_to_disk()

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

    def on_copy_code(self, _=None):
        ans = self.last_answer or ""
        if not ans:
            start = self.text_buffer.get_start_iter()
            end = self.text_buffer.get_end_iter()
            ans = self.text_buffer.get_text(start, end, True)

        code_blocks = re.findall(r'```(?:[\w\+\-\.]+)?\n?(.*?)```', ans, re.DOTALL)
        if code_blocks:
            extracted_code = "\n\n".join(b.strip() for b in code_blocks if b.strip())
        else:
            inline_code = re.findall(r'`([^`\n]+)`', ans)
            if inline_code:
                extracted_code = "\n".join(inline_code)
            else:
                extracted_code = ans

        if extracted_code:
            display = Gdk.Display.get_default()
            if display:
                clipboard = display.get_clipboard()
                clipboard.set(extracted_code)
            if shutil.which("wl-copy"):
                try:
                    subprocess.run(["wl-copy"], input=extracted_code.encode("utf-8"), timeout=1.0)
                except Exception:
                    pass
            self.copy_code_btn.set_label("✓ Copied Code!")
            self.update_mascot_mood("happy")
            def reset_code_copy():
                self.copy_code_btn.set_label("📋 Copy Code")
                return GLib.SOURCE_REMOVE
            GLib.timeout_add_seconds(2, reset_code_copy)

    def _save_conversation_to_disk(self):
        try:
            hist_file = os.path.join(APP_DIR, "conversation_history.json")
            clean_conv = []
            for item in self.conversation[-30:]:
                role = item.get("role", "")
                content = item.get("content", "")
                if isinstance(content, list):
                    text_parts = [p.get("text", "") for p in content if p.get("type") == "text"]
                    content = "\n".join(text_parts)
                clean_conv.append({"role": role, "content": str(content)})
            with open(hist_file, "w", encoding="utf-8") as f:
                json.dump(clean_conv, f, indent=2, ensure_ascii=False)
        except Exception as e:
            log(f"Failed to save conversation history: {e}")

    def _load_conversation_from_disk(self):
        try:
            hist_file = os.path.join(APP_DIR, "conversation_history.json")
            if os.path.exists(hist_file):
                with open(hist_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, list):
                        self.conversation = data[-20:]
                        for turn in reversed(self.conversation):
                            if turn.get("role") == "assistant" and turn.get("content"):
                                self.last_answer = turn["content"]
                                break
        except Exception as e:
            log(f"Failed to load conversation history: {e}")

    def _auto_capture_screen_sync(self):
        """Zero-click silent screenshot capture when visual queries are asked."""
        shot_path = f"/tmp/lexi_auto_screen_{int(time.time())}.png"
        if shutil.which("gnome-screenshot"):
            try:
                res = subprocess.run(["gnome-screenshot", "-f", shot_path], capture_output=True, timeout=2.5)
                if res.returncode == 0 and os.path.exists(shot_path) and os.path.getsize(shot_path) > 0:
                    return shot_path
            except Exception:
                pass
        if shutil.which("scrot"):
            try:
                res = subprocess.run(["scrot", shot_path], capture_output=True, timeout=2.5)
                if res.returncode == 0 and os.path.exists(shot_path) and os.path.getsize(shot_path) > 0:
                    return shot_path
            except Exception:
                pass
        if shutil.which("import"):
            try:
                res = subprocess.run(["import", "-window", "root", shot_path], capture_output=True, timeout=2.5)
                if res.returncode == 0 and os.path.exists(shot_path) and os.path.getsize(shot_path) > 0:
                    return shot_path
            except Exception:
                pass
        return None


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
