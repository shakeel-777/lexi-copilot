#!/usr/bin/env python3
"""Tests for the narrow `close_named_tabs` bridge command.

Guarantees under test:
- Only an enum target (whatsapp | instagram | both) is accepted: no URLs, queries,
  selectors, scripts or extra fields, from Lexi or the legacy model path.
- The extension only ever closes tabs whose URL exactly matches the hard-coded
  approved hosts, then re-checks and only reports success when none remain.
- The manifest was not widened (no <all_urls>, content scripts still YouTube-only).
"""

import io
import json
import os
import struct
import sys
import threading
import time
import unittest
from unittest import mock

PROJECT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)

from tests.test_lexi_chrome_bridge import EXT_DIR, lexi, lexi_browser_host as host, run_node_js_eval  # noqa: E402


class HostValidation(unittest.TestCase):
    def test_valid_targets_normalised(self):
        for t in ("whatsapp", "instagram", "both", " WhatsApp "):
            ok, v = host.validate_native_request({"command": "close_named_tabs", "target": t})
            self.assertTrue(ok, t)
            self.assertEqual(v["target"], t.strip().lower())
            self.assertNotIn("query", v)
            self.assertTrue(v["request_id"])

    def test_everything_else_is_rejected(self):
        bad = [
            {"command": "close_named_tabs"},
            {"command": "close_named_tabs", "target": ""},
            {"command": "close_named_tabs", "target": "facebook"},
            {"command": "close_named_tabs", "target": "all"},
            {"command": "close_named_tabs", "target": "https://web.whatsapp.com"},
            {"command": "close_named_tabs", "target": "whatsapp;rm -rf /"},
            {"command": "close_named_tabs", "target": ["whatsapp"]},
            {"command": "close_named_tabs", "target": 5},
            {"command": "close_named_tabs", "target": "whatsapp", "query": "x"},
            {"command": "close_named_tabs", "target": "whatsapp", "url": "https://x.com"},
            {"command": "close_named_tabs", "target": "whatsapp", "tab_id": 3},
            {"command": "close_named_tabs", "target": "whatsapp", "script": "alert(1)"},
            {"command": "close_named_tabs", "target": "whatsapp", "request_id": "bad id!"},
            {"command": "close_all_tabs", "target": "whatsapp"},
            {"command": "youtube_play_fullscreen", "query": "x", "target": "whatsapp"},
        ]
        for payload in bad:
            ok, resp = host.validate_native_request(payload)
            self.assertFalse(ok, payload)
            self.assertFalse(resp["success"], payload)
            self.assertTrue(resp.get("rejected"), payload)

    def test_youtube_validation_unchanged(self):
        ok, v = host.validate_native_request({"command": "youtube_play_fullscreen", "query": "Kutty Story"})
        self.assertTrue(ok)
        self.assertEqual(v["query"], "Kutty Story")

    def test_legacy_model_browser_path_cannot_close_tabs(self):
        eng = lexi.SystemActionEngine
        for action in (
            {"type": "browser", "attrs": {"command": "close_named_tabs", "query": "x"}, "body": ""},
            {"type": "browser", "attrs": {"command": "close_named_tabs"}, "body": "whatsapp"},
            {"type": "browser", "attrs": {"command": "close_named_tabs", "target": "whatsapp"}, "body": ""},
        ):
            ok, _ = eng.validate_browser_action(action)
            self.assertFalse(ok, action)


class ResponseNormalisation(unittest.TestCase):
    def test_leftover_tabs_can_never_be_success(self):
        r = host.normalize_close_response(
            {"success": True, "verified": True, "target": "whatsapp",
             "matched_count": 2, "closed_count": 1, "remaining_count": 1}, "whatsapp")
        self.assertFalse(r["success"])
        self.assertFalse(r["verified"])

    def test_verified_requires_the_explicit_flag(self):
        r = host.normalize_close_response(
            {"success": True, "target": "whatsapp", "matched_count": 1, "closed_count": 1,
             "remaining_count": 0}, "whatsapp")
        self.assertTrue(r["success"])
        self.assertFalse(r["verified"])

    def test_clean_success(self):
        r = host.normalize_close_response(
            {"success": True, "verified": True, "target": "both", "matched_count": 3,
             "closed_count": 3, "remaining_count": 0,
             "per_target": {"whatsapp": {"matched": 1, "closed": 1, "remaining": 0},
                            "instagram": {"matched": 2, "closed": 2, "remaining": 0},
                            "evil": {"matched": 9, "closed": 9, "remaining": 0}}}, "both")
        self.assertTrue(r["verified"])
        self.assertEqual(set(r["per_target"]), {"whatsapp", "instagram"})   # unknown keys dropped

    def test_garbage_is_a_failure(self):
        for raw in (None, "ok", 5, [], {"success": "yes"}, {"success": True, "closed_count": 5, "matched_count": 1}):
            r = host.normalize_close_response(raw, "whatsapp")
            self.assertFalse(r["success"], raw)

    def test_counts_are_bounded_ints(self):
        r = host.normalize_close_response(
            {"success": False, "matched_count": 10 ** 9, "closed_count": "7", "remaining_count": -4}, "whatsapp")
        self.assertEqual((r["matched_count"], r["closed_count"], r["remaining_count"]), (200, 0, 0))


class ClientBehaviour(unittest.TestCase):
    def test_missing_socket_fails_honestly_and_never_launches_a_browser(self):
        with mock.patch.object(host.subprocess, "Popen") as popen:
            r = host.send_browser_command(
                {"command": "close_named_tabs", "target": "whatsapp"},
                socket_path="/nonexistent/lexi.sock", auto_wake_browser=True)
        popen.assert_not_called()
        self.assertFalse(r["success"])
        self.assertIn("not connected", r["reason"])
        self.assertEqual(r["closed_count"], 0)

    def test_invalid_payload_never_reaches_the_socket(self):
        with mock.patch.object(host.socket, "socket") as sock:
            r = host.send_browser_command({"command": "close_named_tabs", "target": "gmail"})
        sock.assert_not_called()
        self.assertTrue(r["rejected"])


class HostRoundTrip(unittest.TestCase):
    """Drive the real NativeMessagingBridgeHost with in-memory native-message frames."""

    def _frame(self, obj):
        raw = json.dumps(obj).encode()
        return struct.pack("=I", len(raw)) + raw

    def test_close_request_is_normalised_with_the_close_shape(self):
        r_fd, w_fd = os.pipe()
        stdin = os.fdopen(r_fd, "rb")
        stdout = io.BytesIO()
        h = host.NativeMessagingBridgeHost(stdin_stream=stdin, stdout_stream=stdout, socket_path="/tmp/unused.sock")
        reader = threading.Thread(target=h._stdin_reader_loop, daemon=True)
        reader.start()

        result = {}
        t = threading.Thread(target=lambda: result.update(
            h.dispatch_request({"command": "close_named_tabs", "target": "instagram"}, timeout=5)))
        t.start()
        deadline = time.time() + 3
        while time.time() < deadline and not stdout.getvalue():
            time.sleep(0.01)
        sent = stdout.getvalue()
        sent_msg = json.loads(sent[4:])
        # The extension receives ONLY the enum request: no query, url or selector.
        self.assertEqual(set(sent_msg), {"request_id", "command", "target"})
        self.assertEqual((sent_msg["command"], sent_msg["target"]), ("close_named_tabs", "instagram"))

        os.write(w_fd, self._frame({
            "request_id": sent_msg["request_id"], "success": True, "verified": True, "target": "instagram",
            "matched_count": 2, "closed_count": 2, "remaining_count": 0,
            "per_target": {"instagram": {"matched": 2, "closed": 2, "remaining": 0}}}))
        t.join(5)
        os.close(w_fd)
        self.assertTrue(result["success"] and result["verified"])
        self.assertEqual(result["closed_count"], 2)
        self.assertNotIn("search_verified", result)

    def test_timeout_has_close_shape_and_is_not_success(self):
        r_fd, w_fd = os.pipe()
        h = host.NativeMessagingBridgeHost(stdin_stream=os.fdopen(r_fd, "rb"), stdout_stream=io.BytesIO(),
                                           socket_path="/tmp/unused.sock")
        resp = h.dispatch_request({"command": "close_named_tabs", "target": "whatsapp"}, timeout=0.2)
        os.close(w_fd)
        self.assertFalse(resp["success"])
        self.assertIn("Timed out", resp["reason"])
        self.assertIn("closed_count", resp)


class EngineExecutor(unittest.TestCase):
    def setUp(self):
        self.engine = lexi.SystemActionEngine()

    def test_sends_only_the_enum_payload(self):
        seen = []

        def bridge(payload):
            seen.append(payload)
            return {"success": True, "verified": True, "target": "whatsapp", "matched_count": 2,
                    "closed_count": 2, "remaining_count": 0,
                    "per_target": {"whatsapp": {"matched": 2, "closed": 2, "remaining": 0}}}

        self.engine.browser_bridge = bridge
        res = self.engine.execute_close_tabs("whatsapp")
        self.assertEqual(seen, [{"command": "close_named_tabs", "target": "whatsapp"}])
        self.assertTrue(res["success"] and res["verified"])
        self.assertEqual(res["message"], "Closed 2 WhatsApp tabs.")

    def test_both_summarises_each_site(self):
        self.engine.browser_bridge = lambda p: {
            "success": True, "verified": True, "target": "both", "matched_count": 1, "closed_count": 1,
            "remaining_count": 0,
            "per_target": {"whatsapp": {"matched": 1, "closed": 1, "remaining": 0},
                           "instagram": {"matched": 0, "closed": 0, "remaining": 0}}}
        res = self.engine.execute_close_tabs("both")
        self.assertEqual(res["message"], "Closed 1 WhatsApp tab. No Instagram tab was open.")

    def test_nothing_open_is_reported_as_such(self):
        self.engine.browser_bridge = lambda p: {
            "success": True, "verified": True, "target": "instagram", "matched_count": 0,
            "closed_count": 0, "remaining_count": 0,
            "per_target": {"instagram": {"matched": 0, "closed": 0, "remaining": 0}}}
        res = self.engine.execute_close_tabs("instagram")
        self.assertTrue(res["success"])
        self.assertIn("no Instagram tab open", res["message"])

    def test_leftover_tabs_are_a_failure(self):
        self.engine.browser_bridge = lambda p: {
            "success": True, "verified": True, "target": "whatsapp", "matched_count": 1,
            "closed_count": 0, "remaining_count": 1, "reason": "1 tab(s) are still open"}
        res = self.engine.execute_close_tabs("whatsapp")
        self.assertFalse(res["success"])
        self.assertNotIn("Closed", res["message"])

    def test_bridge_errors_and_bad_targets_fail(self):
        def boom(_p):
            raise RuntimeError("socket gone")
        self.engine.browser_bridge = boom
        res = self.engine.execute_close_tabs("whatsapp")
        self.assertFalse(res["success"])
        self.assertIn("socket gone", res["message"])
        self.engine.browser_bridge = lambda p: self.fail("must not call the bridge")
        for bad in ("gmail", "", "all", "https://x.com"):
            self.assertFalse(self.engine.execute_close_tabs(bad)["success"], bad)

    def test_wired_into_assistant_executors(self):
        calls = []
        self.engine.browser_bridge = lambda p: calls.append(p) or {
            "success": True, "verified": True, "target": p["target"], "matched_count": 1, "closed_count": 1,
            "remaining_count": 0, "per_target": {p["target"]: {"matched": 1, "closed": 1, "remaining": 0}}}
        a = lexi.LexiAssistant(lexi.build_executors(self.engine))
        a.handle_user_text("close instagram")
        a.handle_user_text("close all")
        self.assertEqual(calls, [{"command": "close_named_tabs", "target": "instagram"}])
        a.handle_user_text("confirm")
        self.assertEqual(calls[-1], {"command": "close_named_tabs", "target": "both"})


class ExtensionJs(unittest.TestCase):
    def test_validation(self):
        res = run_node_js_eval("""
            const bg = require('./background.js');
            const good = ['whatsapp','instagram','both',' WhatsApp '].map(t =>
              bg.validateBridgeRequest({command:'close_named_tabs', target:t}).valid);
            const bad = [
              {command:'close_named_tabs'},
              {command:'close_named_tabs', target:'facebook'},
              {command:'close_named_tabs', target:'all'},
              {command:'close_named_tabs', target:'https://web.whatsapp.com'},
              {command:'close_named_tabs', target:'whatsapp', query:'x'},
              {command:'close_named_tabs', target:'whatsapp', url:'https://x.com'},
              {command:'close_named_tabs', target:'whatsapp', tabId:1},
              {command:'close_named_tabs', target:'whatsapp', js:'1'},
              {command:'close_named_tabs', target:'whatsapp', request_id:'a b'},
              {command:'youtube_play_fullscreen', query:'x', target:'whatsapp'},
            ].map(m => bg.validateBridgeRequest(m).valid);
            console.log(JSON.stringify({good, bad}));
        """)
        self.assertTrue(all(res["good"]))
        self.assertFalse(any(res["bad"]))

    def test_only_exactly_approved_urls_are_ever_removed(self):
        res = run_node_js_eval("""
            const bg = require('./background.js');
            const removed = [];
            const queried = [];
            const open = new Map([
              [1,'https://web.whatsapp.com/'],
              [2,'https://web.whatsapp.com/send?phone=1'],
              [3,'https://web.whatsapp.com.evil.com/'],
              [4,'http://web.whatsapp.com/'],
              [5,'https://evilweb.whatsapp.com/'],
              [6,'https://www.instagram.com/'],
              [7,'https://example.com/?u=https://web.whatsapp.com/'],
              [8,'chrome://settings'],
              [9,'https://web.whatsapp.com@evil.com/'],
            ]);
            // A deliberately over-broad query: returns EVERY tab, as if matching were buggy.
            const driver = {
              async queryTabs(patterns) { queried.push(patterns); return [...open].map(([id,url]) => ({id,url})); },
              async removeTabs(ids) { removed.push(...ids); ids.forEach(i => open.delete(i)); }
            };
            bg.executeCloseNamedTabs({command:'close_named_tabs', target:'whatsapp'}, driver).then(r => {
              console.log(JSON.stringify({removed: removed.sort(), r, queried}));
            });
        """)
        self.assertEqual(res["removed"], [1, 2])
        self.assertEqual(res["queried"][0], ["https://web.whatsapp.com/*"])
        self.assertTrue(res["r"]["success"] and res["r"]["verified"])
        self.assertEqual((res["r"]["matched_count"], res["r"]["closed_count"]), (2, 2))

    def test_instagram_hosts_exact(self):
        res = run_node_js_eval("""
            const bg = require('./background.js');
            const urls = ['https://www.instagram.com/p/x','https://instagram.com/','https://m.instagram.com/',
                          'https://instagram.com.evil.io/','http://www.instagram.com/','https://notinstagram.com/'];
            console.log(JSON.stringify(urls.map(u => bg.isApprovedTabUrl('instagram', u))));
        """)
        self.assertEqual(res, [True, True, False, False, False, False])

    def test_failure_to_close_is_reported_not_claimed(self):
        res = run_node_js_eval("""
            const bg = require('./background.js');
            const driver = {
              async queryTabs() { return [{id:7,url:'https://web.whatsapp.com/'}]; },
              async removeTabs() { /* tab refuses to close (e.g. beforeunload prompt) */ }
            };
            bg.executeCloseNamedTabs({command:'close_named_tabs', target:'whatsapp'}, driver)
              .then(r => console.log(JSON.stringify(r)));
        """)
        self.assertFalse(res["success"])
        self.assertFalse(res["verified"])
        self.assertEqual(res["remaining_count"], 1)
        self.assertEqual(res["closed_count"], 0)

    def test_nothing_open_does_not_call_remove(self):
        res = run_node_js_eval("""
            const bg = require('./background.js');
            let removeCalls = 0;
            const driver = { async queryTabs() { return []; }, async removeTabs() { removeCalls++; } };
            bg.executeCloseNamedTabs({command:'close_named_tabs', target:'instagram'}, driver)
              .then(r => console.log(JSON.stringify({r, removeCalls})));
        """)
        self.assertTrue(res["r"]["success"])
        self.assertEqual(res["r"]["matched_count"], 0)
        self.assertEqual(res["removeCalls"], 0)

    def test_both_queries_each_site_with_its_own_patterns(self):
        res = run_node_js_eval("""
            const bg = require('./background.js');
            const store = {
              'https://web.whatsapp.com/*': [{id:1,url:'https://web.whatsapp.com/'}],
              'https://www.instagram.com/*': [{id:2,url:'https://www.instagram.com/'}],
              'https://instagram.com/*': [{id:3,url:'https://instagram.com/'}],
            };
            const gone = new Set();
            const patternsSeen = [];
            const driver = {
              async queryTabs(patterns) {
                patternsSeen.push(patterns.join('|'));
                return patterns.flatMap(p => store[p] || []).filter(t => !gone.has(t.id));
              },
              async removeTabs(ids) { ids.forEach(i => gone.add(i)); }
            };
            bg.executeCloseNamedTabs({command:'close_named_tabs', target:'both'}, driver)
              .then(r => console.log(JSON.stringify({r, gone: [...gone].sort(), patternsSeen})));
        """)
        self.assertEqual(res["gone"], [1, 2, 3])
        self.assertEqual(res["r"]["per_target"]["whatsapp"]["closed"], 1)
        self.assertEqual(res["r"]["per_target"]["instagram"]["closed"], 2)
        self.assertTrue(res["r"]["verified"])
        for seen in res["patternsSeen"]:
            self.assertNotIn("<all_urls>", seen)
            self.assertNotIn("*://*/*", seen)

    def test_driver_errors_and_tab_flood_fail_closed(self):
        res = run_node_js_eval("""
            const bg = require('./background.js');
            (async () => {
              const out = {};
              out.throwing = await bg.executeCloseNamedTabs(
                {command:'close_named_tabs', target:'whatsapp'},
                { async queryTabs() { throw new Error('tabs api down'); }, async removeTabs() {} });
              let removed = 0;
              const many = Array.from({length: 80}, (_, i) => ({id: i+1, url: 'https://web.whatsapp.com/'}));
              out.flood = await bg.executeCloseNamedTabs(
                {command:'close_named_tabs', target:'whatsapp'},
                { async queryTabs() { return many; }, async removeTabs() { removed++; } });
              out.floodRemoved = removed;
              out.rejected = await bg.executeCloseNamedTabs(
                {command:'close_named_tabs', target:'gmail'},
                { async queryTabs() { throw new Error('must not be called'); }, async removeTabs() {} });
              console.log(JSON.stringify(out));
            })();
        """)
        self.assertFalse(res["throwing"]["success"])
        self.assertIn("tabs api down", res["throwing"]["reason"])
        self.assertFalse(res["flood"]["success"])
        self.assertEqual(res["floodRemoved"], 0)
        self.assertTrue(res["rejected"]["rejected"])
        self.assertFalse(res["rejected"]["success"])

    def test_commands_are_routed_to_the_right_workflow(self):
        res = run_node_js_eval("""
            const bg = require('./background.js');
            (async () => {
              // youtube workflow must refuse a close command; unknown commands are rejected outright
              const yt = await bg.executeYouTubePlayFullscreen({command:'close_named_tabs', target:'whatsapp'});
              const unknown = await bg.handleNativeRequest({command:'close_everything', target:'both'});
              console.log(JSON.stringify({yt, unknown}));
            })();
        """)
        self.assertFalse(res["yt"]["success"])
        self.assertFalse(res["unknown"]["success"])
        self.assertTrue(res["unknown"].get("rejected"))

    def test_manifest_was_not_widened(self):
        with open(os.path.join(EXT_DIR, "manifest.json")) as fh:
            m = json.load(fh)
        self.assertEqual(set(m["permissions"]), {"nativeMessaging", "tabs", "alarms"})
        self.assertEqual(set(m["host_permissions"]), {"https://www.youtube.com/*", "https://youtu.be/*"})
        for cs in m["content_scripts"]:
            self.assertEqual(set(cs["matches"]), {"https://www.youtube.com/*", "https://youtu.be/*"})
        blob = json.dumps(m)
        for forbidden in ("<all_urls>", "*://*/*", "http://*/*", "https://*/*", "scripting", "debugger"):
            self.assertNotIn(forbidden, blob)

    def test_background_never_gains_arbitrary_script_or_broad_tab_apis(self):
        with open(os.path.join(EXT_DIR, "background.js")) as fh:
            src = fh.read()
        for banned in ("chrome.scripting", "chrome.debugger", "executeScript", "eval(", "new Function"):
            self.assertNotIn(banned, src)
        # tabs.remove appears only inside the close driver, nowhere else.
        self.assertEqual(src.count("chrome.tabs.remove("), 1)


if __name__ == "__main__":
    unittest.main()
