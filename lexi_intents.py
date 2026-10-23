"""Deterministic intent router. Runs BEFORE the LLM.

Only high-confidence local intents are handled here.  Anything uncertain
either asks ONE concise question or falls through to the LLM.  Nothing
here ever produces a shell command, selector, JS or keystroke.
"""

from __future__ import annotations

import ast
import datetime
import operator
import re
import time
import urllib.parse
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from lexi_apps import resolve_app
from lexi_actions import (
    ActionRecord, FAILED, PendingConfirmation, can_retry,
)

# Route kinds
ACTION = "action"          # run registry action `name` with `args`
CONFIRM = "confirm"        # user explicitly confirmed the pending sensitive step
CANCEL = "cancel"          # user cancelled the pending step
CLARIFY = "clarify"        # ask one concise question; do nothing
REPLY = "reply"            # answer locally with `text`; do nothing
SEQUENCE = "sequence"      # ordered list of ACTION routes in `steps`
LLM = "llm"                # not a local intent; hand to the language model

MAX_ROUTED_LEN = 320       # raised slightly to allow "open X then close Y" type commands


@dataclass
class Route:
    kind: str
    name: str = ""
    args: Dict[str, Any] = field(default_factory=dict)
    text: str = ""
    retry_of: Optional[ActionRecord] = None
    steps: list = field(default_factory=list)   # used by SEQUENCE: list[Route]


# -- text normalisation --------------------------------------------------------

_LEAD_RE = re.compile(
    r"^(?:(?:hey|hi|ok(?:ay)?|yo)\s+)?(?:lexi[,:]?\s+)?"
    r"(?:(?:can|could|would|will)\s+(?:you|u|we)\s+(?:please\s+)?|"
    r"please\s+|kindly\s+|i\s+(?:want|need)\s+(?:you\s+)?to\s+|"
    r"i(?:'d| would)\s+like\s+(?:you\s+)?to\s+|i\s+wanna\s+|let'?s\s+|just\s+|"
    r"how\s+about\s+(?:we\s+)?|go\s+ahead\s+and\s+)*",
    re.I,
)
_TRAIL_RE = re.compile(r"(?:\s+(?:please|pls|plz|for me|right now|now|thanks|thank you))+[.!?]*$", re.I)


def normalise(text: str) -> str:
    t = (text or "").strip()
    t = re.sub(r"\s+", " ", t)
    t = t.rstrip("?!. ")
    for _ in range(2):                       # "hey lexi, can you please ..."
        t = _LEAD_RE.sub("", t, count=1).strip()
    t = _TRAIL_RE.sub("", t).strip()
    # Normalise common fused typing errors: "openc hrome" -> "open chrome", "openchrome" -> "open chrome"
    t = re.sub(r"\bopenc\s*hrome\b", "open chrome", t, flags=re.I)
    t = re.sub(r"\bopen([a-z]+)\b", lambda m: f"open {m.group(1)}" if m.group(1).lower() in ("chrome", "spotify", "firefox", "discord", "files", "terminal", "vlc") else m.group(0), t, flags=re.I)
    t = re.sub(r"\bclose([a-z]+)\b", lambda m: f"close {m.group(1)}" if m.group(1).lower() in ("chrome", "spotify", "firefox", "discord", "files", "terminal", "vlc") else m.group(0), t, flags=re.I)
    return t


_TOKEN_NOISE_RE = re.compile(r"\b(?:the|a|an|my|this|that|it)\b", re.I)


# -- patterns ---------------------------------------------------------------

_CONFIRM_RE = re.compile(r"(?:yes[, ]+)?(?:send(?: it| that| the message)?(?: now)?|confirm(?:ed)?|yes[, ]+send(?: it)?)", re.I)
_CONFIRM_CLOSE_RE = re.compile(r"(?:yes[, ]+)?confirm(?:ed)?|(?:yes[, ]+)?close (?:them|it|all)(?: all)?", re.I)
_CANCEL_RE = re.compile(r"(?:cancel|never ?mind|forget it|don'?t send(?: it)?|stop|no|nope|discard(?: it)?)", re.I)
_RETRY_RE = re.compile(r"(?:do it|do that|go ahead|try again|retry|again|once more|one more time|do it again|try that again|run it again)", re.I)

_VOL_ABS_RE = re.compile(r"(?:set |turn |change |put )?(?:the )?(?:sound |system |master )?volume (?:to|at|on) (\d{1,3}) ?%?$|volume (\d{1,3}) ?%?$|(?:set|make) (?:the )?volume (\d{1,3}) ?%?$", re.I)
_VOL_REL_RE = re.compile(
    r"(?:(?:make|turn|set|bump|crank) (?:it|the (?:sound|volume|music|song|audio)|this) (?:a (?:bit|little|lot|touch) )?(?P<dir1>louder|quieter|softer|up|down|lower|higher)"
    r"|(?:(?P<dir2>louder|quieter|softer)(?: please)?)"
    r"|(?:volume (?P<dir3>up|down))"
    r"|(?:(?P<dir4>increase|decrease|raise|lower|reduce) (?:the )?volume)"
    r"|(?:turn (?:the )?(?:volume |sound |it )?(?P<dir5>up|down))"
    r"|(?:(?P<dir6>increase|decrease|raise|lower|reduce) (?:the )?volume by (?P<n>\d{1,2}) ?%?))"
    r"(?: by (?P<by>\d{1,2}) ?%?)?$",
    re.I,
)
_MUTE_RE = re.compile(r"(mute|unmute)(?: (?:the )?(?:sound|volume|audio|music|computer|laptop|pc))?$", re.I)
_BRIGHT_ABS_RE = re.compile(r"(?:set |turn |change |put )?(?:the )?(?:screen |display )?brightness (?:to|at) (\d{1,3}) ?%?$|brightness (\d{1,3}) ?%?$", re.I)
_BRIGHT_REL_RE = re.compile(
    r"(?:(?:make|turn) (?:it|the (?:screen|display)|this) (?:a (?:bit|little|lot) )?(?P<d1>brighter|dimmer|darker)"
    r"|(?P<d2>brighter|dimmer)"
    r"|brightness (?P<d3>up|down)"
    r"|(?P<d4>increase|decrease|raise|lower|reduce|dim) (?:the )?(?:screen |display )?brightness"
    r"|dim (?:the )?(?:screen|display))"
    r"(?: by (?P<by>\d{1,2}) ?%?)?$",
    re.I,
)
_LOCK_RE = re.compile(r"lock(?: (?:the |my )?(?:screen|computer|laptop|pc|system|session))?(?: up)?$", re.I)
_MEDIA_RE = {
    "pause": re.compile(r"(?:pause|hold)(?: (?:the )?(?:music|song|track|video|playback|it|this))?$", re.I),
    "play": re.compile(r"(?:play|resume|unpause|continue)(?: (?:the )?(?:music|song|track|video|playback|it))?$|(?:start|resume) (?:the )?(?:music|playback)$", re.I),
    "next": re.compile(r"(?:next|skip)(?: (?:the |this )?(?:song|track|one|video))?$|play (?:the )?next(?: song| track| one)?$", re.I),
    "previous": re.compile(r"(?:previous|prev|go back|last (?:song|track))(?: (?:song|track|one))?$|play (?:the )?previous(?: song| track| one)?$", re.I),
    "stop": re.compile(r"stop (?:the )?(?:music|song|playback|playing)$", re.I),
}
_PLAY_RE = re.compile(r"(?:play|put on|listen to|stream)\s+(?P<q>.+)$", re.I)
_CLOSE_RE = re.compile(r"(?:close|quit|kill)\s+(?P<t>.+)$", re.I)
_CHROME_NAME_RE = re.compile(r"(?:google )?chrome(?: browser)?", re.I)
_WA_NAME_RE = re.compile(r"\bwhats ?app\b", re.I)
_IG_NAME_RE = re.compile(r"\binsta(?:gram)?\b", re.I)
_CLOSE_ALL_RE = re.compile(
    r"(?:all|everything|them all|all of (?:them|it)|all (?:the |my )?(?:windows|apps|pages|sites))"
    r"(?: (?:windows|apps|pages|sites))?", re.I)
_CLOSE_TABS_ALL_RE = re.compile(
    r"^(?:(?:both|all|all\s+my|social)\s+tabs"
    r"|(?:all\s+(?:the\s+|my\s+)?tabs\s+(?:in|on)\s+(?:chrome|the\s+browser|browser))"
    r"|(?:whats ?app|wa)\s+(?:and|&)\s+insta(?:gram)?"
    r"|insta(?:gram)?\s+(?:and|&)\s+(?:whats ?app|wa)"
    r"|(?:both\s+)?(?:whats ?app|wa)\s+(?:and|&)\s+insta(?:gram)?\s+tabs?"
    r")$",
    re.I,
)
_CLOSE_ALL_APPS_RE = re.compile(
    r"^(?:(?:close|quit|kill)\s+(?:all\s+)?running\s+(?:apps?|processes?|programs?)"
    r"|(?:close|quit|kill)\s+(?:all|everything|them\s+all|all\s+of\s+(?:them|it))(?:\s+(?:my\s+)?(?:apps?|windows|programs?))?"
    r"|(?:close|quit|kill)\s+all\s+(?:the\s+|my\s+)?(?:apps?|windows|programs?)"
    r"|(?:whats|what\s+is|what's)\s+running\s+and\s+close)$",
    re.I,
)
# Catches "whats running", "what is running"
_LIST_RUNNING_RE = re.compile(
    r"(?:what(?:'?s| is)\s+(?:running|open|currently\s+running|currently\s+open)"
    r"|(?:list|show|check)\s+(?:running|open|all)\s+(?:apps?|processes?|programs?))", re.I)
_CLOSE_VAGUE_RE = re.compile(r"(?:the |this |that |my |current |open )*(?:tab|tabs|window|windows|page|pages)", re.I)
CLOSE_ALL_PROMPT = (
    "This will close every WhatsApp and Instagram tab in Chrome (only those two sites, nothing else). "
    "Say “confirm” to close them, or “cancel”."
)
CLOSE_UNSUPPORTED_TEXT = (
    "I can close Chrome, Spotify, Files, Terminal, Firefox, Discord, VS Code, and VLC, plus WhatsApp and Instagram tabs in Chrome. "
    "I don't have a way to close anything else yet."
)
_OPEN_RE = re.compile(
    r"(?:open(?:\s+up)?|launch|start|run|go\s+to|pull\s+up|bring\s+up|switch\s+to|show(?:\s+me)?|take\s+me\s+to|check|display)\s+(?P<t>.+)$",
    re.I
)

_PHONE_BATTERY_RE = re.compile(
    r"^(?:(?:what(?:\x27s| is)|check)\s+(?:the\s+|my\s+)?)?phone(?:'?s)?\s+battery(?:\s+(?:percentage|level|status|life|health))?$"
    r"|^(?:check\s+)?battery\s+(?:on|of)\s+(?:my\s+|the\s+)?phone$"
    r"|^(?:how\s+much\s+)?phone\s+battery(?:\s+do\s+i\s+have)?$",
    re.I
)

_PHONE_RING_RE = re.compile(
    r"^(?:ring|find|buzz|locate|call)\s+(?:my\s+|the\s+)?phone$"
    r"|^(?:where\s+is\s+my\s+phone|ping\s+my\s+phone)$",
    re.I
)

_PHONE_SNAP_RE = re.compile(
    r"^(?:take|snap|capture)\s+(?:a\s+)?(?:photo|pic|picture|camera\s+snap)\s+(?:from|with|using)\s+(?:my\s+|the\s+)?phone$"
    r"|^(?:phone\s+camera\s+snap|snap\s+phone\s+camera)$",
    re.I
)

_PHONE_CLIPBOARD_RE = re.compile(
    r"^(?:push|sync|send|copy)\s+(?:the\s+|my\s+)?clipboard\s+to\s+(?:my\s+|the\s+)?phone$"
    r"|^sync\s+clipboard$",
    re.I
)

_SYS_RESOURCES_RE = re.compile(
    r"^(?:(?:check|what(?:\x27s| is))\s+)?(?:system\s+|cpu\s+|ram\s+|memory\s+|disk\s+)?(?:resources|usage|stats|load)$"
    r"|^(?:how\s+is\s+my\s+cpu|what\s+is\s+eating\s+my\s+(?:cpu|ram|memory|disk))$"
    r"|^(?:check\s+)?(?:disk\s+space|cpu\s+usage|ram\s+usage)$",
    re.I
)

_NETWORK_STATUS_RE = re.compile(
    r"^(?:(?:check|what(?:\x27s| is))\s+)?(?:my\s+)?(?:network|wifi|wi-fi|internet)(?:\s+(?:status|connection|speed))?$"
    r"|^(?:what(?:\x27s| is)\s+my\s+ip(?:\s+address)?)$"
    r"|^(?:am\s+i\s+connected\s+to\s+(?:the\s+)?internet)$",
    re.I
)

_WIFI_LIST_RE = re.compile(
    r"^(?:scan|list|show|find|search)\s+(?:available\s+|nearby\s+)?(?:wi-?fi|wifi)(?:\s+networks)?$",
    re.I
)

_BLUETOOTH_RE = re.compile(
    r"^(?:turn|switch)\s+bluetooth\s+(?P<act1>on|off)$"
    r"|^bluetooth\s+(?P<act2>on|off)$"
    r"|^(?:check\s+)?bluetooth(?:\s+status)?$",
    re.I
)

_CLIPBOARD_READ_RE = re.compile(
    r"^(?:what(?:\x27s| is)\s+(?:on|in)\s+(?:the\s+|my\s+)?clipboard|read\s+(?:the\s+|my\s+)?clipboard|show\s+(?:the\s+|my\s+)?clipboard|paste\s+clipboard)$",
    re.I
)

_CLIPBOARD_HIST_RE = re.compile(
    r"^(?:(?:show|view|check|get|list|open)\s+)?(?:the\s+|my\s+)?clipboard(?:\s+history|\s+vault)?$"
    r"|^(?:recent|last)\s+(?:copied|clipboard)(?:\s+(?:items|snippets|history))?$"
    r"|^clipboard$",
    re.I
)
_CLIPBOARD_CLOSE_RE = re.compile(
    r"^(?:close|quit|hide|dismiss)\s+(?:the\s+|my\s+)?clipboard(?:\s+history|\s+vault)?$",
    re.I
)
_CLIPBOARD_RESTORE_RE = re.compile(
    r"^(?:copy|restore)\s+(?:clipboard\s+)?item\s+(?P<idx>\d+)$",
    re.I
)

_FOCUS_RE = re.compile(
    r"^(?:(?:start\s+)?(?:focus|pomodoro)(?:\s+(?:mode|session|timer))?(?:\s+(?:for\s+)?(?P<m1>\d+)\s*(?:mins?|minutes?|m)?)?"
    r"|timer\s+(?P<m2>\d+)\s*(?:mins?|minutes?|m)?)$",
    re.I
)
_FOCUS_STOP_RE = re.compile(r"^(?:stop|cancel|end)\s+(?:focus|pomodoro|timer)$", re.I)
_FOCUS_STATUS_RE = re.compile(r"^(?:focus|pomodoro|timer)\s+status$", re.I)

_DOWNLOADS_STATUS_RE = re.compile(
    r"^(?:(?:check|what(?:'s| is))\s+)?downloads?(?:\s+status)?$"
    r"|^(?:what\s+is|is\s+anything)\s+downloading$",
    re.I
)

_NOTES_ADD_RE = re.compile(
    r"^(?:(?:take|write|add|save|make)(?:\s+(?:a|down))?\s+note(?::|\s+that|\s+to|\s+saying)?|note:?)\s+(?P<txt>.+)$",
    re.I
)
_NOTES_LIST_RE = re.compile(
    r"^(?:(?:show|view|check|get|list|open|read)\s+)?(?:my\s+)?notes$"
    r"|^(?:quick\s+)?notes(?:\s+list)?$"
    r"|^(?:what\s+are\s+)?my\s+notes$",
    re.I
)
_NOTES_CLEAR_RE = re.compile(
    r"^(?:clear|delete|erase|wipe|remove)\s+(?:all\s+)?(?:my\s+)?notes$",
    re.I
)
_TERMINAL_DIAG_RE = re.compile(
    r"^(?:why\s+did\s+my\s+command\s+fail|fix\s+(?:my\s+|the\s+)?last\s+command|fix\s+(?:the\s+)?(?:terminal\s+)?error|diagnose\s+(?:the\s+)?(?:last\s+)?error|what\s+was\s+my\s+error)$",
    re.I
)

_PORT_KILL_RE = re.compile(
    r"^(?:kill|free|terminate|close|clear)\s+port\s+(?P<port>\d+)$",
    re.I
)
_PORT_INSPECT_RE = re.compile(
    r"^(?:(?:check|inspect|who\s+is\s+using|what\s+is\s+on|what(?:\x27s| is)\s+running\s+on)\s+)?port\s+(?P<port>\d+)$"
    r"|^(?:who|what)\s+is\s+using\s+port\s+(?P<port2>\d+)$",
    re.I
)

_TOP_PROCS_RE = re.compile(
    r"^(?:(?:show|list|get|check)\s+)?top\s+(?P<sort>cpu|ram|memory)?\s*processes$"
    r"|^(?:what(?:\x27s| is)\s+(?:eating|using|consuming)\s+(?:my\s+)?(?P<sort2>cpu|ram|memory|pc))$"
    r"|^(?:what(?:\x27s| is)\s+lagging\s+(?:my\s+)?(?:pc|laptop|system))$"
    r"|^(?P<sort3>cpu|ram|memory)\s+hogs$"
    r"|^top\s+processes$",
    re.I
)
_KILL_PROC_RE = re.compile(
    r"^(?:kill|terminate|stop)\s+(?:process|pid)\s+(?P<target>[a-zA-Z0-9_\-\.]+)$",
    re.I
)

_DND_ON_RE = re.compile(
    r"^(?:turn\s+on|enable|activate|start)\s+(?:dnd|do\s+not\s+disturb)$"
    r"|^(?:dnd|do\s+not\s+disturb)\s+on$"
    r"|^mute\s+notifications$"
    r"|^dnd$",
    re.I
)
_DND_OFF_RE = re.compile(
    r"^(?:turn\s+off|disable|deactivate|stop)\s+(?:dnd|do\s+not\s+disturb)$"
    r"|^(?:dnd|do\s+not\s+disturb)\s+off$"
    r"|^unmute\s+notifications$",
    re.I
)
_DND_STATUS_RE = re.compile(
    r"^(?:check\s+)?dnd\s+status$|^(?:is\s+)?dnd\s+(?:on|active)\??$",
    re.I
)

_BATTERY_RE = re.compile(
    r"^(?:(?:what(?:\x27s| is)|check)\s+(?:the\s+|my\s+)?)?battery(?:\s+(?:percentage|level|status|life|health))?$"
    r"|^(?:how\s+much\s+)?battery(?:\s+do\s+i\s+have)?$"
    r"|^(?:is\s+(?:the|my)\s+laptop\s+charging)$",
    re.I
)

_NOW_PLAYING_RE = re.compile(
    r"^(?:what(?:\x27s| is)\s+(?:playing|this\s+song|the\s+song|the\s+track|this\s+music)"
    r"|what\s+(?:song|track|music)\s+is\s+playing"
    r"|who\s+is\s+singing(?:\s+this)?"
    r"|now\s+playing|current\s+song|current\s+track)$",
    re.I
)

_TIME_DATE_RE = re.compile(
    r"^(?:what(?:\x27s| is)\s+the\s+time|what\s+time\s+is\s+it|current\s+time|"
    r"what(?:\x27s| is)\s+(?:today(?:\x27s)?\s+date|the\s+date(?:\s+today)?)|today(?:\x27s)?\s+date|"
    r"what(?:\x27s| is)\s+(?:the\s+day|today(?:\x27s)?\s+day)|what\s+day\s+is\s+(?:it|today))$",
    re.I
)

_SCREENSHOT_RE = re.compile(
    r"^(?:take(?:\s+a)?\s+)?screenshot(?:\s+(?:of\s+)?(?:the\s+|my\s+)?(?:screen|desktop))?$"
    r"|^(?:capture|grab)(?:\s+the)?\s+screen$",
    re.I
)

_SLEEP_RE = re.compile(r"^(?:put(?:\s+(?:the|my))?\s+computer\s+to\s+)?(?:sleep|suspend)$", re.I)
_RESTART_RE = re.compile(r"^(?:restart|reboot)(?:\s+(?:(?:the|my)\s+)?(?:computer|laptop|pc|system))?$", re.I)
_POWEROFF_RE = re.compile(r"^(?:shut\s*down|power\s*off)(?:\s+(?:(?:the|my)\s+)?(?:computer|laptop|pc|system))?$", re.I)

_MEET_RE = re.compile(r"^(?:start|open|new|create)(?:\s+a)?\s+(?:google\s+)?meet(?:ing)?$", re.I)
_DOCS_RE = re.compile(r"^(?:new|create(?:\s+a)?)\s+(?:google\s+)?doc(?:ument)?$", re.I)
_SHEETS_RE = re.compile(r"^(?:new|create(?:\s+a)?)\s+(?:google\s+)?sheet(?:s)?$", re.I)
_SLIDES_RE = re.compile(r"^(?:new|create(?:\s+a)?)\s+(?:google\s+)?slide(?:s)?$", re.I)

_MAPS_RE = re.compile(r"^(?:(?:directions|maps?|navigate)(?:\s+to)?|where\s+is)\s+(?P<dest>.+)$", re.I)
_WEATHER_RE = re.compile(r"^(?:what(?:\x27s| is)\s+(?:the\s+)?)?weather(?:\s+today)?(?:\s+(?:in|for|at)\s+(?P<loc>.+))?$", re.I)
_DEFINE_RE = re.compile(r"^(?:define|meaning\s+of)\s+(?P<word>[\w\-]+)$", re.I)
_THANKS_RE = re.compile(r"^(?:thanks|thank\s+you)(?:\s+lexi|\s+so\s+much|\s+a\s+lot)?$", re.I)
_CREATOR_RE = re.compile(
    r"^(?:who\s+(?:made|created|built|designed|developed|programmed)\s+you"
    r"|who\s+is\s+(?:your\s+)?(?:creator|maker|builder|developer|architect|boss|owner|master)"
    r"|who\s+is\s+shakeel(?:\s+to\s+you)?"
    r"|do\s+you\s+know\s+shakeel"
    r"|who\s+owns\s+you)$",
    re.I,
)
_IDENTITY_RE = re.compile(r"^(?:who\s+are\s+you|what\s+is\s+your\s+name)$", re.I)
_HELP_RE = re.compile(r"^(?:help|what\s+can\s+you\s+do|capabilities)$", re.I)

# WhatsApp draft requests. Order matters: most specific first.
_WA_PATTERNS = [
    re.compile(r"(?:open (?:whatsapp )?(?:and )?)?send\s+(?:a\s+)?(?:whatsapp\s+)?(?:message\s+)?(?:saying\s+|that says\s+)?(?P<m>.+?)\s+to\s+(?P<c>.+?)(?:\s+(?:on|in|via|through)\s+whatsapp(?: web)?)?$", re.I),
    re.compile(r"(?:whatsapp|wa)\s+(?P<c>[\w .'\-]{1,60}?)\s+(?:saying\s+|that\s+says\s+|:\s*)?(?P<m>.+)$", re.I),
    re.compile(r"(?:message|text|msg)\s+(?P<c>[\w .'\-]{1,60}?)\s+(?:saying\s+|that\s+says\s+|:\s*)(?P<m>.+?)(?:\s+on whatsapp)?$", re.I),
    re.compile(r"tell\s+(?P<c>[\w .'\-]{1,60}?)\s+(?:on whatsapp\s+)?(?:that\s+)?(?P<m>.+?)(?:\s+on whatsapp)?$", re.I),
]
_NOT_WHATSAPP_RE = re.compile(
    r"\b(?:email|e-mail|mail|file|files|document|pdf|photo|picture|money|payment|\$|rupees?|inr|"
    r"chatgpt|claude|gemini|openai|copilot|ai|terminal|bash|shell)\b",
    re.I
)

_KNOWN_SITES = {
    "youtube": "youtube.com", "yt": "youtube.com",
    "gmail": "mail.google.com", "google mail": "mail.google.com",
    "github": "github.com",
    "google": "google.com", "maps": "maps.google.com", "google maps": "maps.google.com",
    "drive": "drive.google.com", "google drive": "drive.google.com",
    "reddit": "reddit.com", "twitter": "x.com", "x": "x.com",
    "netflix": "netflix.com", "wikipedia": "wikipedia.org",
    "instagram": "instagram.com", "insta": "instagram.com",
    "chatgpt": "chatgpt.com", "claude": "claude.ai",
    "meet": "meet.google.com/new", "google meet": "meet.google.com/new",
    "docs": "docs.new", "google docs": "docs.new",
    "sheets": "sheets.new", "google sheets": "sheets.new",
    "slides": "slides.new", "google slides": "slides.new",
    "bookmyshow": "in.bookmyshow.com", "book my show": "in.bookmyshow.com", "bms": "in.bookmyshow.com",
    "amazon": "amazon.in", "flipkart": "flipkart.com",
    "swiggy": "swiggy.com", "zomato": "zomato.com",
}

_KNOWN_DIRECT_LAUNCH = {
    "web": ("open_app", {"name": "chrome"}),
    "the web": ("open_app", {"name": "chrome"}),
    "web browser": ("open_app", {"name": "chrome"}),
    "browser": ("open_app", {"name": "chrome"}),
    "internet": ("open_app", {"name": "chrome"}),
    "whatsapp": ("open_app", {"name": "whatsapp"}),
    "whatsapp web": ("open_app", {"name": "whatsapp"}),
    "wa": ("open_app", {"name": "whatsapp"}),
    "spotify": ("open_app", {"name": "spotify"}),
    "chrome": ("open_app", {"name": "chrome"}),
    "google chrome": ("open_app", {"name": "chrome"}),
    "terminal": ("open_app", {"name": "terminal"}),
    "console": ("open_app", {"name": "terminal"}),
    "files": ("open_app", {"name": "files"}),
    "file manager": ("open_app", {"name": "files"}),
    "calculator": ("open_app", {"name": "calculator"}),
    "calc": ("open_app", {"name": "calculator"}),
    "vscode": ("open_app", {"name": "code"}),
    "vs code": ("open_app", {"name": "code"}),
    "code": ("open_app", {"name": "code"}),
    "settings": ("open_app", {"name": "settings"}),
}
_SITE_LIKE_RE = re.compile(r"^(?:https?://)?(?:www\.)?([\w\-]+(?:\.[\w\-]+)+)(/[^\s]*)?$", re.I)

_COUNT_WORDS = {
    "1": 1, "one": 1, "a": 1,
    "2": 2, "two": 2, "a couple": 2, "a couple of": 2, "couple": 2, "both": 2,
    "3": 3, "three": 3,
    "4": 4, "four": 4,
    "5": 5, "five": 5,
    "6": 6, "six": 6,
    "7": 7, "seven": 7,
    "8": 8, "eight": 8,
    "9": 9, "nine": 9,
    "10": 10, "ten": 10,
}

_OPEN_N_APPS_RE = re.compile(
    r"^(?:open(?:\s+up)?|launch|start|run)\s+(?P<n>\d+|one|two|three|four|five|six|seven|eight|nine|ten|a couple(?: of)?)\s+"
    r"(?:apps?|applications?|things?|programs?|windows?|tools?)"
    r"(?:\s+(?:in|on)\s+(?:my\s+)?(?:lap|laptop|computer|system|pc|desktop))?"
    r"(?:\s+for\s+me)?$",
    re.I
)

# Top reliable desktop applications on the user's system to open when multi-apps are requested
_TOP_DESKTOP_APPS = [
    "chrome",
    "files",
    "terminal",
    "calculator",
    "spotify",
    "gnome-text-editor",
]


# -- helpers -----------------------------------------------------------------

def _family_of(rec: Optional[ActionRecord]) -> str:
    if rec is None or (time.time() - (rec.finished or rec.started)) > 900:
        return ""
    if rec.name.startswith("volume") or rec.name in ("spotify_play", "youtube_play", "media_control"):
        return "audio"
    if rec.name.startswith("brightness"):
        return "display"
    return ""


def _clean_query(q: str) -> str:
    q = q.strip().strip("\"'`")
    q = re.sub(r"\b(?:on|in|using|via|with)\s+(?:the\s+)?(?:spotify|youtube|yt)\b", "", q, flags=re.I)
    q = re.sub(r"\b(?:the\s+)?(?:song|track|music|video)\s+(?=\S)", "", q, count=1, flags=re.I)
    return re.sub(r"\s+", " ", q).strip(" ,.")


_SEARCH_SERVICES = {
    "chatgpt": ("ChatGPT", "chatgpt.com/?q={q}"),
    "claude": ("Claude", "claude.ai/new?q={q}"),
    "google": ("Google", "google.com/search?q={q}"),
    "youtube": ("YouTube", "youtube.com/results?search_query={q}"),
    "yt": ("YouTube", "youtube.com/results?search_query={q}"),
    "reddit": ("Reddit", "reddit.com/search/?q={q}"),
    "github": ("GitHub", "github.com/search?q={q}"),
    "duckduckgo": ("DuckDuckGo", "duckduckgo.com/?q={q}"),
    "ddg": ("DuckDuckGo", "duckduckgo.com/?q={q}"),
    "bing": ("Bing", "bing.com/search?q={q}"),
    "wikipedia": ("Wikipedia", "en.wikipedia.org/wiki/Special:Search?search={q}"),
    "wiki": ("Wikipedia", "en.wikipedia.org/wiki/Special:Search?search={q}"),
    "bookmyshow": ("BookMyShow", "google.com/search?q={q}+bookmyshow"),
    "amazon": ("Amazon", "amazon.in/s?k={q}"),
    "flipkart": ("Flipkart", "flipkart.com/search?q={q}"),
    "netflix": ("Netflix", "netflix.com/search?q={q}"),
    "swiggy": ("Swiggy", "google.com/search?q={q}+swiggy"),
    "zomato": ("Zomato", "google.com/search?q={q}+zomato"),
    "maps": ("Google Maps", "maps.google.com/maps?q={q}"),
}


def _clean_search_query(q: str) -> str:
    q = (q or "").strip().strip("\"'`")
    q = re.sub(r"\s+(?:in|on|with|using)\s+(?:chrome|the browser|browser)$", "", q, flags=re.I).strip()
    return q


_MATH_OPS = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
    ast.Div: operator.truediv, ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod, ast.Pow: operator.pow,
    ast.USub: operator.neg, ast.UAdd: operator.pos,
}


def _eval_node(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    elif isinstance(node, ast.BinOp):
        left = _eval_node(node.left)
        right = _eval_node(node.right)
        op_type = type(node.op)
        if op_type in _MATH_OPS:
            return _MATH_OPS[op_type](left, right)
    elif isinstance(node, ast.UnaryOp):
        operand = _eval_node(node.operand)
        op_type = type(node.op)
        if op_type in _MATH_OPS:
            return _MATH_OPS[op_type](operand)
    raise ValueError("Unsupported operation")


def _eval_math(text: str) -> Optional[str]:
    clean = re.sub(r"^(?:calculate|what(?:\x27s| is))\s+", "", text.strip(), flags=re.I).strip(" =?")
    m_pct = re.match(r"^(\d+(?:\.\d+)?)\s*%\s+of\s+(\d+(?:\.\d+)?)$", clean, re.I)
    if m_pct:
        pct, val = float(m_pct.group(1)), float(m_pct.group(2))
        res = (pct / 100.0) * val
        return f"{pct:g}% of {val:g} = {res:g}"

    clean_expr = clean.replace("^", "**").replace("x", "*").replace("×", "*").replace("÷", "/")
    if not re.search(r"[\+\-\*\/\%]", clean_expr):
        return None
    try:
        tree = ast.parse(clean_expr, mode="eval")
        val = _eval_node(tree.body)
        return f"{clean} = {val:g}"
    except ZeroDivisionError:
        return "Division by zero is undefined."
    except Exception:
        return None


_CURRENCY_RATES = {
    "usd": 1.0,
    "inr": 84.4,
    "eur": 0.92,
    "gbp": 0.77,
    "jpy": 152.0,
    "cad": 1.38,
    "aud": 1.51,
    "aed": 3.67,
}

_UNIT_CATEGORIES = {
    # length (base: meters)
    "m": ("length", 1.0, "m", "📏"), "meter": ("length", 1.0, "meters", "📏"), "meters": ("length", 1.0, "meters", "📏"),
    "km": ("length", 1000.0, "km", "📏"), "kms": ("length", 1000.0, "km", "📏"), "kilometer": ("length", 1000.0, "km", "📏"), "kilometers": ("length", 1000.0, "km", "📏"),
    "mi": ("length", 1609.344, "miles", "📏"), "mile": ("length", 1609.344, "miles", "📏"), "miles": ("length", 1609.344, "miles", "📏"),
    "ft": ("length", 0.3048, "ft", "📏"), "feet": ("length", 0.3048, "ft", "📏"), "foot": ("length", 0.3048, "ft", "📏"),
    "in": ("length", 0.0254, "inches", "📏"), "inch": ("length", 0.0254, "inches", "📏"), "inches": ("length", 0.0254, "inches", "📏"),
    "cm": ("length", 0.01, "cm", "📏"), "centimeter": ("length", 0.01, "cm", "📏"), "centimeters": ("length", 0.01, "cm", "📏"),
    # mass (base: grams)
    "g": ("mass", 1.0, "g", "⚖️"), "gram": ("mass", 1.0, "grams", "⚖️"), "grams": ("mass", 1.0, "grams", "⚖️"),
    "kg": ("mass", 1000.0, "kg", "⚖️"), "kgs": ("mass", 1000.0, "kg", "⚖️"), "kilogram": ("mass", 1000.0, "kg", "⚖️"), "kilograms": ("mass", 1000.0, "kg", "⚖️"),
    "lb": ("mass", 453.59237, "lbs", "⚖️"), "lbs": ("mass", 453.59237, "lbs", "⚖️"), "pound": ("mass", 453.59237, "lbs", "⚖️"), "pounds": ("mass", 453.59237, "lbs", "⚖️"),
    "oz": ("mass", 28.3495, "oz", "⚖️"), "ounce": ("mass", 28.3495, "oz", "⚖️"), "ounces": ("mass", 28.3495, "oz", "⚖️"),
    # data (base: bytes)
    "b": ("data", 1, "B", "💾"), "byte": ("data", 1, "bytes", "💾"), "bytes": ("data", 1, "bytes", "💾"),
    "kb": ("data", 1024, "KB", "💾"), "kilobyte": ("data", 1024, "KB", "💾"), "kilobytes": ("data", 1024, "KB", "💾"),
    "mb": ("data", 1024**2, "MB", "💾"), "megabyte": ("data", 1024**2, "MB", "💾"), "megabytes": ("data", 1024**2, "MB", "💾"),
    "gb": ("data", 1024**3, "GB", "💾"), "gigabyte": ("data", 1024**3, "GB", "💾"), "gigabytes": ("data", 1024**3, "GB", "💾"),
    "tb": ("data", 1024**4, "TB", "💾"), "terabyte": ("data", 1024**4, "TB", "💾"), "terabytes": ("data", 1024**4, "TB", "💾"),
}

_CITY_TIMEZONES = {
    "tokyo": ("Tokyo", 9.0, "JST"),
    "japan": ("Tokyo", 9.0, "JST"),
    "london": ("London", 0.0, "GMT/BST"),
    "uk": ("London", 0.0, "GMT/BST"),
    "new york": ("New York", -5.0, "EST/EDT"),
    "ny": ("New York", -5.0, "EST/EDT"),
    "sydney": ("Sydney", 11.0, "AEDT"),
    "australia": ("Sydney", 11.0, "AEDT"),
    "dubai": ("Dubai", 4.0, "GST"),
    "uae": ("Dubai", 4.0, "GST"),
    "paris": ("Paris", 1.0, "CET"),
    "france": ("Paris", 1.0, "CET"),
    "berlin": ("Berlin", 1.0, "CET"),
    "germany": ("Berlin", 1.0, "CET"),
    "singapore": ("Singapore", 8.0, "SGT"),
    "los angeles": ("Los Angeles", -8.0, "PST/PDT"),
    "la": ("Los Angeles", -8.0, "PST/PDT"),
    "san francisco": ("San Francisco", -8.0, "PST/PDT"),
    "california": ("San Francisco", -8.0, "PST/PDT"),
    "toronto": ("Toronto", -5.0, "EST/EDT"),
    "canada": ("Toronto", -5.0, "EST/EDT"),
    "mumbai": ("Mumbai", 5.5, "IST"),
    "delhi": ("Delhi", 5.5, "IST"),
    "india": ("India", 5.5, "IST"),
    "beijing": ("Beijing", 8.0, "CST"),
    "china": ("Beijing", 8.0, "CST"),
    "utc": ("UTC", 0.0, "UTC"),
    "gmt": ("GMT", 0.0, "GMT"),
}


def _eval_conversions(text: str) -> Optional[str]:
    clean = text.strip().lower()

    # 1. World Time / Timezones
    m_tz = re.match(r"^(?:(?:what(?:\x27s| is)\s+(?:the\s+)?)?time\s+(?:in|at)\s+|time\s+)(?P<city>[a-zA-Z\s]+)$", clean)
    if m_tz:
        city_key = m_tz.group("city").strip().lower()
        if city_key in _CITY_TIMEZONES:
            city_name, offset, tz_abbr = _CITY_TIMEZONES[city_key]
            now_utc = datetime.datetime.now(datetime.timezone.utc)
            city_time = now_utc + datetime.timedelta(hours=offset)
            time_str = city_time.strftime("%I:%M %p").lstrip("0")
            date_str = city_time.strftime("%A, %B %d")
            return f"🕒 The current time in {city_name} is {time_str} ({tz_abbr}, {date_str})."

    # 2. Currency Conversions (e.g. "100 usd in inr", "50 eur to usd")
    m_curr = re.match(r"^(?:convert\s+)?(?P<val>\d+(?:\.\d+)?)\s*(?P<from>[a-zA-Z]{3})\s+(?:in|to|into)\s+(?P<to>[a-zA-Z]{3})$", clean)
    if m_curr:
        c_from = m_curr.group("from").lower()
        c_to = m_curr.group("to").lower()
        if c_from in _CURRENCY_RATES and c_to in _CURRENCY_RATES:
            val = float(m_curr.group("val"))
            rate_from = _CURRENCY_RATES[c_from]
            rate_to = _CURRENCY_RATES[c_to]
            converted = (val / rate_from) * rate_to
            eff_rate = rate_to / rate_from
            return f"💱 {val:g} {c_from.upper()} ≈ {converted:,.2f} {c_to.upper()} (approx. rate ~{eff_rate:,.2f})"

    # 3. Temperature Conversions (e.g. "100 c in f", "86 f in c")
    m_temp = re.match(r"^(?:convert\s+)?(?P<val>-?\d+(?:\.\d+)?)\s*(?:°\s*)?(?P<from>c|celsius|f|fahrenheit)\s+(?:in|to|into)\s+(?:°\s*)?(?P<to>c|celsius|f|fahrenheit)$", clean)
    if m_temp:
        val = float(m_temp.group("val"))
        t_from = m_temp.group("from")[0]
        t_to = m_temp.group("to")[0]
        if t_from == "c" and t_to == "f":
            res = (val * 9.0 / 5.0) + 32.0
            return f"🌡️ {val:g}°C = {res:g}°F"
        elif t_from == "f" and t_to == "c":
            res = (val - 32.0) * 5.0 / 9.0
            return f"🌡️ {val:g}°F = {res:g}°C"

    # 4. Units & Data (e.g. "100 km in miles", "10 kg in lbs", "100 gb in mb")
    m_unit = re.match(r"^(?:convert\s+)?(?P<val>\d+(?:\.\d+)?)\s*(?P<from>[a-zA-Z]+)\s+(?:in|to|into)\s+(?P<to>[a-zA-Z]+)$", clean)
    if m_unit:
        u_from = m_unit.group("from").lower()
        u_to = m_unit.group("to").lower()
        if u_from in _UNIT_CATEGORIES and u_to in _UNIT_CATEGORIES:
            cat1, factor1, name1, sym = _UNIT_CATEGORIES[u_from]
            cat2, factor2, name2, _ = _UNIT_CATEGORIES[u_to]
            if cat1 == cat2:
                val = float(m_unit.group("val"))
                converted = (val * factor1) / factor2
                if cat1 == "data" and converted >= 1:
                    return f"{sym} {val:g} {name1} = {int(converted):,d} {name2}"
                return f"{sym} {val:g} {name1} = {converted:,.2f} {name2}"

    return None


def _search_or_ask_route(norm: str) -> Optional[Route]:
    # 1. "open/launch <service> and (ask|prompt|search|tell it) <query>"
    m = re.match(
        r"^(?:open|launch|go to|pull up)\s+(?P<svc>chatgpt|claude|google|youtube|yt|reddit|github|duckduckgo|ddg|bing|wikipedia|wiki|spotify)\s+"
        r"(?:and\s+)?(?:ask|prompt|search(?:\s+for)?|tell(?:\s+it)?|query)\s+(?P<q>.+)$",
        norm, re.I
    )
    if m:
        svc = m.group("svc").lower()
        q = _clean_search_query(m.group("q"))
        if not q:
            return Route(CLARIFY, text=f"What would you like to ask or search on {svc.title()}?")
        if svc == "spotify":
            return Route(ACTION, "spotify_play", {"query": q})
        url_tmpl = _SEARCH_SERVICES.get(svc, ("Google", "google.com/search?q={q}"))[1]
        return Route(ACTION, "open_site", {"site": url_tmpl.format(q=urllib.parse.quote_plus(q))})

    # 2. "ask/prompt/send/tell <chatgpt|claude> [to|with|about|that|saying] <query>"
    m = re.match(
        r"^(?:ask|prompt|tell|send(?:\s+(?:a\s+)?(?:message|prompt))?(?:\s+to)?)\s+(?P<svc>chatgpt|claude)\s+(?:to\s+|with\s+|about\s+|that\s+|saying\s+|:\s*)?(?P<q>.+)$",
        norm, re.I
    )
    if m:
        svc = m.group("svc").lower()
        q = _clean_search_query(m.group("q"))
        if not q:
            return Route(CLARIFY, text=f"What would you like to ask {svc.title()}?")
        url_tmpl = _SEARCH_SERVICES[svc][1]
        return Route(ACTION, "open_site", {"site": url_tmpl.format(q=urllib.parse.quote_plus(q))})

    # 2b. "ask/send/prompt <query> (in|on|to) (chatgpt|claude)"
    m = re.match(
        r"^(?:ask|prompt|send(?:\s+(?:a\s+)?(?:message|prompt))?)\s+(?P<q>.+?)\s+(?:in|on|to)\s+(?P<svc>chatgpt|claude)$",
        norm, re.I
    )
    if m:
        svc = m.group("svc").lower()
        q = _clean_search_query(m.group("q"))
        if q:
            url_tmpl = _SEARCH_SERVICES[svc][1]
            return Route(ACTION, "open_site", {"site": url_tmpl.format(q=urllib.parse.quote_plus(q))})

    # 3. "search [on] <service> for <query>"
    m = re.match(
        r"^search\s+(?:on\s+)?(?P<svc>google|youtube|yt|reddit|github|duckduckgo|ddg|bing|wikipedia|wiki|spotify)\s+for\s+(?P<q>.+)$",
        norm, re.I
    )
    if m:
        svc = m.group("svc").lower()
        q = _clean_search_query(m.group("q"))
        if not q:
            return Route(CLARIFY, text=f"What should I search on {svc.title()}?")
        if svc == "spotify":
            return Route(ACTION, "spotify_play", {"query": q})
        url_tmpl = _SEARCH_SERVICES.get(svc, ("Google", "google.com/search?q={q}"))[1]
        return Route(ACTION, "open_site", {"site": url_tmpl.format(q=urllib.parse.quote_plus(q))})

    # 4. "search [for] <query> (on|in|using|via) <service|chrome|browser>"
    m = re.match(
        r"^search\s+(?:for\s+)?(?P<q>.+?)\s+(?:on|in|using|via)\s+(?P<svc>google|youtube|yt|reddit|github|duckduckgo|ddg|bing|wikipedia|wiki|spotify|chrome|the browser|browser)$",
        norm, re.I
    )
    if m:
        svc = m.group("svc").lower()
        q = _clean_search_query(m.group("q"))
        if not q:
            return Route(CLARIFY, text="What should I search for?")
        if svc == "spotify":
            return Route(ACTION, "spotify_play", {"query": q})
        if svc in ("chrome", "browser", "the browser"):
            svc = "google"
        url_tmpl = _SEARCH_SERVICES.get(svc, ("Google", "google.com/search?q={q}"))[1]
        return Route(ACTION, "open_site", {"site": url_tmpl.format(q=urllib.parse.quote_plus(q))})

    # 5. "google <query>" (e.g. "google cute cats", but not "google chrome" or "google drive" or "google maps")
    m = re.match(r"^google\s+(?P<q>.+)$", norm, re.I)
    if m:
        q = _clean_search_query(m.group("q"))
        if q.lower() not in ("chrome", "drive", "maps", "earth", "docs", "sheets", "slides", "calendar", "mail", "gmail"):
            return Route(ACTION, "open_site", {"site": f"google.com/search?q={urllib.parse.quote_plus(q)}"})

    # 6. Plain "search [for] <query>"
    m = re.match(r"^search\s+(?:for\s+)?(?P<q>.+)$", norm, re.I)
    if m:
        q = _clean_search_query(m.group("q"))
        if re.match(r"^(?:my\s+)?(?:files|folders|documents|emails|messages)\b", q, re.I):
            return None
        if q and q.lower() not in ("for", "it", "this", "something"):
            return Route(ACTION, "open_site", {"site": f"google.com/search?q={urllib.parse.quote_plus(q)}"})

    # 7. "open/check/find/show/look up <query> (in|on) <service> [extra/location]"
    # e.g. "open meesaya murukku 2 in book my show coimbatore"
    # e.g. "open mechanical keyboard on amazon"
    m = re.match(
        r"^(?:open|show|check|find|look\s+up|search(?:\s+for)?)\s+(?P<q>.+?)\s+(?:in|on)\s+"
        r"(?P<svc>book\s*my\s*show|bms|amazon|flipkart|google|youtube|yt|reddit|github|twitter|x|netflix|wikipedia|wiki|chatgpt|claude|spotify|maps|swiggy|zomato)"
        r"(?:\s+(?P<extra>.+))?$",
        norm, re.I
    )
    if m:
        q = _clean_search_query(m.group("q"))
        svc_raw = m.group("svc").lower()
        extra = (m.group("extra") or "").strip()
        svc = re.sub(r"\s+", "", svc_raw)
        if svc == "bms":
            svc = "bookmyshow"
        elif svc == "yt":
            svc = "youtube"
        elif svc == "wiki":
            svc = "wikipedia"
        full_q = f"{q} {extra}".strip()
        if not full_q:
            return Route(CLARIFY, text=f"What would you like to search on {svc.title()}?")
        if svc == "spotify":
            return Route(ACTION, "spotify_play", {"query": full_q})
        url_tmpl = _SEARCH_SERVICES.get(svc, ("Search", "google.com/search?q={q}"))[1]
        return Route(ACTION, "open_site", {"site": url_tmpl.format(q=urllib.parse.quote_plus(full_q))})

    return None


def _volume_route(norm: str, last: Optional[ActionRecord]) -> Optional[Route]:
    m = _VOL_ABS_RE.match(norm)
    if m:
        pct = next(g for g in m.groups() if g)
        if int(pct) > 100:
            return Route(CLARIFY, text="Volume goes from 0 to 100. What level do you want?")
        return Route(ACTION, "volume_set", {"percent": int(pct)})
    m = _MUTE_RE.match(norm)
    if m:
        return Route(ACTION, "volume_change", {"mode": m.group(1).lower()})
    m = _VOL_REL_RE.match(norm)
    if m:
        d = (m.group("dir1") or m.group("dir2") or m.group("dir3") or m.group("dir4")
             or m.group("dir5") or m.group("dir6") or "").lower()
        step = int(m.group("by") or m.group("n") or 10)
        # "make it up/down" is ambiguous between volume and brightness
        if d in ("up", "down", "higher", "lower") and not re.search(r"volume|sound|audio|music|song|louder|quieter", norm, re.I):
            fam = _family_of(last)
            if fam == "display":
                return Route(ACTION, "brightness_change", {"mode": "up" if d in ("up", "higher") else "down", "step": step})
            if fam != "audio":
                return Route(CLARIFY, text="Volume or brightness?")
        mode = "up" if d in ("louder", "up", "higher", "increase", "raise") else "down"
        return Route(ACTION, "volume_change", {"mode": mode, "step": step})
    return None


def _brightness_route(norm: str) -> Optional[Route]:
    m = _BRIGHT_ABS_RE.match(norm)
    if m:
        pct = int(next(g for g in m.groups() if g))
        if pct > 100:
            return Route(CLARIFY, text="Brightness goes from 5 to 100. What level do you want?")
        return Route(ACTION, "brightness_set", {"percent": max(5, pct)})
    m = _BRIGHT_REL_RE.match(norm)
    if m:
        d = (m.group("d1") or m.group("d2") or m.group("d3") or m.group("d4") or "dim").lower()
        mode = "up" if d in ("brighter", "up", "increase", "raise") else "down"
        return Route(ACTION, "brightness_change", {"mode": mode, "step": int(m.group("by") or 10)})
    return None


def _play_route(norm: str) -> Optional[Route]:
    m = _PLAY_RE.match(norm)
    if not m:
        return None
    raw_q = m.group("q")
    low = raw_q.lower()
    if re.fullmatch(r"(?:the )?next(?: song| track| one)?|(?:the )?previous(?: song| track| one)?|it|this|that|again|music|something|some music|a song|anything", low):
        return None                          # handled by media-control patterns
    wants_yt = bool(re.search(r"\b(?:youtube|yt)\b", raw_q, re.I))
    fullscreen = bool(re.search(r"\bfull ?screen\b", raw_q, re.I))
    q = re.sub(r"\bfull ?screen\b", "", raw_q, flags=re.I)
    q = re.sub(r"\bfull\s*video\b", "", q, flags=re.I)
    q = re.sub(r"\b(?:on|in|using|via)\s+(?:the\s+)?(?:youtube|yt)\b", "", q, flags=re.I)
    q = re.sub(r"\b(?:youtube|yt)\b", "", q, flags=re.I)
    q = _clean_query(q)
    if not q:
        return Route(CLARIFY, text="What should I play?")
    if len(q) > 120:
        return Route(CLARIFY, text="That's a long title. What exactly should I play?")
    if wants_yt:
        return Route(ACTION, "youtube_play", {"query": q, "fullscreen": "yes" if fullscreen else "no"})
    return Route(ACTION, "spotify_play", {"query": q})


def _open_route(norm: str) -> Optional[Route]:
    # Check if user requested opening N apps/things (e.g. "open 5 things", "open 5 app in my lap")
    m_count = _OPEN_N_APPS_RE.match(norm)
    if m_count:
        raw_n = m_count.group("n").lower()
        n = _COUNT_WORDS.get(raw_n, int(raw_n) if raw_n.isdigit() else 5)
        count = max(1, min(len(_TOP_DESKTOP_APPS), n))
        selected = _TOP_DESKTOP_APPS[:count]
        steps = [Route(ACTION, "open_app", {"name": app}) for app in selected]
        return Route(SEQUENCE, steps=steps)

    m = _OPEN_RE.match(norm)
    if not m:
        return None
    target = m.group("t").strip().strip("\"'")
    target = re.sub(r"^up\s+", "", target, flags=re.I).strip()
    target_low = re.sub(r"^(?:the |my )", "", target.lower()).strip()
    target_low = re.sub(r"\s+(?:in|on)\s+(?:chrome|the browser|browser)$", "", target_low).strip()
    target_low = re.sub(r"\s+(?:app|application|website|site|page|tab|window)$", "", target_low).strip()
    if not target_low:
        return Route(CLARIFY, text="What should I open?")
    if target_low in ("clipboard", "clipboard history", "clipboard vault"):
        return Route(ACTION, "clipboard_history", {"action": "list"})
    if target_low in ("whatsapp", "whatsapp web", "wa"):
        return Route(ACTION, "open_app", {"name": "whatsapp"})
    if target_low in _KNOWN_DIRECT_LAUNCH:
        act_name, args = _KNOWN_DIRECT_LAUNCH[target_low]
        return Route(ACTION, act_name, args)
    if target_low in _KNOWN_SITES:
        return Route(ACTION, "open_site", {"site": _KNOWN_SITES[target_low]})
    site = _SITE_LIKE_RE.match(target)
    if site:
        return Route(ACTION, "open_site", {"site": site.group(1) + (site.group(2) or "")})

    # Do not treat phrases containing conjunctions or action verbs as single app names
    if re.search(r"\b(?:and|then|ask|search|tell|prompt|play|find|look|write)\b", target_low, re.I):
        return None

    # Do not treat vague or conversational clauses as single app names; let LLM handle them
    if re.search(r"\b(?:things?|stuff|apps?|applications?|something|anything|whatever|lap|laptop|computer)\b", target_low, re.I):
        return None

    # Check known desktop apps
    app = resolve_app(target_low)
    if app is not None:
        return Route(ACTION, "open_app", {"name": app.key})

    # If it has prepositions or multiple words, fallback to web search instead of failing as app
    if re.search(r"\b(?:in|on|at|for|from|about)\b", target_low, re.I) or len(target_low.split()) > 2:
        return Route(ACTION, "open_site", {"site": f"google.com/search?q={urllib.parse.quote_plus(target)}"})

    if re.fullmatch(r"[\w .'\-]{1,60}", target_low):
        return Route(ACTION, "open_app", {"name": target_low})
    return Route(CLARIFY, text="I can open apps and websites by name. Which one do you mean?")


def _close_route(norm: str, last: Optional[ActionRecord] = None) -> Optional[Route]:
    """Route supported close commands without guessing arbitrary desktop windows."""
    # Port killer
    m_pkill = _PORT_KILL_RE.match(norm)
    if m_pkill:
        return Route(ACTION, "port_control", {"action": "kill", "port": int(m_pkill.group("port"))})

    # Process killer
    m_kproc = _KILL_PROC_RE.match(norm)
    if m_kproc:
        return Route(ACTION, "top_processes", {"action": "kill", "target": m_kproc.group("target")})

    m = _CLOSE_RE.match(norm)
    if not m:
        return None
    t = m.group("t").strip().strip("\"'")
    low_t = t.lower()

    # Pronoun resolution: "close it", "quit it", "close that", "kill it"
    if low_t in ("it", "that", "this", "the app", "this app"):
        if last is not None and last.name in ("open_app", "open_site"):
            if last.name == "open_app":
                app_target = last.args.get("name", "")
                if _CHROME_NAME_RE.fullmatch(app_target):
                    return Route(ACTION, "close_chrome", {})
                resolved = resolve_app(app_target)
                if resolved is not None and resolved.key != "chrome":
                    return Route(ACTION, "close_app", {"name": resolved.key})
                return Route(ACTION, "close_app", {"name": app_target})
            elif last.name == "open_site":
                return Route(ACTION, "close_chrome", {})
        return Route(CLARIFY, text="Close what? Tell me which app or window you want to close.")

    # Clipboard close
    if low_t in ("clipboard", "clipboard history", "clipboard vault", "my clipboard"):
        return Route(REPLY, text="Clipboard history is just a list in our conversation — nothing to close.")

    if _CHROME_NAME_RE.fullmatch(t):
        return Route(ACTION, "close_chrome", {})
    app = resolve_app(t)
    if app is not None and app.key != "chrome":
        return Route(ACTION, "close_app", {"name": app.key})
    wa = bool(_WA_NAME_RE.search(t))
    ig = bool(_IG_NAME_RE.search(t))
    if (wa and ig) or _CLOSE_TABS_ALL_RE.fullmatch(t) or low_t in ("both", "both tabs", "both of them"):
        return Route(ACTION, "close_tabs_all", {})
    if wa:
        return Route(ACTION, "close_tabs", {"target": "whatsapp"})
    if ig:
        return Route(ACTION, "close_tabs", {"target": "instagram"})
    if _CLOSE_ALL_RE.fullmatch(t):
        return Route(ACTION, "close_all_apps", {})
    if _CLOSE_VAGUE_RE.fullmatch(t):
        return Route(CLARIFY, text="Which one should I close: WhatsApp or Instagram?")
    return Route(REPLY, text=CLOSE_UNSUPPORTED_TEXT)


def _whatsapp_route(norm: str) -> Optional[Route]:
    if _NOT_WHATSAPP_RE.search(norm) and not re.search(r"whatsapp", norm, re.I):
        return None
    for rx in _WA_PATTERNS:
        m = rx.match(norm)
        if not m:
            continue
        contact = re.sub(r"\s+", " ", m.group("c")).strip(" \"'")
        message = m.group("m").strip().strip("\"'")
        contact = re.sub(r"^(?:my |to )", "", contact, flags=re.I).strip()
        if contact.lower() in ("chatgpt", "claude", "gemini", "openai", "copilot", "google", "youtube", "spotify", "browser", "chrome"):
            return None
        if not contact or not message:
            return Route(CLARIFY, text="Who should I message, and what should it say?")
        if len(contact) > 60 or len(message) > 1000:
            return Route(CLARIFY, text="Who should I message, and what should it say?")
        return Route(ACTION, "whatsapp_draft", {"contact": contact, "message": message})
    return None


# -- the router --------------------------------------------------------------

def route(text: str, last_action: Optional[ActionRecord] = None,
          pending: Optional[PendingConfirmation] = None) -> Route:
    raw = (text or "").strip()
    if not raw or len(raw) > MAX_ROUTED_LEN:
        return Route(LLM)
    norm = normalise(raw)
    low = norm.lower()
    if not norm:
        return Route(LLM)

    # 0. Intercept bare close commands & pronouns: never guess or kill apps from previous memory
    if low in ("close", "quit", "kill", "exit", "close it", "close that", "close app", "close the app", "close window", "close the window"):
        if low in ("close it", "close that", "close the app") and last_action is not None and last_action.name in ("open_app", "open_site"):
            r_close = _close_route("close it", last_action)
            if r_close:
                return r_close
        return Route(CLARIFY, text="Close what? Tell me which app or window you want to close.")

    # Close clipboard command
    if _CLIPBOARD_CLOSE_RE.match(low):
        return Route(REPLY, text="Clipboard history is just a list in our conversation — nothing to close.")

    # Close commands take priority before general sequence splitting
    if re.match(r"^(?:close|quit|kill)\s+", low):
        r_close = _close_route(low, last_action)
        if r_close:
            return r_close

    # 0a. Search & Ask routes (e.g. "open chatgpt and ask who are u", "search cute cats on google")
    r_search = _search_or_ask_route(norm)
    if r_search:
        return r_search

    # 0a-i. Conversational etiquette: thanks, creator, identity, help
    if _THANKS_RE.match(low):
        return Route(REPLY, text="You're welcome!")
    if _CREATOR_RE.match(low):
        return Route(REPLY, text="You are Shakeel, my creator, sole architect, and master. You built me from the ground up to be your autonomous AI companion.")
    if _IDENTITY_RE.match(low):
        return Route(REPLY, text="I'm Lexi, your personal desktop AI companion, created and architected by Shakeel.")
    if _HELP_RE.match(low):
        return Route(REPLY, text="I can control volume and brightness, play music on Spotify and YouTube, search the web and ChatGPT, check your battery and media status, take screenshots, open desktop apps, draft WhatsApp messages, lock your screen, and run terminal tasks.")

    # 0a-ii. Date & Time
    if _TIME_DATE_RE.match(low):
        now_str = datetime.datetime.now().strftime("It's %I:%M %p on %A, %B %d, %Y.").replace(" 0", " ")
        return Route(REPLY, text=now_str)

    # 0a-iii. Safe Local Math, Conversions & World Time
    math_res = _eval_math(norm)
    if math_res:
        return Route(REPLY, text=math_res)
    conv_res = _eval_conversions(norm)
    if conv_res:
        return Route(REPLY, text=conv_res)

    # 0a-iv. Phone & Battery queries
    if _PHONE_BATTERY_RE.match(low):
        return Route(ACTION, "phone_battery", {})
    if _PHONE_RING_RE.match(low):
        return Route(ACTION, "phone_ring", {})
    if _PHONE_SNAP_RE.match(low):
        return Route(ACTION, "phone_camera_snap", {})
    if _PHONE_CLIPBOARD_RE.match(low):
        return Route(ACTION, "phone_clipboard_sync", {})

    if _BATTERY_RE.match(low):
        return Route(ACTION, "system_battery", {})

    # Focus & Pomodoro Timer
    if _FOCUS_STOP_RE.match(low):
        return Route(ACTION, "focus_timer", {"action": "stop"})
    if _FOCUS_STATUS_RE.match(low):
        return Route(ACTION, "focus_timer", {"action": "status"})
    m_focus = _FOCUS_RE.match(low)
    if m_focus:
        mins = int(m_focus.group("m1") or m_focus.group("m2") or 25)
        return Route(ACTION, "focus_timer", {"action": "start", "minutes": mins})

    # Clipboard Reading, History & Download Sentinel
    if _CLIPBOARD_READ_RE.match(low):
        return Route(ACTION, "clipboard_read", {})
    if _CLIPBOARD_HIST_RE.match(low):
        return Route(ACTION, "clipboard_history", {"action": "list"})
    m_clip_res = _CLIPBOARD_RESTORE_RE.match(low)
    if m_clip_res:
        return Route(ACTION, "clipboard_history", {"action": "copy", "index": int(m_clip_res.group("idx"))})

    if _DOWNLOADS_STATUS_RE.match(low):
        return Route(ACTION, "download_status", {})

    # Quick Notes & Scratchpad
    m_note_add = _NOTES_ADD_RE.match(norm)
    if m_note_add:
        txt = (m_note_add.group("txt") or "").strip()
        if txt:
            return Route(ACTION, "quick_notes", {"action": "add", "text": txt})
    if _NOTES_LIST_RE.match(low):
        return Route(ACTION, "quick_notes", {"action": "list"})
    if _NOTES_CLEAR_RE.match(low):
        return Route(ACTION, "quick_notes", {"action": "clear"})

    # Terminal Error Diagnoser
    if _TERMINAL_DIAG_RE.match(low):
        return Route(ACTION, "terminal_diagnose", {})

    # Port Inspector & Killer
    m_pkill = _PORT_KILL_RE.match(low)
    if m_pkill:
        return Route(ACTION, "port_control", {"action": "kill", "port": int(m_pkill.group("port"))})
    m_pinsp = _PORT_INSPECT_RE.match(low)
    if m_pinsp:
        port_num = int(m_pinsp.group("port") or m_pinsp.group("port2"))
        return Route(ACTION, "port_control", {"action": "inspect", "port": port_num})

    # Top Resource Consumers & Process Killer
    m_kproc = _KILL_PROC_RE.match(low)
    if m_kproc:
        return Route(ACTION, "top_processes", {"action": "kill", "target": m_kproc.group("target")})
    m_tprocs = _TOP_PROCS_RE.match(low)
    if m_tprocs:
        sort_kind = (m_tprocs.group("sort") or m_tprocs.group("sort2") or m_tprocs.group("sort3") or "").lower()
        sort_by = "mem" if sort_kind in ("ram", "memory") else "cpu"
        return Route(ACTION, "top_processes", {"action": "list", "sort_by": sort_by})

    # Do Not Disturb (DND) Control
    if _DND_ON_RE.match(low):
        return Route(ACTION, "dnd_control", {"action": "on"})
    if _DND_OFF_RE.match(low):
        return Route(ACTION, "dnd_control", {"action": "off"})
    if _DND_STATUS_RE.match(low):
        return Route(ACTION, "dnd_control", {"action": "status"})

    # Extended System & Network queries
    if _SYS_RESOURCES_RE.match(low):
        return Route(ACTION, "system_resources", {})
    if _NETWORK_STATUS_RE.match(low):
        return Route(ACTION, "network_status", {})
    if _WIFI_LIST_RE.match(low):
        return Route(ACTION, "wifi_list", {})
    m_bt = _BLUETOOTH_RE.match(low)
    if m_bt:
        act = m_bt.group("act1") or m_bt.group("act2") or "status"
        return Route(ACTION, "bluetooth_control", {"action": act})

    # 0a-v. Media now playing query
    if _NOW_PLAYING_RE.match(low):
        return Route(ACTION, "media_now_playing", {})

    # 0a-vi. Screenshot capture
    if _SCREENSHOT_RE.match(low):
        return Route(ACTION, "take_screenshot", {})

    # 0a-vii. System power controls (confirmation required)
    if _SLEEP_RE.match(low):
        return Route(ACTION, "system_sleep", {})
    if _RESTART_RE.match(low):
        return Route(ACTION, "system_restart", {})
    if _POWEROFF_RE.match(low):
        return Route(ACTION, "system_poweroff", {})

    # 0a-viii. Productivity & Web quick-actions
    if _MEET_RE.match(low):
        return Route(ACTION, "open_site", {"site": "meet.google.com/new"})
    if _DOCS_RE.match(low):
        return Route(ACTION, "open_site", {"site": "docs.new"})
    if _SHEETS_RE.match(low):
        return Route(ACTION, "open_site", {"site": "sheets.new"})
    if _SLIDES_RE.match(low):
        return Route(ACTION, "open_site", {"site": "slides.new"})

    m_map = _MAPS_RE.match(norm)
    if m_map:
        dest = (m_map.group("dest") or "").strip()
        if dest:
            return Route(ACTION, "open_site", {"site": f"google.com/maps/search/{urllib.parse.quote_plus(dest)}"})

    m_weather = _WEATHER_RE.match(norm)
    if m_weather:
        loc = (m_weather.group("loc") or "").strip()
        q = f"weather in {loc}" if loc else "weather today"
        return Route(ACTION, "open_site", {"site": f"google.com/search?q={urllib.parse.quote_plus(q)}"})

    m_def = _DEFINE_RE.match(norm)
    if m_def:
        word = m_def.group("word").strip()
        if word:
            return Route(ACTION, "open_site", {"site": f"google.com/search?q={urllib.parse.quote_plus('define ' + word)}"})

    # 0b. Chained / sequence commands ("open spotify and play believer", "mute and lock screen")
    if (" and " in low or " then " in low) and not re.search(r"\b(?:saying|that says|tell)\b", low):
        split_m = re.split(r"\s+(?:and then|then|and)\s+", raw, maxsplit=1, flags=re.I)
        if len(split_m) == 2:
            p1, p2 = split_m[0].strip(), split_m[1].strip()
            # Special case: "open spotify and play <query>"
            if re.match(r"^(?:open|launch)\s+spotify$", p1, re.I) and re.match(r"^(?:play|listen to)\s+", p2, re.I):
                r2 = _play_route(normalise(p2))
                if r2 and r2.kind == ACTION:
                    return r2
            # Special case: "open youtube and play/search <query>"
            if re.match(r"^(?:open|launch)\s+(?:youtube|yt)$", p1, re.I) and re.match(r"^(?:play|search|listen to)\s+", p2, re.I):
                q = re.sub(r"^(?:play|search|listen to)\s+", "", p2, flags=re.I)
                return Route(ACTION, "youtube_play", {"query": q})
            # General 2-step sequence
            r1 = route(p1, last_action, pending)
            r1_rec = ActionRecord(name=r1.name, args=r1.args, started=time.time()) if r1.kind == ACTION else last_action
            r2 = route(p2, r1_rec, pending)

            # Contradiction check: opening and immediately closing the same target in one command
            is_r1_open = r1.kind == ACTION and r1.name in ("open_app", "open_site")
            is_r2_close = r2.kind == ACTION and r2.name in ("close_app", "close_chrome", "close_tabs", "close_all_apps")
            if is_r1_open and is_r2_close:
                target1 = r1.args.get("name") or ("chrome" if "chrome" in str(r1.args.get("site", "")) else r1.args.get("site"))
                target2 = "chrome" if r2.name == "close_chrome" else r2.args.get("name")
                t1_s = str(target1 or "").lower()
                t2_s = str(target2 or "").lower()
                if t1_s and t2_s and (t1_s == t2_s or t1_s in t2_s or t2_s in t1_s or t2_s in ("it", "that", "the app")):
                    return Route(REPLY, text=f"I won't open and immediately close {t1_s.title()} in the same command.")

            if r1.kind == ACTION and r2.kind == ACTION:
                return Route(SEQUENCE, steps=[r1, r2])

    # 0c. Conversational modifier follow-up (e.g. "in chrome", "open in chrome")
    if re.fullmatch(r"(?:open\s+)?(?:in|on|with|using)\s+(?:chrome|the browser|browser)", low):
        if last_action and last_action.name == "open_site":
            return Route(REPLY, text="I already opened that in your default browser.")
        return Route(CLARIFY, text="What would you like to search or open in Chrome?")

    # 1. A pending confirmation owns the next turn.
    if pending is not None and not pending.expired():
        closing = pending.name in ("close_tabs_all", "close_all_apps")
        is_power = pending.name.startswith("system_")
        if (_CONFIRM_CLOSE_RE if (closing or is_power) else _CONFIRM_RE).fullmatch(low):
            return Route(CONFIRM, pending.name, dict(pending.args))
        if _CANCEL_RE.fullmatch(low):
            return Route(CANCEL)
        if low in ("yes", "yeah", "yep", "ok", "okay", "sure", "do it", "go ahead", "y"):
            # vague "yes" must never authorise a send or a mass-close
            if closing:
                return Route(CLARIFY, text="To close them, say “confirm”. To leave them open, say “cancel”.")
            if is_power:
                return Route(CLARIFY, text="To proceed, say “confirm”. To cancel, say “cancel”.")
            return Route(CLARIFY, text="To send it, say “send it”. To drop it, say “cancel”.")
        # anything else: treat as a new request; the pending draft stays until it expires

    # 2. Follow-ups that depend on last_action.
    if _RETRY_RE.fullmatch(low):
        if last_action is None:
            return Route(CLARIFY, text="Do what? I don't have anything recent to repeat.")
        ok, why = can_retry(last_action)
        if ok:
            return Route(ACTION, last_action.name, dict(last_action.args), retry_of=last_action)
        if last_action.state == FAILED or why:
            return Route(REPLY, text=why or "I can't safely repeat that.")
        # last action succeeded: "again" repeats only harmless, repeatable controls
        cap = last_action.capability
        if low in ("again", "do it again", "once more", "one more time", "run it again") and cap and cap.retry_safe and not cap.sensitive:
            return Route(ACTION, last_action.name, dict(last_action.args), retry_of=last_action)
        return Route(CLARIFY, text=f"Do what? Last I did: {last_action.name.replace('_', ' ')}.")

    # 3. Volume / brightness / lock
    r = _volume_route(low, last_action)
    if r:
        return r
    r = _brightness_route(low)
    if r:
        return r
    if _LOCK_RE.match(low):
        return Route(ACTION, "lock_screen", {})

    # 3b-i. "What's running?" — list known apps that are currently open
    if _LIST_RUNNING_RE.match(low):
        return Route(ACTION, "list_running_apps", {})

    # 3b-ii. "Close all running process/apps" — close every known running app
    if _CLOSE_ALL_APPS_RE.match(low):
        return Route(ACTION, "close_all_apps", {})

    # 3b. Closing WhatsApp / Instagram browser tabs (nothing else can be closed)
    r = _close_route(low)
    if r:
        return r

    # 4. Media controls and "play X"
    for cmd, rx in _MEDIA_RE.items():
        if rx.match(low):
            return Route(ACTION, "media_control", {"command": cmd})
    r = _play_route(norm)
    if r:
        return r

    # 5. WhatsApp (draft only) and opening apps/sites
    r = _whatsapp_route(norm)
    if r:
        return r
    r = _open_route(norm)
    if r:
        return r

    # 5b. Direct launch of known apps and sites (Spotlight / Siri style)
    if low in _KNOWN_DIRECT_LAUNCH:
        act_name, args = _KNOWN_DIRECT_LAUNCH[low]
        return Route(ACTION, act_name, args)
    if low in _KNOWN_SITES:
        return Route(ACTION, "open_site", {"site": _KNOWN_SITES[low]})

    # 6. A bare verb with no object is uncertain: ask once, don't guess.
    if re.fullmatch(r"(?:send|message|text|volume|brightness)", low):
        return Route(CLARIFY, text={
            "send": "Send what, and to whom?", "message": "Who should I message, and what should it say?",
            "text": "Who should I message, and what should it say?",
            "volume": "What volume level do you want?", "brightness": "What brightness level do you want?",
        }[low])
    return Route(LLM)
