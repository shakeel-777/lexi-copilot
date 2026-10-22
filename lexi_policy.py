"""Single execution policy for terminal commands: blocked / confirm / allow.

Pure Python (no GTK) so both the legacy action path and the capability registry
share it and tests can exercise it directly.

  BLOCKED  - never runs, never asks: erasing the filesystem root, GUI input injection.
  CONFIRM  - high-impact (rm, sudo, dd, overwrite, curl|sh ...): needs an explicit yes.
  ALLOW    - ordinary work (mkdir, touch, cat, ls, cp, git, writing new files ...).
"""

import os
import re
import shlex
from typing import Tuple

ALLOW = "allow"
CONFIRM = "confirm"
BLOCKED = "blocked"

_RISKY_WORD_RE = re.compile(r'(?:^|[\s;&|(`])(?:rm|sudo|pkexec|doas|dd|mkfs(?:\.\w+)?|shred|truncate)(?=$|[\s;&|)`])')
_RECURSIVE_PERM_RE = re.compile(r'\b(?:chmod|chown)\b[^;&|]*\s-\w*R')
_FIND_DELETE_RE = re.compile(r'\bfind\b[^;&|]*\s-delete\b')
_REDIRECT_OVERWRITE_RE = re.compile(r'(?<!>)>(?![>&])')
_PIPE_TO_SHELL_RE = re.compile(r'\b(?:curl|wget)\b[^|;]*\|\s*(?:sudo\s+)?(?:ba|z|da)?sh\b')
_GUI_INPUT_SHELL_RE = re.compile(
    r'(?:^|[\s;&|(`\x27\x22/])(?:xdotool|ydotool|wtype|dotool|xte|evemu-event|evemu-play)\b',
    re.I
)

def is_gui_input_shell_command(cmd):
    """True when a shell command attempts to invoke a GUI input/keystroke injection tool
    (e.g. xdotool, ydotool, wtype, dotool, or `sleep 9 && xdotool key f`).
    Keyboard and typing input must use structured <action type="key"> or <action type="type"> tags."""
    cmd = (cmd or "").strip()
    if not cmd:
        return False
    return bool(_GUI_INPUT_SHELL_RE.search(cmd))

_ROOT_TARGET_RE = re.compile(r'^/+(?:\.{1,2}|\*)?/*$')
_SHELL_WRAPPERS = ('bash', 'sh', 'zsh', 'dash', 'ksh')
_MAX_WRAP_DEPTH = 4


def _is_root_target(token):
    """True for '/', '//', '/.', '/..', '/*' and similar spellings of the filesystem root."""
    return bool(_ROOT_TARGET_RE.match(token))


def is_hard_blocked_command(cmd):
    """True for destructive attempts to recursively erase the filesystem root.

    This is intentionally a refusal, not a confirmation prompt.  It normalises
    root spellings ('/', '//', '/.', '/..', '/*'), looks inside shell wrappers
    (bash -c, sh -c, eval) and privilege or env wrappers, and fails closed when
    the nesting is too deep to analyse.
    """
    return _hard_blocked(cmd or "", 0)


def _hard_blocked(cmd, depth):
    if depth > _MAX_WRAP_DEPTH:
        return True
    for segment in re.split(r'&&|\|\||;|\||\n', cmd):
        try:
            parts = shlex.split(segment)
        except ValueError:
            parts = segment.split()
        for i, tok in enumerate(parts):
            prog = os.path.basename(tok)
            if prog == "rm":
                args = parts[i + 1:]
                recursive = any(
                    arg == "--recursive" or (arg.startswith("-") and "r" in arg[1:])
                    for arg in args
                )
                targets = [arg for arg in args if not arg.startswith("-")]
                if recursive and any(_is_root_target(t) for t in targets):
                    return True
            elif prog in _SHELL_WRAPPERS:
                rest = parts[i + 1:]
                for j, opt in enumerate(rest):
                    if opt.startswith("-") and not opt.startswith("--") and "c" in opt[1:] and j + 1 < len(rest):
                        if _hard_blocked(rest[j + 1], depth + 1):
                            return True
                        break
            elif prog == "eval":
                if _hard_blocked(" ".join(parts[i + 1:]), depth + 1):
                    return True
    return False

_REDIRECT_TARGET_RE = re.compile(r"""(?<![>&])\d*>\|?(?![>&])\s*("[^"]*"|'[^']*'|[^\s;&|<>()]+)""")


def _redirect_overwrites_existing(cmd):
    """True if an output redirect (>) could truncate a file that already exists."""
    scrubbed = re.sub(r'\d*>>?\s*/dev/null', '', cmd)
    if not _REDIRECT_OVERWRITE_RE.search(scrubbed):
        return False
    targets = _REDIRECT_TARGET_RE.findall(scrubbed)
    if not targets:
        return True                      # saw a '>' but could not read its target
    has_cd = bool(re.search(r'\bcd\b', cmd))
    for raw in targets:
        t = raw.strip("\"'")
        if t.startswith("/dev/"):
            continue
        if re.search(r'[$`*?\[\]{}~]', t) and not t.startswith("~/"):
            return True                  # expansion we cannot resolve safely
        if has_cd and not t.startswith(("/", "~")):
            return True                  # cwd changes inside the command
        if os.path.lexists(os.path.expanduser(t)):
            return True
    return False



def is_risky_command(cmd):
    """True for commands that need a spoken/clicked confirmation first:
    rm, sudo, dd, mkfs, shred, curl|sh, and mv that would overwrite a file."""
    cmd = (cmd or "").strip()
    if not cmd:
        return False
    if (_PIPE_TO_SHELL_RE.search(cmd) or _RISKY_WORD_RE.search(cmd)
            or _RECURSIVE_PERM_RE.search(cmd) or _FIND_DELETE_RE.search(cmd)):
        return True
    # '>' truncates an EXISTING file (risky); creating a new file is ordinary work.
    # >> appends, >&/2>&1 and /dev/null are harmless. Unresolvable targets stay risky.
    if _redirect_overwrites_existing(cmd):
        return True
    for seg in re.split(r'&&|\|\||;|\|', cmd):
        try:
            parts = shlex.split(seg)
        except ValueError:
            return True  # unparseable -> be careful
        if not parts or parts[0] != "mv":
            continue
        if re.search(r'\bcd\b', cmd):
            return True  # cwd changes inside the command; can't resolve targets safely
        if "-t" in parts or "--target-directory" in parts or any(a.startswith("--target-directory=") for a in parts):
            # GNU form: mv -t DIR SRC...  (destination comes first)
            if "-t" in parts:
                i = parts.index("-t")
                dest_raw = parts[i + 1] if i + 1 < len(parts) else ""
                srcs = [a for j, a in enumerate(parts[1:], 1) if not a.startswith("-") and j != i + 1]
            else:
                dest_raw = next((a.split("=", 1)[1] for a in parts if a.startswith("--target-directory=")), "")
                if not dest_raw and "--target-directory" in parts:
                    i = parts.index("--target-directory")
                    dest_raw = parts[i + 1] if i + 1 < len(parts) else ""
                srcs = [a for a in parts[1:] if not a.startswith("-") and a != dest_raw]
            args = srcs + [dest_raw]
        else:
            args = [a for a in parts[1:] if not a.startswith("-")]
        if len(args) < 2:
            continue
        dest = os.path.expanduser(args[-1])
        if os.path.isdir(dest):
            # moving into a directory: risky only if a same-named file is already there
            if any(os.path.exists(os.path.join(dest, os.path.basename(a.rstrip('/')))) for a in args[:-1]):
                return True
        elif os.path.exists(dest):
            return True
    return False


ROOT_WIPE_MESSAGE = "Refused: recursively deleting the filesystem root is permanently blocked."
GUI_INPUT_MESSAGE = "Rejected command: direct GUI input tools are not permitted in shell commands."


def classify_command(cmd) -> Tuple[str, str]:
    """Return (decision, reason). Order matters: blocks are decided BEFORE any confirmation."""
    cmd = (cmd or "").strip()
    if not cmd:
        return BLOCKED, "No command provided"
    if is_hard_blocked_command(cmd):
        return BLOCKED, ROOT_WIPE_MESSAGE
    if is_gui_input_shell_command(cmd):
        return BLOCKED, GUI_INPUT_MESSAGE
    if is_risky_command(cmd):
        return CONFIRM, "high-impact command"
    return ALLOW, ""
