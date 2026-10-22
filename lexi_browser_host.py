#!/usr/bin/env python3
"""
Lexi Chrome Native Messaging Bridge (`lexi_browser_host.py`).

Architecture:
1. When launched by Chrome/Chromium as a Native Messaging host (`com.lexi.browser_bridge`),
   speaks length-prefixed JSON on stdin/stdout with the Lexi YouTube Bridge MV3 extension
   and opens a private per-user Unix domain socket (`0600`) at:
     $XDG_RUNTIME_DIR/lexi-browser-bridge.sock
   (fallback: /tmp/lexi-browser-bridge-<uid>.sock).
2. Does NOT enable Chrome remote debugging and does NOT expose any HTTP or TCP server.
3. Validates all requests and responses strictly:
   - Only allows declared structured commands (`youtube_play_fullscreen`).
   - Rejects unknown commands, malformed payloads, unexpected keys, and any JavaScript/script strings.
"""

import json
import os
import re
import socket
import struct
import subprocess
import sys
import threading
import time
import uuid

HOST_NAME = "com.lexi.browser_bridge"
YOUTUBE_COMMAND = "youtube_play_fullscreen"
CLOSE_TABS_COMMAND = "close_named_tabs"
SUPPORTED_COMMANDS = frozenset({YOUTUBE_COMMAND, CLOSE_TABS_COMMAND})
ALLOWED_REQUEST_KEYS = frozenset({"command", "query", "target", "request_id"})
# close_named_tabs may only ever name one of these fixed sites (or both).  The
# extension owns the exact URL patterns; the host never sends a URL or selector.
CLOSE_TARGET_SITES = ("whatsapp", "instagram")
CLOSE_TARGETS = frozenset(CLOSE_TARGET_SITES) | {"both"}
FORBIDDEN_REQUEST_KEYS = frozenset({
    "js", "javascript", "script", "code", "eval", "function", "fn",
    "shell", "cmd", "exec", "html", "url", "href", "onclick", "onload"
})

_FORBIDDEN_QUERY_RE = re.compile(
    r"(?:javascript\s*:|data\s*:\s*text/html|<\s*/?\s*script\b|\beval\s*\("
    r"|\bnew\s+Function\b|\bFunction\s*\(|\bdocument\s*\.|\bwindow\s*\."
    r"|\bchrome\s*\.|\bglobalThis\s*\.|=>|[;{}])",
    re.IGNORECASE,
)
_CONTROL_CHAR_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_REQUEST_ID_RE = re.compile(r"^[a-zA-Z0-9_.-]{1,64}$")

MAX_NATIVE_MESSAGE_BYTES = 1024 * 1024  # 1 MiB cap


def _youtube_failure(reason, request_id=None, rejected=False):
    resp = {
        "success": False,
        "search_verified": False,
        "watch_verified": False,
        "playing_verified": False,
        "fullscreen_verified": False,
        "title": "",
        "url": "",
        "reason": reason,
    }
    if rejected:
        resp["rejected"] = True
    if request_id:
        resp["request_id"] = request_id
    return resp


def _close_failure(reason, request_id=None, target="", rejected=False):
    resp = {
        "success": False,
        "verified": False,
        "target": target if target in CLOSE_TARGETS else "",
        "matched_count": 0,
        "closed_count": 0,
        "remaining_count": 0,
        "per_target": {},
        "reason": reason,
    }
    if rejected:
        resp["rejected"] = True
    if request_id:
        resp["request_id"] = request_id
    return resp


def failure_response(command, reason, request_id=None, target="", rejected=False):
    """Failure payload shaped for the given command (never optimistic)."""
    if command == CLOSE_TABS_COMMAND:
        return _close_failure(reason, request_id, target, rejected)
    return _youtube_failure(reason, request_id, rejected)


def get_bridge_socket_path():
    """Return the user-private Unix domain socket path for the Native Messaging host."""
    override = os.environ.get("LEXI_BROWSER_BRIDGE_SOCKET", "").strip()
    if override:
        return override
    runtime_dir = os.environ.get("XDG_RUNTIME_DIR", "").strip()
    if runtime_dir and os.path.isdir(runtime_dir):
        return os.path.join(runtime_dir, "lexi-browser-bridge.sock")
    uid = os.getuid() if hasattr(os, "getuid") else 1000
    return f"/tmp/lexi-browser-bridge-{uid}.sock"


def _validate_close_request(payload, command):
    """Validate a close_named_tabs request: enum target only, no free text."""
    if "query" in payload:
        return False, _close_failure(
            "Malformed payload: unexpected field 'query'", rejected=True)
    target = payload.get("target")
    if not isinstance(target, str) or target.strip().lower() not in CLOSE_TARGETS:
        return False, _close_failure(
            "Malformed payload: 'target' must be one of "
            + ", ".join(sorted(CLOSE_TARGETS)), rejected=True)
    req_id = payload.get("request_id")
    if req_id is None:
        req_id = f"req_{uuid.uuid4().hex[:12]}"
    elif not isinstance(req_id, str) or not _REQUEST_ID_RE.match(req_id):
        return False, _close_failure(
            "Malformed payload: invalid 'request_id'", rejected=True)
    return True, {
        "request_id": req_id,
        "command": command,
        "target": target.strip().lower(),
    }


def validate_native_request(payload):
    """Validate a structured browser command payload before sending to or accepting in the bridge.

    Returns `(is_valid: bool, normalized_or_error: dict)`.
    Rejects unknown commands, malformed payloads, unexpected keys, and any JS/code strings.
    """
    if not isinstance(payload, dict):
        return False, {
            "success": False,
            "rejected": True,
            "search_verified": False,
            "watch_verified": False,
            "playing_verified": False,
            "fullscreen_verified": False,
            "title": "",
            "url": "",
            "reason": "Malformed payload: expected a JSON object",
        }

    for k in payload.keys():
        k_str = str(k)
        if k_str.lower() in FORBIDDEN_REQUEST_KEYS:
            return False, {
                "success": False,
                "rejected": True,
                "search_verified": False,
                "watch_verified": False,
                "playing_verified": False,
                "fullscreen_verified": False,
                "title": "",
                "url": "",
                "reason": f"Rejected payload: forbidden executable field '{k_str}' is not allowed",
            }
        if k_str not in ALLOWED_REQUEST_KEYS:
            return False, {
                "success": False,
                "rejected": True,
                "search_verified": False,
                "watch_verified": False,
                "playing_verified": False,
                "fullscreen_verified": False,
                "title": "",
                "url": "",
                "reason": f"Malformed payload: unexpected field '{k_str}'",
            }

    command = payload.get("command")
    if not isinstance(command, str) or not command.strip():
        return False, {
            "success": False,
            "rejected": True,
            "search_verified": False,
            "watch_verified": False,
            "playing_verified": False,
            "fullscreen_verified": False,
            "title": "",
            "url": "",
            "reason": "Malformed payload: missing string 'command'",
        }

    command = command.strip()
    if command not in SUPPORTED_COMMANDS:
        return False, {
            "success": False,
            "rejected": True,
            "search_verified": False,
            "watch_verified": False,
            "playing_verified": False,
            "fullscreen_verified": False,
            "title": "",
            "url": "",
            "reason": f"Unsupported browser command: '{command}'",
        }

    if command == CLOSE_TABS_COMMAND:
        return _validate_close_request(payload, command)

    if "target" in payload:
        return False, _youtube_failure(
            "Malformed payload: unexpected field 'target'", rejected=True)

    query = payload.get("query")
    if not isinstance(query, str):
        return False, {
            "success": False,
            "rejected": True,
            "search_verified": False,
            "watch_verified": False,
            "playing_verified": False,
            "fullscreen_verified": False,
            "title": "",
            "url": "",
            "reason": "Malformed payload: 'query' must be a string",
        }

    query_clean = query.strip()
    if not query_clean or len(query_clean) > 200:
        return False, {
            "success": False,
            "rejected": True,
            "search_verified": False,
            "watch_verified": False,
            "playing_verified": False,
            "fullscreen_verified": False,
            "title": "",
            "url": "",
            "reason": "Malformed payload: 'query' must be 1 to 200 characters",
        }

    if _CONTROL_CHAR_RE.search(query_clean):
        return False, {
            "success": False,
            "rejected": True,
            "search_verified": False,
            "watch_verified": False,
            "playing_verified": False,
            "fullscreen_verified": False,
            "title": "",
            "url": "",
            "reason": "Malformed payload: 'query' contains control characters",
        }

    if _FORBIDDEN_QUERY_RE.search(query_clean):
        return False, {
            "success": False,
            "rejected": True,
            "search_verified": False,
            "watch_verified": False,
            "playing_verified": False,
            "fullscreen_verified": False,
            "title": "",
            "url": "",
            "reason": "Rejected payload: JavaScript or code expressions are not permitted in 'query'",
        }

    req_id = payload.get("request_id")
    if req_id is None:
        req_id = f"req_{uuid.uuid4().hex[:12]}"
    elif not isinstance(req_id, str) or not _REQUEST_ID_RE.match(req_id):
        return False, {
            "success": False,
            "rejected": True,
            "search_verified": False,
            "watch_verified": False,
            "playing_verified": False,
            "fullscreen_verified": False,
            "title": "",
            "url": "",
            "reason": "Malformed payload: invalid 'request_id'",
        }

    return True, {
        "request_id": req_id,
        "command": command,
        "query": query_clean,
    }


def normalize_native_response(raw_resp):
    """Normalize and sanitize the structured response returned by the Chrome extension."""
    if not isinstance(raw_resp, dict):
        return {
            "success": False,
            "search_verified": False,
            "watch_verified": False,
            "playing_verified": False,
            "fullscreen_verified": False,
            "title": "",
            "url": "",
            "reason": "Malformed response from browser extension",
        }

    search_v = bool(raw_resp.get("search_verified") is True)
    watch_v = bool(raw_resp.get("watch_verified") is True)
    playing_v = bool(raw_resp.get("playing_verified") is True)
    fullscreen_v = bool(raw_resp.get("fullscreen_verified") is True)

    # Watch, playback, and fullscreen cannot be true unless search & watch succeeded
    if not search_v:
        watch_v = False
        playing_v = False
        fullscreen_v = False
    elif not watch_v:
        playing_v = False
        fullscreen_v = False

    title = str(raw_resp.get("title") or "").strip()[:200]
    title = _CONTROL_CHAR_RE.sub(" ", title)
    url = str(raw_resp.get("url") or "").strip()[:500]
    reason = str(raw_resp.get("reason") or "").strip()[:400]

    success = bool(raw_resp.get("success") is True and search_v and watch_v)
    result = {
        "success": success,
        "search_verified": search_v,
        "watch_verified": watch_v,
        "playing_verified": playing_v,
        "fullscreen_verified": fullscreen_v,
        "title": title,
        "url": url,
        "reason": reason,
    }
    if raw_resp.get("request_id"):
        result["request_id"] = str(raw_resp["request_id"])[:64]
    if raw_resp.get("rejected"):
        result["rejected"] = True
    if raw_resp.get("stage"):
        result["stage"] = str(raw_resp["stage"])[:40]
    return result


def _bounded_int(value, hi=200):
    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    return max(0, min(value, hi))


def normalize_close_response(raw_resp, expected_target=""):
    """Sanitise a close_named_tabs response.  `verified` is only true when the
    extension re-checked and found zero matching tabs left."""
    if not isinstance(raw_resp, dict):
        return _close_failure("Malformed response from browser extension",
                              target=expected_target)
    target = str(raw_resp.get("target") or expected_target or "").strip().lower()
    if target not in CLOSE_TARGETS:
        target = expected_target if expected_target in CLOSE_TARGETS else ""
    matched = _bounded_int(raw_resp.get("matched_count"))
    closed = _bounded_int(raw_resp.get("closed_count"))
    remaining = _bounded_int(raw_resp.get("remaining_count"))
    per_target = {}
    raw_pt = raw_resp.get("per_target")
    if isinstance(raw_pt, dict):
        for site in CLOSE_TARGET_SITES:
            entry = raw_pt.get(site)
            if isinstance(entry, dict):
                per_target[site] = {
                    "matched": _bounded_int(entry.get("matched")),
                    "closed": _bounded_int(entry.get("closed")),
                    "remaining": _bounded_int(entry.get("remaining")),
                }
    success = bool(raw_resp.get("success") is True and remaining == 0 and closed <= matched)
    verified = bool(success and raw_resp.get("verified") is True)
    reason = _CONTROL_CHAR_RE.sub(" ", str(raw_resp.get("reason") or "").strip()[:400])
    result = {
        "success": success,
        "verified": verified,
        "target": target,
        "matched_count": matched,
        "closed_count": closed,
        "remaining_count": remaining,
        "per_target": per_target,
        "reason": reason,
    }
    if raw_resp.get("request_id"):
        result["request_id"] = str(raw_resp["request_id"])[:64]
    if raw_resp.get("rejected"):
        result["rejected"] = True
    return result


def normalize_response_for(command, raw_resp, target=""):
    if command == CLOSE_TABS_COMMAND:
        return normalize_close_response(raw_resp, target)
    return normalize_native_response(raw_resp)


def read_native_message(stream):
    """Read a Chrome Native Messaging frame (4-byte native uint32 length + UTF-8 JSON)."""
    raw_len = stream.read(4)
    if not raw_len:
        return None
    if len(raw_len) < 4:
        raise ValueError("Truncated native message length header")
    msg_len = struct.unpack("=I", raw_len)[0]
    if msg_len == 0 or msg_len > MAX_NATIVE_MESSAGE_BYTES:
        raise ValueError(f"Invalid native message length: {msg_len}")
    payload_bytes = b""
    while len(payload_bytes) < msg_len:
        chunk = stream.read(msg_len - len(payload_bytes))
        if not chunk:
            raise ValueError("Truncated native message body")
        payload_bytes += chunk
    return json.loads(payload_bytes.decode("utf-8"))


def write_native_message(stream, obj):
    """Write a Chrome Native Messaging frame (4-byte native uint32 length + UTF-8 JSON)."""
    encoded = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    if len(encoded) > MAX_NATIVE_MESSAGE_BYTES:
        raise ValueError("Native message exceeds maximum size")
    stream.write(struct.pack("=I", len(encoded)))
    stream.write(encoded)
    stream.flush()


class NativeMessagingBridgeHost:
    """Bridges Chrome Native Messaging stdio to a user-private Unix domain socket for Lexi."""

    def __init__(self, stdin_stream=None, stdout_stream=None, socket_path=None):
        self.stdin_stream = stdin_stream or sys.stdin.buffer
        self.stdout_stream = stdout_stream or sys.stdout.buffer
        self.socket_path = socket_path or get_bridge_socket_path()
        self._write_lock = threading.Lock()
        self._pending = {}
        self._pending_lock = threading.Lock()
        self._running = True
        self._server_sock = None

    def _send_to_extension(self, msg):
        with self._write_lock:
            write_native_message(self.stdout_stream, msg)

    def _stdin_reader_loop(self):
        try:
            while self._running:
                msg = read_native_message(self.stdin_stream)
                if msg is None:
                    break
                if isinstance(msg, dict) and msg.get("type") in ("lexi_host_pong", "lexi_content_ready"):
                    continue
                req_id = msg.get("request_id") if isinstance(msg, dict) else None
                with self._pending_lock:
                    slot = None
                    if req_id and req_id in self._pending:
                        slot = self._pending[req_id]
                    elif self._pending:
                        # Fallback to oldest pending request if request_id was omitted
                        slot = self._pending[next(iter(self._pending))]
                    if slot is not None:
                        slot["response"] = normalize_response_for(
                            slot.get("command"), msg, slot.get("target", ""))
                        slot["event"].set()
        except Exception:
            pass
        finally:
            self._running = False
            with self._pending_lock:
                for slot in self._pending.values():
                    slot["event"].set()
            if self._server_sock:
                try:
                    self._server_sock.close()
                except Exception:
                    pass

    def dispatch_request(self, raw_request, timeout=20.0):
        """Validate a request from Lexi, forward it to the Chrome extension, and await the response."""
        is_valid, validated = validate_native_request(raw_request)
        if not is_valid:
            return validated

        req_id = validated["request_id"]
        command = validated["command"]
        target = validated.get("target", "")
        evt = threading.Event()
        slot = {"event": evt, "response": None, "command": command, "target": target}
        with self._pending_lock:
            self._pending[req_id] = slot

        def _fail(reason):
            return failure_response(command, reason, req_id, target)

        try:
            self._send_to_extension(validated)
            if not evt.wait(timeout=timeout):
                return _fail(f"Timed out after {int(timeout)}s waiting for Chrome extension response")
            if slot["response"] is None:
                return _fail("Chrome Native Messaging bridge disconnected before completing request")
            return slot["response"]
        except Exception as exc:
            return _fail(f"Native messaging write error: {exc}")
        finally:
            with self._pending_lock:
                self._pending.pop(req_id, None)

    def _handle_client_conn(self, conn):
        try:
            conn.settimeout(25.0)
            data = b""
            while len(data) < MAX_NATIVE_MESSAGE_BYTES:
                chunk = conn.recv(65536)
                if not chunk:
                    break
                data += chunk
                if b"\n" in data:
                    break
            raw_line = data.decode("utf-8", errors="replace").strip()
            if not raw_line:
                return
            try:
                req_obj = json.loads(raw_line)
            except Exception:
                resp = {
                    "success": False,
                    "rejected": True,
                    "search_verified": False,
                    "watch_verified": False,
                    "playing_verified": False,
                    "fullscreen_verified": False,
                    "title": "",
                    "url": "",
                    "reason": "Malformed JSON payload sent to native bridge socket",
                }
            else:
                resp = self.dispatch_request(req_obj)
            conn.sendall((json.dumps(resp, ensure_ascii=False) + "\n").encode("utf-8"))
        except Exception:
            pass
        finally:
            try:
                conn.close()
            except Exception:
                pass

    def serve_forever(self):
        """Start the stdin reader thread and accept local Unix socket requests from Lexi."""
        if os.path.exists(self.socket_path):
            try:
                os.unlink(self.socket_path)
            except OSError:
                pass

        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._server_sock = srv
        srv.bind(self.socket_path)
        os.chmod(self.socket_path, 0o600)
        srv.listen(8)
        srv.settimeout(1.0)

        reader = threading.Thread(target=self._stdin_reader_loop, daemon=True)
        reader.start()

        try:
            while self._running:
                try:
                    conn, _ = srv.accept()
                except socket.timeout:
                    continue
                except OSError:
                    break
                t = threading.Thread(target=self._handle_client_conn, args=(conn,), daemon=True)
                t.start()
        finally:
            self._running = False
            try:
                srv.close()
            except Exception:
                pass
            if os.path.exists(self.socket_path):
                try:
                    os.unlink(self.socket_path)
                except OSError:
                    pass


def send_browser_command(
    payload,
    socket_path=None,
    timeout=22.0,
    auto_wake_browser=False,
    # Chrome may need several seconds to cold-start the MV3 service worker and
    # its Native Messaging host after the browser has just opened.
    wake_timeout=15.0,
):
    """Client helper called by `lexi.py` to validate and send a structured command to the bridge."""
    is_valid, validated = validate_native_request(payload)
    if not is_valid:
        return validated

    command = validated["command"]
    target = validated.get("target", "")
    req_id = validated["request_id"]
    # Closing tabs must never launch a browser just to close something.
    if command == CLOSE_TABS_COMMAND:
        auto_wake_browser = False

    def _fail(reason):
        return failure_response(command, reason, req_id, target)

    sock_path = socket_path or get_bridge_socket_path()

    if not os.path.exists(sock_path) and auto_wake_browser:
        try:
            subprocess.Popen(
                ["xdg-open", "https://www.youtube.com"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            deadline = time.time() + wake_timeout
            while time.time() < deadline:
                if os.path.exists(sock_path):
                    break
                time.sleep(0.2)
        except Exception:
            pass

    if not os.path.exists(sock_path):
        return _fail(
            "Lexi Chrome Native Messaging bridge is not connected. "
            "Ensure Chrome is open with the Lexi extension loaded and "
            "install_chrome_bridge.sh has been run with your extension ID."
        )

    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(timeout)
            client.connect(sock_path)
            msg_bytes = (json.dumps(validated, ensure_ascii=False) + "\n").encode("utf-8")
            client.sendall(msg_bytes)
            chunks = []
            while True:
                buf = client.recv(65536)
                if not buf:
                    break
                chunks.append(buf)
                if b"\n" in buf:
                    break
            raw_reply = b"".join(chunks).decode("utf-8", errors="replace").strip()
            if not raw_reply:
                return _fail("Empty response from Lexi Chrome Native Messaging host")
            parsed = json.loads(raw_reply)
            return normalize_response_for(command, parsed, target)
    except Exception as exc:
        return _fail(f"Could not reach Lexi Chrome Native Messaging bridge: {exc}")


def main():
    host = NativeMessagingBridgeHost()
    host.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
