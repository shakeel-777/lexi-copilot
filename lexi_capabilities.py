"""Structured capability registry for Lexi.

Every action Lexi may perform is declared here: name, input schema,
confirmation level, how to verify the result, and what to say on failure.
Anything not in this registry cannot run, whether it came from the local
intent router or from LLM output.

Pure Python (no GTK / subprocess) so it is unit-testable.
"""

import json
import re
import urllib.parse
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from lexi_apps import CONFIRM_ON_QUIT, resolve_app
from lexi_policy import classify_command

# Confirmation levels
CONFIRM_NONE = "none"          # safe, run immediately
CONFIRM_REQUIRED = "required"  # must get an explicit yes before the irreversible step


class SchemaError(ValueError):
    pass


# Characters that never belong in a user-visible argument (shell, JS, markup).
_FORBIDDEN_CHARS_RE = re.compile(r"[;&|`$<>{}\\\n\r\x00]")
_FORBIDDEN_TOKEN_RE = re.compile(
    r"(?:javascript\s*:|data\s*:|\bxdotool\b|\bydotool\b|\bwtype\b|\bdotool\b"
    r"|\beval\s*\(|\bdocument\s*\.|\bwindow\s*\.)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Field:
    """One argument of a capability."""
    kind: str                           # "str" | "int" | "enum"
    required: bool = True
    max_len: int = 200
    choices: Tuple[str, ...] = ()
    minimum: Optional[int] = None
    maximum: Optional[int] = None
    pattern: Optional[str] = None       # full-match regex for strings
    allow_free_text: bool = False       # message bodies may contain punctuation
    raw: bool = False                   # shell command text: gated by lexi_policy, not by char filters


@dataclass(frozen=True)
class Capability:
    name: str
    description: str
    schema: Dict[str, Field]
    confirmation: str = CONFIRM_NONE
    verification: str = "none"          # human-readable: how success is verified
    failure_text: str = "I couldn't do that."
    retry_safe: bool = True             # may "try again" re-run it silently?
    sensitive: bool = False             # send/delete/payment/login/permission
    executor: str = ""                  # key resolved by the app, e.g. "set_volume"
    policy: Optional[Callable[[Dict[str, Any]], Tuple[str, str]]] = None  # args -> (allow|confirm|blocked, reason)
    router_only: bool = False           # if True, excluded from LLM tools prompt; only the router dispatches it

    def validate(self, args: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(args, dict):
            raise SchemaError("arguments must be an object")
        unknown = set(args) - set(self.schema)
        if unknown:
            raise SchemaError(f"unexpected argument(s): {', '.join(sorted(unknown))}")
        clean: Dict[str, Any] = {}
        for key, spec in self.schema.items():
            if key not in args or args[key] in (None, ""):
                if spec.required:
                    raise SchemaError(f"missing required argument '{key}'")
                continue
            clean[key] = _validate_value(key, args[key], spec)
        return clean


def _validate_value(key: str, value: Any, spec: Field) -> Any:
    if spec.kind == "int":
        if isinstance(value, bool):
            raise SchemaError(f"'{key}' must be an integer")
        try:
            num = int(value)
        except (TypeError, ValueError):
            raise SchemaError(f"'{key}' must be an integer")
        if spec.minimum is not None and num < spec.minimum:
            raise SchemaError(f"'{key}' must be >= {spec.minimum}")
        if spec.maximum is not None and num > spec.maximum:
            raise SchemaError(f"'{key}' must be <= {spec.maximum}")
        return num
    if not isinstance(value, str):
        raise SchemaError(f"'{key}' must be text")
    text = value.strip()
    if len(text) > spec.max_len:
        raise SchemaError(f"'{key}' is too long")
    if spec.kind == "enum":
        low = text.lower()
        if low not in spec.choices:
            raise SchemaError(f"'{key}' must be one of {', '.join(spec.choices)}")
        return low
    if spec.raw:
        if "\x00" in text:
            raise SchemaError(f"'{key}' contains disallowed characters")
        return text                     # execution policy decides; filters here would break normal commands
    if _FORBIDDEN_TOKEN_RE.search(text):
        raise SchemaError(f"'{key}' contains a disallowed token")
    if not spec.allow_free_text and _FORBIDDEN_CHARS_RE.search(text):
        raise SchemaError(f"'{key}' contains disallowed characters")
    if spec.allow_free_text and ("\x00" in text):
        raise SchemaError(f"'{key}' contains disallowed characters")
    if key == "site":
        _SITES_MAP = {
            "web": "google.com", "browser": "google.com", "internet": "google.com",
            "google": "google.com", "youtube": "youtube.com", "yt": "youtube.com",
            "gmail": "mail.google.com", "github": "github.com", "reddit": "reddit.com",
            "twitter": "x.com", "x": "x.com", "instagram": "instagram.com", "insta": "instagram.com",
            "chatgpt": "chatgpt.com", "claude": "claude.ai", "bookmyshow": "in.bookmyshow.com",
            "amazon": "amazon.in", "flipkart": "flipkart.com", "netflix": "netflix.com",
        }
        low = text.lower().strip()
        clean_site = re.sub(r"^https?://(?:www\.)?", "", text, flags=re.I).strip()
        clean_low = clean_site.lower()
        if clean_low in _SITES_MAP:
            text = _SITES_MAP[clean_low]
        elif low in _SITES_MAP:
            text = _SITES_MAP[low]
        elif not re.search(r"\.[a-z]{2,}", clean_low):
            text = f"google.com/search?q={urllib.parse.quote_plus(text)}"
        else:
            if " " in text:
                text = text.replace(" ", "+")
    if spec.pattern and not re.fullmatch(spec.pattern, text):
        raise SchemaError(f"'{key}' has an invalid format")
    return text


# Contacts / app names: letters, digits, spaces and a few name punctuation marks.
_NAME_RE = r"[\w .'\-]{1,100}"
_SITE_RE = r"(?:https?://)?(?:www\.)?[\w.\-]{1,100}\.[A-Za-z]{2,24}(?:/[\w\-./%?=&+#~]*)?"

REGISTRY: Dict[str, Capability] = {}


def _register(cap: Capability) -> Capability:
    REGISTRY[cap.name] = cap
    return cap


_register(Capability(
    name="volume_set",
    description="Set the system volume to an absolute percentage.",
    schema={"percent": Field("int", minimum=0, maximum=100)},
    verification="read back volume with wpctl",
    failure_text="I couldn't change the volume.",
    executor="set_volume",
))
_register(Capability(
    name="volume_change",
    description="Raise or lower the volume by a number of percentage points, or mute/unmute.",
    schema={"mode": Field("enum", choices=("up", "down", "mute", "unmute")),
            "step": Field("int", required=False, minimum=1, maximum=50)},
    verification="read back volume with wpctl",
    failure_text="I couldn't change the volume.",
    executor="set_volume",
))
_register(Capability(
    name="brightness_set",
    description="Set screen brightness to an absolute percentage.",
    schema={"percent": Field("int", minimum=5, maximum=100)},
    verification="read back brightness with brightnessctl",
    failure_text="I couldn't change the brightness.",
    executor="set_brightness",
))
_register(Capability(
    name="brightness_change",
    description="Raise or lower brightness by a number of percentage points.",
    schema={"mode": Field("enum", choices=("up", "down")),
            "step": Field("int", required=False, minimum=1, maximum=50)},
    verification="read back brightness with brightnessctl",
    failure_text="I couldn't change the brightness.",
    executor="set_brightness",
))
_register(Capability(
    name="lock_screen",
    description="Lock the screen.",
    schema={},
    failure_text="I couldn't lock the screen.",
    retry_safe=True,
    executor="lock_screen",
))
_register(Capability(
    name="open_app",
    description="Open an installed application by name.",
    schema={"name": Field("str", pattern=_NAME_RE)},
    verification="launcher reported the app started",
    failure_text="I couldn't open that app.",
    executor="open_target",
))
_register(Capability(
    name="open_site",
    description="Open a website in the default browser.",
    schema={"site": Field("str", pattern=_SITE_RE, allow_free_text=True)},
    verification="none: Lexi can only confirm the browser was asked to open it",
    failure_text="I couldn't open that site.",
    executor="open_target",
))
_register(Capability(
    name="media_control",
    description="Play, pause, skip or go back on the current media player.",
    schema={"command": Field("enum", choices=("play", "pause", "next", "previous", "toggle", "stop"))},
    verification="read player status with playerctl",
    failure_text="I couldn't control the media player.",
    executor="media_control",
))
_register(Capability(
    name="spotify_play",
    description="Search Spotify for a song/artist and play the first match.",
    schema={"query": Field("str", max_len=120)},
    verification="player status and track metadata via playerctl",
    failure_text="I couldn't start that on Spotify.",
    executor="spotify_play",
))
_register(Capability(
    name="youtube_play",
    description="Play a video on YouTube through the scoped Chrome extension bridge.",
    schema={"query": Field("str", max_len=120),
            "fullscreen": Field("enum", required=False, choices=("yes", "no"))},
    verification="Chrome extension reports playing state; fullscreen is reported honestly",
    failure_text="I couldn't play that on YouTube.",
    executor="youtube_play",
))
_register(Capability(
    name="close_chrome",
    description="Gracefully close every Google Chrome window.",
    schema={},
    verification="process table is re-read; success only if the main Chrome process is gone",
    failure_text="I couldn't close Chrome.",
    retry_safe=False,
    executor="close_chrome",
))
_register(Capability(
    name="close_tabs",
    description="Close the Chrome tabs for exactly one approved site: whatsapp or instagram. Cannot close anything else.",
    schema={"target": Field("enum", choices=("whatsapp", "instagram"))},
    verification="extension re-checks the exact approved tab patterns and confirms none remain",
    failure_text="I couldn't close that tab.",
    executor="close_tabs",
))
_register(Capability(
    name="close_tabs_all",
    description="Close every WhatsApp and Instagram tab in Chrome (those two sites only). Requires explicit confirmation.",
    schema={},
    confirmation=CONFIRM_REQUIRED,
    verification="extension re-checks the exact approved tab patterns and confirms none remain",
    failure_text="I couldn't close those tabs.",
    retry_safe=False,
    sensitive=True,
    executor="close_tabs_all",
))
_register(Capability(
    name="whatsapp_draft",
    description="Open WhatsApp Web, find the exact contact and type a draft. Does NOT send.",
    schema={"contact": Field("str", pattern=_NAME_RE),
            "message": Field("str", max_len=1000, allow_free_text=True)},
    verification="exact contact header and composer text are checked before asking to send",
    failure_text="I couldn't prepare that WhatsApp draft.",
    retry_safe=False,           # a retry re-types a draft; ask first
    executor="whatsapp_draft",
))
_register(Capability(
    name="whatsapp_send",
    description="Send the drafted WhatsApp message. Requires explicit user confirmation.",
    schema={"contact": Field("str", pattern=_NAME_RE)},
    confirmation=CONFIRM_REQUIRED,
    verification="Send button clicked in the verified chat",
    failure_text="I couldn't send that WhatsApp message.",
    retry_safe=False,
    sensitive=True,
    executor="whatsapp_send",
))


def _close_app_policy(args):
    spec = resolve_app(args.get("name", ""))
    if spec is not None and spec.key in CONFIRM_ON_QUIT:
        return "confirm", "closing the terminal ends anything running in it"
    return "allow", ""


_register(Capability(
    name="close_app",
    description="Gracefully quit a desktop app by name (chrome, spotify, files, terminal). Closes every window of that app.",
    schema={"name": Field("str", pattern=_NAME_RE)},
    verification="process table is re-read; success only if the app's main process is gone",
    failure_text="I couldn't close that app.",
    retry_safe=False,
    executor="close_app",
    policy=lambda args: _close_app_policy(args),
))
_register(Capability(
    name="list_running_apps",
    description="List which of the known desktop apps (Chrome, Spotify, Files, Terminal) are currently running.",
    schema={},
    verification="none — reads /proc at call time",
    failure_text="I couldn't check what's running.",
    retry_safe=True,
    executor="list_running_apps",
    router_only=True,
))
_register(Capability(
    name="close_all_apps",
    description="Close every currently-running known desktop app (Chrome, Spotify, Files, Terminal). Requires explicit confirmation.",
    schema={},
    confirmation=CONFIRM_REQUIRED,
    verification="each app's process table entry is verified gone after close",
    failure_text="I couldn't close all apps.",
    retry_safe=False,
    sensitive=True,
    executor="close_all_apps",
    router_only=True,
))
_register(Capability(
    name="terminal_task",
    description=("Run one bash command for a task the user directly asked for (create/list/copy/move/edit files, "
                 "git, installs, project work). Output and exit code are reported. High-impact commands "
                 "(rm, sudo, dd, overwriting a file...) ask the user to confirm first; wiping the filesystem root is refused."),
    schema={"command": Field("str", max_len=4000, allow_free_text=True, raw=True)},
    verification="exit status and output of the command",
    failure_text="That command failed.",
    retry_safe=False,
    executor="terminal_task",
    policy=lambda args: classify_command(args.get("command", "")),
))
_register(Capability(
    name="system_battery",
    description="Check the current battery percentage and charging state.",
    schema={},
    verification="battery sysfs/upower reported state",
    failure_text="I couldn't check the battery level.",
    executor="system_battery",
    router_only=True,
))
_register(Capability(
    name="media_now_playing",
    description="Check what music/track is currently playing.",
    schema={},
    verification="playerctl reported metadata and status",
    failure_text="I couldn't check what's playing.",
    executor="media_now_playing",
    router_only=True,
))
_register(Capability(
    name="take_screenshot",
    description="Take a screenshot of the entire desktop and save it to Pictures/Screenshots.",
    schema={},
    verification="screenshot image saved to disk and copied to clipboard",
    failure_text="I couldn't take a screenshot.",
    executor="take_screenshot",
    router_only=True,
))
_register(Capability(
    name="system_sleep",
    description="Put the computer into suspend / sleep mode. Requires explicit user confirmation.",
    schema={},
    confirmation=CONFIRM_REQUIRED,
    verification="systemctl suspend initiated",
    failure_text="I couldn't suspend the system.",
    sensitive=True,
    executor="system_sleep",
    router_only=True,
))
_register(Capability(
    name="system_restart",
    description="Restart the computer. Requires explicit user confirmation.",
    schema={},
    confirmation=CONFIRM_REQUIRED,
    verification="systemctl reboot initiated",
    failure_text="I couldn't restart the system.",
    sensitive=True,
    executor="system_restart",
    router_only=True,
))
_register(Capability(
    name="system_poweroff",
    description="Shut down the computer. Requires explicit user confirmation.",
    schema={},
    confirmation=CONFIRM_REQUIRED,
    verification="systemctl poweroff initiated",
    failure_text="I couldn't shut down the system.",
    sensitive=True,
    executor="system_poweroff",
    router_only=True,
))

# --- Mobile & Phone Copilot Capabilities (Android Companion / ADB) ---
_register(Capability(
    name="phone_battery",
    description="Check the connected Android phone's battery percentage, charging state, and health via ADB.",
    schema={},
    verification="adb dumpsys battery reported state",
    failure_text="I couldn't check your phone's battery level.",
    executor="phone_battery",
))
_register(Capability(
    name="phone_ring",
    description="Ring the connected Android phone at maximum volume to help locate it.",
    schema={},
    verification="alarm ringtone triggered on phone",
    failure_text="I couldn't ring your phone.",
    executor="phone_ring",
))
_register(Capability(
    name="phone_camera_snap",
    description="Trigger a camera snap on the connected phone and load the photo into Lexi.",
    schema={},
    verification="photo captured by phone camera",
    failure_text="I couldn't take a photo from your phone.",
    executor="phone_camera_snap",
))
_register(Capability(
    name="phone_clipboard_sync",
    description="Sync text from desktop clipboard or message argument directly to connected Android phone.",
    schema={"text": Field("str", required=False, max_len=4000, allow_free_text=True)},
    verification="clipboard synced to phone companion app",
    failure_text="I couldn't sync clipboard to your phone.",
    executor="phone_clipboard_sync",
))

# --- Extended Hardware & System Capabilities ---
_register(Capability(
    name="system_resources",
    description="Check CPU usage, RAM utilization, load average, disk space, and temperatures.",
    schema={},
    verification="system status read from proc and sysfs",
    failure_text="I couldn't read system resources.",
    executor="system_resources",
))
_register(Capability(
    name="network_status",
    description="Check active Wi-Fi SSID, local IP address, gateway latency, and internet status.",
    schema={},
    verification="network interface and ping verified",
    failure_text="I couldn't check network status.",
    executor="network_status",
))
_register(Capability(
    name="wifi_list",
    description="Scan and list nearby available Wi-Fi networks with signal strength.",
    schema={},
    verification="nmcli wifi scan completed",
    failure_text="I couldn't scan Wi-Fi networks.",
    executor="wifi_list",
))
_register(Capability(
    name="bluetooth_control",
    description="Check bluetooth status or turn bluetooth radio on/off.",
    schema={"action": Field("enum", choices=("status", "on", "off"))},
    verification="bluetoothctl / rfkill state confirmed",
    failure_text="I couldn't manage bluetooth.",
    executor="bluetooth_control",
))
_register(Capability(
    name="clipboard_read",
    description="Read the current contents of the desktop clipboard.",
    schema={},
    verification="clipboard content read via wl-paste/xclip",
    failure_text="I couldn't read your clipboard.",
    executor="clipboard_read",
))
_register(Capability(
    name="focus_timer",
    description="Start, stop, or check status of the Dynamic Island focus / pomodoro timer.",
    schema={"action": Field("enum", choices=("start", "stop", "status")),
            "minutes": Field("int", required=False)},
    verification="timer state recorded and ticking",
    failure_text="I couldn't update the focus timer.",
    executor="focus_timer",
))
_register(Capability(
    name="clipboard_history",
    description="View clipboard history or copy an item back to the desktop clipboard.",
    schema={"action": Field("enum", choices=("list", "copy", "clear")),
            "index": Field("int", required=False)},
    verification="clipboard history queried or restored",
    failure_text="I couldn't access clipboard history.",
    executor="clipboard_history",
))
_register(Capability(
    name="download_status",
    description="Check the status and speed of active file downloads in ~/Downloads.",
    schema={},
    verification="download directory scanned",
    failure_text="I couldn't check active downloads.",
    executor="download_status",
))
_register(Capability(
    name="quick_notes",
    description="Take quick markdown notes, view recent notes, or clear notes.",
    schema={"action": Field("enum", choices=("add", "list", "clear")),
            "text": Field("str", required=False, max_len=500)},
    verification="note written or read from persistent storage",
    failure_text="I couldn't access quick notes.",
    executor="quick_notes",
    router_only=True,
))
_register(Capability(
    name="terminal_diagnose",
    description="Analyze the last terminal command and error to provide an explanation and fix.",
    schema={},
    verification="terminal history read and analyzed",
    failure_text="I couldn't diagnose the last terminal command.",
    executor="terminal_diagnose",
    router_only=True,
))
_register(Capability(
    name="port_control",
    description="Inspect which process is using a network port, or kill/free the port.",
    schema={"action": Field("enum", choices=("inspect", "kill")),
            "port": Field("int")},
    verification="network sockets queried via ss/lsof",
    failure_text="I couldn't inspect or free that port.",
    executor="port_control",
    router_only=True,
))
_register(Capability(
    name="top_processes",
    description="List top CPU/RAM consuming processes or terminate a runaway process.",
    schema={"action": Field("enum", choices=("list", "kill"), required=False),
            "sort_by": Field("enum", choices=("cpu", "mem"), required=False),
            "target": Field("str", required=False)},
    verification="process table queried or signal dispatched",
    failure_text="I couldn't inspect or manage processes.",
    executor="top_processes",
    router_only=True,
))
_register(Capability(
    name="dnd_control",
    description="Enable, disable, or query Do Not Disturb (DND) mode.",
    schema={"action": Field("enum", choices=("on", "off", "status", "toggle"))},
    verification="notification service state updated",
    failure_text="I couldn't change Do Not Disturb mode.",
    executor="dnd_control",
    router_only=True,
))
# Intentionally NOT registered: raw keystrokes, typing text, JavaScript, selectors.
# (Shell commands ARE available, but only through terminal_task and its policy.)


# -- Mapping legacy <action ...> tags from the model onto registry entries ----

SHELL_TAG_TYPES = ("command", "bash", "sh", "shell", "terminal")

# Tag types that must never be executed from model output.
REJECTED_MODEL_TYPES = {
    "key": "raw keyboard input from model output is not allowed",
    "hotkey": "raw keyboard input from model output is not allowed",
    "type": "raw typing from model output is not allowed",
    "write": "raw typing from model output is not allowed",
}

_URLISH_RE = re.compile(r"^(?:https?://)?(?:www\.)?([\w\-]+(?:\.[\w\-]+)+)(/[^\s]*)?$", re.I)


def model_action_to_structured(act: Dict[str, Any]) -> Tuple[Optional[Tuple[str, Dict[str, Any]]], Optional[str]]:
    """Translate one parsed <action> dict into (name, args), or return (None, reason).

    The result is NOT yet validated; call validate_structured() on it.
    """
    act_type = (act.get("type") or "").lower().strip()
    attrs = act.get("attrs") or {}
    body = (act.get("body") or "").strip()

    # Preferred form: <action name="volume_set">{"percent": 40}</action>
    reg_name = (attrs.get("name") or "").strip()
    if reg_name:
        if reg_name not in REGISTRY:
            return None, f"'{reg_name[:40]}' is not a supported action"
        args: Dict[str, Any] = {k: v for k, v in attrs.items() if k not in ("name", "type")}
        if body:
            try:
                parsed = json.loads(body)
            except ValueError:
                return None, f"{reg_name}: arguments were not valid JSON"
            if not isinstance(parsed, dict):
                return None, f"{reg_name}: arguments must be a JSON object"
            args.update(parsed)
        return (reg_name, args), None

    if act_type in REJECTED_MODEL_TYPES:
        return None, REJECTED_MODEL_TYPES[act_type]

    # Legacy compatibility adapter: <action type="command">cmd</action> -> terminal_task.
    # Safety is decided by terminal_task's policy, not by dropping the capability.
    if act_type in SHELL_TAG_TYPES:
        cmd = body or (attrs.get("cmd") or "").strip()
        if not cmd:
            return None, "the command was empty"
        return ("terminal_task", {"command": cmd}), None

    if act_type in ("volume", "vol", "audio"):
        level = (attrs.get("level") or body).strip().lower()
        if level in ("mute", "unmute", "toggle"):
            return ("volume_change", {"mode": "mute" if level != "unmute" else "unmute"}), None
        m = re.fullmatch(r"([+\-]?)(\d{1,3})\s*%?", level)
        if not m:
            return None, "unrecognised volume level"
        if m.group(1):
            return ("volume_change", {"mode": "up" if m.group(1) == "+" else "down", "step": int(m.group(2))}), None
        return ("volume_set", {"percent": int(m.group(2))}), None

    if act_type in ("brightness", "bright"):
        level = (attrs.get("level") or body).strip()
        m = re.fullmatch(r"([+\-]?)(\d{1,3})\s*%?", level)
        if not m:
            return None, "unrecognised brightness level"
        if m.group(1):
            return ("brightness_change", {"mode": "up" if m.group(1) == "+" else "down", "step": int(m.group(2))}), None
        return ("brightness_set", {"percent": int(m.group(2))}), None

    if act_type in ("lock", "lockscreen"):
        return ("lock_screen", {}), None

    if act_type in ("media", "player"):
        cmd = (attrs.get("command") or attrs.get("cmd") or body or "toggle").strip().lower()
        m = re.match(r"^(?:play|listen to)\s+(.+)$", cmd)
        if m:
            return ("spotify_play", {"query": m.group(1).strip()}), None
        norm = {"play-pause": "toggle", "resume": "play", "skip": "next", "prev": "previous", "back": "previous"}.get(cmd, cmd)
        return ("media_control", {"command": norm}), None

    if act_type == "browser":
        if (attrs.get("command") or "") == "youtube_play_fullscreen":
            return ("youtube_play", {"query": attrs.get("query", ""), "fullscreen": "yes"}), None
        return None, "unsupported browser command"

    if act_type in ("open", "launch", "run"):
        target = (body or attrs.get("target") or attrs.get("app") or attrs.get("url") or "").strip()
        if target.lower().startswith("spotify:search:"):
            return ("spotify_play", {"query": target[len("spotify:search:"):].strip()}), None
        if target.startswith("/") or target.startswith("~") or ":" in target.replace("://", "", 1) and not target.lower().startswith(("http://", "https://")):
            return None, "opening files, paths or custom URI schemes from model output is not allowed"
        m = _URLISH_RE.match(target)
        if m:
            return ("open_site", {"site": m.group(1) + (m.group(2) or "")}), None
        return ("open_app", {"name": target}), None

    if act_type in ("whatsapp", "wa"):
        contact = (attrs.get("contact") or attrs.get("to") or "").strip()
        message = body or attrs.get("message") or attrs.get("text") or ""
        if not contact:
            return None, "WhatsApp needs an exact contact name"
        # Model output can only ever DRAFT; sending needs the user's confirmation.
        return ("whatsapp_draft", {"contact": contact, "message": message}), None

    if act_type in ("mirror", "phone", "scrcpy"):
        return None, "phone mirroring is not available as a model action"

    return None, f"unsupported action type '{act_type or 'unknown'}'"


def validate_structured(name: str, args: Dict[str, Any]) -> Dict[str, Any]:
    cap = REGISTRY.get(name)
    if cap is None:
        raise SchemaError(f"'{name}' is not a registered action")
    return cap.validate(args)


def model_actions_to_plan(actions: List[Dict[str, Any]]) -> Tuple[List[Tuple[str, Dict[str, Any]]], List[str]]:
    """Return (accepted [(name, validated_args)], rejected reasons)."""
    accepted: List[Tuple[str, Dict[str, Any]]] = []
    rejected: List[str] = []
    for act in actions:
        mapped, reason = model_action_to_structured(act)
        if mapped is None:
            rejected.append(reason or "unsupported action")
            continue
        name, args = mapped
        try:
            accepted.append((name, validate_structured(name, args)))
        except SchemaError as exc:
            rejected.append(f"{name}: {exc}")
    return accepted, rejected
