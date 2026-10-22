"""Truthful action state machine for Lexi.

    planned -> running -> verified | failed | needs_user

User-facing text is produced from the structured result here, never from
model prose.  Pure Python: executors are injected, so tests need no desktop.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional

from lexi_capabilities import (
    CONFIRM_REQUIRED, REGISTRY, SchemaError, validate_structured,
)

PLANNED = "planned"
RUNNING = "running"
VERIFIED = "verified"
FAILED = "failed"
NEEDS_USER = "needs_user"

TERMINAL_STATES = (VERIFIED, FAILED, NEEDS_USER)

RETRY_WINDOW_SEC = 600          # "try again" only reaches back 10 minutes
MAX_AUTO_RETRIES = 1            # one silent retry of the same action, no loops
CONFIRM_WINDOW_SEC = 300        # a pending send confirmation expires in 5 minutes


@dataclass
class ActionRecord:
    name: str
    args: Dict[str, Any]
    state: str = PLANNED
    detail: str = ""             # honest, user-facing sentence about the outcome
    caveat: str = ""             # what could NOT be verified
    attempts: int = 0
    started: float = field(default_factory=time.time)
    finished: float = 0.0
    source: str = "router"       # "router" | "model"

    @property
    def capability(self):
        return REGISTRY.get(self.name)

    def digest(self) -> str:
        """One compact line for memory / LLM context."""
        arg_txt = ", ".join(f"{k}={v}" for k, v in self.args.items() if k != "message")
        if self.name == "whatsapp_draft" and "message" in self.args:
            arg_txt += ", message=<drafted>"
        line = f"{self.name}({arg_txt}) -> {self.state}"
        if self.detail:
            line += f": {self.detail}"
        return line[:240]


@dataclass
class PendingConfirmation:
    """A sensitive step that is waiting for an explicit user yes."""
    name: str                     # registry name of the sensitive action, e.g. whatsapp_send
    args: Dict[str, Any]
    prompt: str
    created: float = field(default_factory=time.time)

    def expired(self, now: Optional[float] = None) -> bool:
        return ((now or time.time()) - self.created) > CONFIRM_WINDOW_SEC


# -- Executors ---------------------------------------------------------------

# An executor takes validated args and returns a dict like the engine's:
#   {"success": bool, "verified": bool|None, "message": str, ...}
Executor = Callable[[Dict[str, Any]], Dict[str, Any]]


def interpret_result(cap_name: str, result: Any) -> tuple[str, str, str]:
    """Map an executor result dict to (state, detail, caveat). Never optimistic."""
    cap = REGISTRY[cap_name]
    if not isinstance(result, dict):
        return FAILED, cap.failure_text, ""
    msg = str(result.get("message") or "").strip()
    if result.get("needs_user"):
        return NEEDS_USER, msg or "I need you to do something first.", ""
    if result.get("login_required"):
        return NEEDS_USER, msg or "Please log in first, then ask me again.", ""
    if not result.get("success"):
        reason = msg
        if reason and not reason.lower().startswith(cap.failure_text.lower()[:12]):
            return FAILED, f"{cap.failure_text} {reason}", ""
        return FAILED, reason or cap.failure_text, ""
    verified = result.get("verified")
    if verified is False:
        return VERIFIED, msg or "Done.", "I couldn't confirm the final result."
    return VERIFIED, msg or "Done.", ""


Confirmer = Callable[[str], bool]


def run_action(name: str, args: Dict[str, Any], executors: Dict[str, Executor],
               source: str = "router", confirmed: bool = False,
               record: Optional[ActionRecord] = None,
               confirmer: Optional[Confirmer] = None) -> ActionRecord:
    """Validate, apply the execution policy, (maybe) confirm, execute, interpret.

    Policy order: blocked is decided BEFORE any confirmation is offered; only
    high-impact actions ask; ordinary actions run straight away.
    """
    rec = record or ActionRecord(name=name, args=dict(args), source=source)
    rec.state, rec.detail, rec.caveat = PLANNED, "", ""
    try:
        cap = REGISTRY.get(name)
        if cap is None:
            raise SchemaError(f"'{name}' is not a registered action")
        rec.args = validate_structured(name, args)
    except SchemaError as exc:
        rec.state, rec.detail = FAILED, f"I can't do that: {exc}."
        rec.finished = time.time()
        return rec

    if cap.confirmation == CONFIRM_REQUIRED and not confirmed:
        rec.state = NEEDS_USER
        rec.detail = "This needs your explicit confirmation first."
        rec.finished = time.time()
        return rec

    if cap.policy is not None:
        decision, reason = cap.policy(rec.args)
        if decision == "blocked":
            rec.state, rec.detail = FAILED, reason or "That is not permitted."
            rec.finished = time.time()
            return rec
        if decision == "confirm" and not confirmed:
            cmd_txt = str(rec.args.get("command") or rec.args)
            if confirmer is None:
                rec.state = NEEDS_USER
                rec.detail = f"That's a high-impact command ({reason}); I didn't run it:\n{cmd_txt}"
                rec.finished = time.time()
                return rec
            try:
                approved = bool(confirmer(f"{reason.capitalize()}:\n\n{cmd_txt}\n\nRun it?"))
            except Exception:
                approved = False
            if not approved:
                rec.state = NEEDS_USER
                rec.detail = "Okay, I didn't run it."
                rec.finished = time.time()
                return rec

    executor = executors.get(cap.executor) or executors.get(name)
    if executor is None:
        rec.state, rec.detail = FAILED, f"{cap.failure_text} (not available on this system)"
        rec.finished = time.time()
        return rec

    rec.state = RUNNING
    rec.attempts += 1
    try:
        result = executor(rec.args)
    except Exception as exc:          # executor bug must never become fake success
        result = {"success": False, "message": f"unexpected error: {exc}"}
    rec.state, rec.detail, rec.caveat = interpret_result(name, result)
    rec.finished = time.time()
    return rec


# -- Retry boundaries --------------------------------------------------------

def can_retry(rec: Optional[ActionRecord], now: Optional[float] = None) -> tuple[bool, str]:
    """Only the immediately previous, non-sensitive, failed action may be retried."""
    if rec is None:
        return False, "There's nothing recent to retry."
    cap = rec.capability
    if cap is None:
        return False, "I don't know how to repeat that."
    if cap.sensitive or cap.confirmation == CONFIRM_REQUIRED:
        return False, "I won't repeat that without you confirming it again."
    if not cap.retry_safe:
        return False, "Tell me again what you want and I'll set it up fresh."
    if rec.state != FAILED:
        return False, ""                # caller decides what to do for non-failed
    if ((now or time.time()) - (rec.finished or rec.started)) > RETRY_WINDOW_SEC:
        return False, "That was a while ago. Tell me what you'd like again."
    if rec.attempts >= MAX_AUTO_RETRIES + 1:
        return False, "That's failed twice already, so I'd rather not keep guessing."
    return True, ""


# -- Reply rendering ----------------------------------------------------------

def render_reply(rec: ActionRecord) -> str:
    """The only text shown for an action. Built from structured state."""
    if rec.state == VERIFIED:
        text = rec.detail or "Done."
        if rec.caveat:
            text = f"{text.rstrip('.')}. {rec.caveat}"
        return text
    if rec.state == NEEDS_USER:
        return rec.detail
    if rec.state == FAILED:
        return rec.detail or (rec.capability.failure_text if rec.capability else "That didn't work.")
    # planned/running should never be shown as an outcome
    return "I'm still working on that."
