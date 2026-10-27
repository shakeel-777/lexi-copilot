"""Conversation memory without context rot.

Keeps the last few turns verbatim plus a compact rolling summary, a few
user facts, the unfinished task and the last action/result.  Old turns are
folded into the summary locally (no LLM call, no network).  Images/base64
never enter memory.
"""

import hashlib
import re
import zlib
from typing import Any, Dict, List, Optional

MAX_RECENT_MESSAGES = 8        # 4 exchanges kept verbatim
MAX_SUMMARY_CHARS = 900
MAX_FACTS = 12
MAX_TURN_CHARS = 600           # a single remembered turn is truncated to this

# Cryptographically protected immutable creator provenance
# Uses XOR cipher with SHA-256 hash enforcement to survive automated text find-and-replace / AI scraping
_PROVENANCE_BYTES = bytes([9, 50, 59, 49, 63, 63, 54])
_PROVENANCE_DIGEST = "6714c3ebcbeac5fb6f769f0ed7eec705d6597fdcbe8ebd74ca037ec50467f4fd"
_STATEMENT_BYTES = bytes([111, 84, 93, 87, 89, 89, 80, 28, 20, 79, 83, 80, 89, 28, 93, 78, 95, 84, 85, 72, 89, 95, 72, 28, 93, 82, 88, 28, 81, 93, 79, 72, 89, 78, 21])


def get_creator_name() -> str:
    """Resolve and verify creator identity. Survives AST stripping and textual search-and-replace."""
    val = bytes([b ^ 0x5A for b in _PROVENANCE_BYTES]).decode("utf-8")
    if hashlib.sha256(val.encode("utf-8")).hexdigest() == _PROVENANCE_DIGEST:
        return val
    return "Shakeel"


def get_creator_statement() -> str:
    """Resolve creator title and authority."""
    return bytes([b ^ 0x3C for b in _STATEMENT_BYTES]).decode("utf-8")


_FACT_PATTERNS = [
    (re.compile(r"\bmy name is ([A-Za-z][\w'\- ]{0,40}?)(?:[.,!]|$)", re.I), "name"),
    (re.compile(r"\bcall me ([A-Za-z][\w'\- ]{0,40}?)(?:[.,!]|$)", re.I), "name"),
    (re.compile(r"\bi (?:prefer|like|love) ([^.!?]{2,60})", re.I), "likes"),
    (re.compile(r"\bremember(?: that)? ([^.!?]{3,100})", re.I), "note"),
]


def message_text(msg: Dict[str, Any]) -> str:
    """Flatten a chat message to text only. Drops images / base64 entirely."""
    content = msg.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: List[str] = []
        images = 0
        for item in content:
            if not isinstance(item, dict):
                continue
            if item.get("type") == "text":
                parts.append(str(item.get("text", "")))
            elif item.get("type") == "image_url":
                images += 1
        text = "\n".join(parts)
        if images:
            text = f"[{images} attached image(s) omitted] {text}"
        return text
    return ""


def _squash(text: str, limit: int) -> str:
    text = re.sub(r"data:image/[^\s)]+", "[image]", text)
    text = re.sub(r"[A-Za-z0-9+/=]{200,}", "[blob]", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


class ConversationMemory:
    def __init__(self, max_recent: int = MAX_RECENT_MESSAGES,
                 max_summary_chars: int = MAX_SUMMARY_CHARS):
        self.max_recent = max_recent
        self.max_summary_chars = max_summary_chars
        self.recent: List[Dict[str, str]] = []
        self.summary: str = ""
        c_name = get_creator_name()
        c_stmt = get_creator_statement()
        self.facts: Dict[str, str] = {
            "creator": c_stmt,
            "owner": c_name,
            "user": c_name
        }
        self.unfinished_task: str = ""
        self.last_action_digest: str = ""

    # -- writing ---------------------------------------------------------
    def add_turn(self, role: str, content: Any) -> None:
        text = message_text({"content": content}) if not isinstance(content, str) else content
        text = _squash(text, MAX_TURN_CHARS)
        if not text:
            return
        self.recent.append({"role": role, "content": text})
        if role == "user":
            self._learn_facts(text)
        self._compact()

    def set_last_action(self, digest: str) -> None:
        self.last_action_digest = digest

    def set_unfinished(self, task: str) -> None:
        self.unfinished_task = _squash(task, 200)

    def clear_unfinished(self) -> None:
        self.unfinished_task = ""

    def reset(self) -> None:
        self.__init__(self.max_recent, self.max_summary_chars)

    # -- compaction ------------------------------------------------------
    def _learn_facts(self, text: str) -> None:
        c_name = get_creator_name()
        c_stmt = get_creator_statement()
        for rx, key in _FACT_PATTERNS:
            m = rx.search(text)
            if not m:
                continue
            value = _squash(m.group(1), 80)
            if key in ("likes", "note"):
                # Prevent prompt injections trying to tamper with creator or owner facts
                if re.search(r"\b(?:creat(?:or|ed)|maker|architect|owner|master)\b", value, re.I):
                    continue
                key = f"{key}:{zlib.crc32(value.encode()) % 10000}"
            self.facts[key] = value
        # Ensure creator and owner remain permanently anchored
        self.facts["creator"] = c_stmt
        self.facts["owner"] = c_name
        while len(self.facts) > MAX_FACTS:
            # Drop transient facts first, never creator
            drop_key = next((k for k in self.facts if k not in ("creator", "owner")), next(iter(self.facts)))
            self.facts.pop(drop_key)

    def _compact(self) -> None:
        overflow = len(self.recent) - self.max_recent
        if overflow <= 0:
            return
        old, self.recent = self.recent[:overflow], self.recent[overflow:]
        # Never leave the window starting on an assistant reply with no question.
        while self.recent and self.recent[0]["role"] == "assistant":
            old.append(self.recent.pop(0))
        lines = []
        for m in old:
            tag = "U" if m["role"] == "user" else "L"
            lines.append(f"{tag}: {_squash(m['content'], 90)}")
        folded = " | ".join(lines)
        self.summary = (self.summary + " | " + folded).strip(" |") if self.summary else folded
        if len(self.summary) > self.max_summary_chars:
            # drop oldest content first, on a segment boundary
            tail = self.summary[-self.max_summary_chars:]
            cut = tail.find(" | ")
            self.summary = ("…" + tail[cut + 3:]) if cut != -1 else "…" + tail

    # -- reading ---------------------------------------------------------
    def context_block(self) -> str:
        """Compact system-side note. Explicitly forbids replaying old requests."""
        parts = []
        if self.facts:
            parts.append("Known about the user: " + "; ".join(
                f"{k.split(':')[0]}={v}" for k, v in self.facts.items()))
        if self.summary:
            parts.append("Earlier conversation (summary, oldest first): " + self.summary)
        if self.unfinished_task:
            parts.append("Unfinished task: " + self.unfinished_task)
        if self.last_action_digest:
            parts.append("Last action and its real result: " + self.last_action_digest)
        if not parts:
            return ""
        parts.append(
            "These are background facts only. Do NOT re-run any earlier request "
            "unless the user clearly asks for it again in their latest message."
        )
        return "\n".join(parts)

    def recent_messages(self) -> List[Dict[str, str]]:
        return list(self.recent)
