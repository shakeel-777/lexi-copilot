"""App-control layer for Lexi: discover real processes, quit gracefully, verify.

No GTK here. Process discovery reads /proc (injectable for tests); signalling,
sleeping and the clock are injectable too, so behaviour is testable without
touching a real desktop or killing a real browser.

Honesty rules enforced here:
  * "closed" is only returned after the target's main process has been observed gone.
  * A target that was never running is reported as "not open", never as "closed".
  * A target that ignores the quit request is reported as still open.
  * Quitting is process-level: it closes EVERY window of that app. Replies say so.
"""

from __future__ import annotations

import os
import re
import signal
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence, Tuple


@dataclass(frozen=True)
class AppSpec:
    key: str                          # canonical name, e.g. "chrome"
    label: str                        # user-facing name
    comms: Tuple[str, ...] = ()       # exact /proc/<pid>/comm values (<=15 chars)
    exe_re: str = ""                  # regex fullmatched against the resolved exe path / argv0
    aliases: Tuple[str, ...] = ()     # names the user may say


APPS: Dict[str, AppSpec] = {}


def _reg(spec: AppSpec) -> None:
    APPS[spec.key] = spec


_reg(AppSpec(
    key="chrome", label="Chrome",
    # Arch's google-chrome package runs /opt/google/chrome/chrome with comm "chrome".
    # Other distros/channels use google-chrome / google-chrome-stable launchers.
    comms=("chrome", "google-chrome", "google-chrome-stable"),
    exe_re=r"(?:/opt/google/chrome(?:-[a-z]+)?/chrome|/usr/bin/google-chrome(?:-stable)?|chrome|google-chrome(?:-stable)?)",
    aliases=("chrome", "google chrome", "google-chrome", "chrome browser", "the browser", "browser"),
))
_reg(AppSpec(
    key="spotify", label="Spotify",
    comms=("spotify",),
    exe_re=r"(?:/opt/spotify/spotify|/usr/bin/spotify|spotify)",
    aliases=("spotify",),
))
_reg(AppSpec(
    key="files", label="Files",
    comms=("nautilus",),
    exe_re=r"(?:/usr/bin/nautilus|nautilus)",
    aliases=("files", "file manager", "nautilus"),
))
_reg(AppSpec(
    key="terminal", label="Terminal",
    comms=("gnome-terminal-", "gnome-terminal", "kgx", "ptyxis"),
    exe_re=r"(?:/usr/(?:lib|libexec)/gnome-terminal-server|gnome-terminal-server|/usr/bin/kgx|/usr/bin/ptyxis|kgx|ptyxis)",
    aliases=("terminal", "gnome terminal", "console"),
))

# Quitting the terminal the user is typing this from would be self-defeating
# (and Lexi itself may be a child of it); it needs an explicit confirmation.
CONFIRM_ON_QUIT = frozenset({"terminal"})


def resolve_app(name: str) -> Optional[AppSpec]:
    n = re.sub(r"^(?:the|my)\s+", "", (name or "").strip().lower())
    n = re.sub(r"\s+(?:app|application|browser window|window|windows)$", "", n).strip()
    for spec in APPS.values():
        if n == spec.key or n in spec.aliases:
            return spec
    return None


# -- process discovery -------------------------------------------------------

@dataclass
class Proc:
    pid: int
    ppid: int
    comm: str
    exe: str
    argv: List[str] = field(default_factory=list)


def list_procs(proc_root: str = "/proc") -> List[Proc]:
    out: List[Proc] = []
    try:
        names = os.listdir(proc_root)
    except OSError:
        return out
    for name in names:
        if not name.isdigit():
            continue
        base = os.path.join(proc_root, name)
        try:
            with open(os.path.join(base, "comm"), "r", errors="replace") as f:
                comm = f.read().strip()
            with open(os.path.join(base, "cmdline"), "rb") as f:
                argv = [a.decode("utf-8", "replace") for a in f.read().split(b"\0") if a]
            ppid = 0
            with open(os.path.join(base, "stat"), "r", errors="replace") as f:
                stat = f.read()
            # comm may contain spaces/parens; ppid is the 2nd field after the last ')'
            rest = stat[stat.rfind(")") + 2:].split()
            if len(rest) >= 2:
                ppid = int(rest[1])
            try:
                exe = os.readlink(os.path.join(base, "exe"))
            except OSError:
                exe = ""
        except (OSError, ValueError):
            continue            # raced with exit, or not readable
        out.append(Proc(int(name), ppid, comm, exe, argv))
    return out


def _is_child_type(argv: Sequence[str]) -> bool:
    """Chromium-family helpers (renderer, gpu, zygote, utility, crashpad...) carry --type=."""
    return any(a.startswith("--type=") for a in argv)


def _matches(spec: AppSpec, p: Proc) -> bool:
    if p.comm in spec.comms:
        return True
    cands = [p.exe] + ([p.argv[0]] if p.argv else [])
    rx = re.compile(spec.exe_re) if spec.exe_re else None
    return bool(rx and any(c and rx.fullmatch(c) for c in cands))


def find_main_procs(spec: AppSpec, procs: Optional[List[Proc]] = None,
                    self_pid: Optional[int] = None) -> List[Proc]:
    """Main (top-level) processes of an app, excluding helper children and Lexi itself.

    For each matching process we drop helpers (--type=...) and any match whose
    parent is also a match, leaving the root of each app instance.
    """
    procs = list_procs() if procs is None else procs
    me = os.getpid() if self_pid is None else self_pid
    matched = [p for p in procs if _matches(spec, p) and p.pid != me
               and not _is_child_type(p.argv)]
    pids = {p.pid for p in matched}
    return [p for p in matched if p.ppid not in pids]


def any_app_procs(spec: AppSpec, procs: Optional[List[Proc]] = None) -> List[Proc]:
    """Every process that belongs to the app, helpers included (used for the final check)."""
    procs = list_procs() if procs is None else procs
    me = os.getpid()
    return [p for p in procs if _matches(spec, p) and p.pid != me]


# -- quitting -----------------------------------------------------------------

@dataclass
class AppController:
    list_fn: Callable[[], List[Proc]] = list_procs
    kill_fn: Callable[[int, int], None] = os.kill
    sleep_fn: Callable[[float], None] = time.sleep
    clock_fn: Callable[[], float] = time.monotonic
    timeout: float = 6.0
    poll: float = 0.25

    def is_running(self, spec: AppSpec) -> bool:
        return bool(find_main_procs(spec, self.list_fn()))

    def quit(self, spec: AppSpec) -> Dict[str, object]:
        """Ask every main process of the app to quit (SIGTERM) and verify it is gone."""
        mains = find_main_procs(spec, self.list_fn())
        if not mains:
            return {"success": True, "verified": True, "closed": False, "was_running": False,
                    "message": f"{spec.label} isn't open."}
        for p in mains:
            try:
                self.kill_fn(p.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass                                  # already gone; the check below decides
            except PermissionError:
                return {"success": False, "verified": False, "closed": False, "was_running": True,
                        "message": f"I'm not allowed to close {spec.label} (permission denied)."}
            except OSError as exc:
                return {"success": False, "verified": False, "closed": False, "was_running": True,
                        "message": f"Couldn't ask {spec.label} to quit: {exc}."}
        deadline = self.clock_fn() + self.timeout
        while True:
            if not find_main_procs(spec, self.list_fn()):
                return {"success": True, "verified": True, "closed": True, "was_running": True,
                        "message": f"Closed {spec.label} (all its windows)."}
            if self.clock_fn() >= deadline:
                break
            self.sleep_fn(self.poll)
        return {"success": False, "verified": False, "closed": False, "was_running": True,
                "message": f"{spec.label} is still open (I asked it to quit, but it didn't; I didn't force it)."}


def quit_app(name: str, controller=None):
    spec = resolve_app(name)
    if spec is None:
        return {"success": False, "verified": False,
                "message": f"I don't know how to close \u201c{name}\u201d yet. I can close: "
                           + ", ".join(s.label for s in APPS.values()) + "."}
    return (controller or AppController()).quit(spec)


def list_running(controller=None):
    """Return the subset of known APPS that are currently running."""
    ctrl = controller or AppController()
    return [spec for spec in APPS.values() if ctrl.is_running(spec)]

