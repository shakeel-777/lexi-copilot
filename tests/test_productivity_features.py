#!/usr/bin/env python3
"""
Unit tests for Lexi Daily Driver & Productivity Features:
- Conversions, Math & World Clock (_eval_conversions)
- Download Sentinel & Active Download Tracking (lexi_downloads)
- Dynamic Island Focus & Pomodoro Timer (lexi_productivity.FocusTimer)
- Clipboard History Vault (lexi_productivity.ClipboardManager)
- Intent routing for all new capabilities
"""

import os
import sys
import time
import tempfile
import unittest

PROJECT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)

import lexi_intents as I
from lexi_capabilities import REGISTRY
from lexi_downloads import DownloadItem, DownloadSentinel
from lexi_productivity import FocusTimer, ClipboardManager


class TestConversionsAndWorldClock(unittest.TestCase):

    def test_currency_conversion(self):
        res = I._eval_conversions("100 usd in inr")
        self.assertIsNotNone(res)
        self.assertIn("USD", res)
        self.assertIn("INR", res)
        self.assertIn("8,440", res)

        res_eur = I._eval_conversions("50 eur to usd")
        self.assertIsNotNone(res_eur)
        self.assertIn("EUR", res_eur)
        self.assertIn("USD", res_eur)

    def test_units_conversion(self):
        # Distance
        res_km = I._eval_conversions("100 km in miles")
        self.assertIsNotNone(res_km)
        self.assertIn("62.14", res_km)
        self.assertIn("miles", res_km)

        # Weight
        res_wt = I._eval_conversions("70 kg in lbs")
        self.assertIsNotNone(res_wt)
        self.assertIn("154.32", res_wt)
        self.assertIn("lbs", res_wt)

        # Temperature
        res_temp_f = I._eval_conversions("100 c in f")
        self.assertIsNotNone(res_temp_f)
        self.assertIn("212", res_temp_f)

        res_temp_c = I._eval_conversions("86 f in c")
        self.assertIsNotNone(res_temp_c)
        self.assertIn("30", res_temp_c)

        # Data
        res_data = I._eval_conversions("100 gb in mb")
        self.assertIsNotNone(res_data)
        self.assertIn("102,400", res_data)

    def test_world_clock(self):
        for city in ("tokyo", "new york", "london", "sydney", "dubai", "paris"):
            res = I._eval_conversions(f"time in {city}")
            self.assertIsNotNone(res, f"Expected time for {city}")
            self.assertIn("The current time in", res)


class TestDownloadSentinel(unittest.TestCase):

    def test_download_item_formatting(self):
        item = DownloadItem("/tmp/archlinux-2026.iso.crdownload")
        self.assertEqual(item.target_name, "archlinux-2026.iso")
        item.update_size(1024 * 1024 * 50, time.time())
        self.assertEqual(item.format_size(), "50.0 MB")

    def test_download_sentinel_scans_and_detects(self):
        with tempfile.TemporaryDirectory() as d:
            sentinel = DownloadSentinel(download_dir=d)
            self.assertFalse(sentinel.has_active_downloads())

            # Create an active partial download
            test_dl = os.path.join(d, "large_model.bin.crdownload")
            with open(test_dl, "wb") as f:
                f.write(b"x" * 1024 * 100)

            sentinel.scan_once()
            self.assertTrue(sentinel.has_active_downloads())
            active = sentinel.get_active_downloads()
            self.assertEqual(len(active), 1)
            self.assertEqual(active[0]["filename"], "large_model.bin")

            # Simulate download finished (renamed to target without .crdownload)
            target = os.path.join(d, "large_model.bin")
            os.rename(test_dl, target)

            events = []
            sentinel.add_listener(lambda e: events.append(e))
            sentinel.scan_once()

            self.assertFalse(sentinel.has_active_downloads())
            completed_events = [e for e in events if e.get("event") == "completed"]
            self.assertTrue(len(completed_events) > 0)


class TestFocusTimer(unittest.TestCase):

    def test_focus_timer_lifecycle(self):
        timer = FocusTimer()
        st = timer.status()
        self.assertFalse(st["active"])

        res = timer.start(minutes=15, label="Deep Work")
        self.assertTrue(res["success"])
        self.assertEqual(timer.formatted_time(), "15:00")

        st2 = timer.status()
        self.assertTrue(st2["active"])
        self.assertEqual(st2["label"], "Deep Work")

        res_stop = timer.stop()
        self.assertTrue(res_stop["success"])
        st3 = timer.status()
        self.assertFalse(st3["active"])


class TestClipboardManager(unittest.TestCase):

    def test_clipboard_manager_records_and_classifies(self):
        cm = ClipboardManager()
        # Test code classification
        cm.record_text("def solve_problem(): return 42")
        h = cm.get_history(5)
        self.assertTrue(len(h) > 0)
        self.assertEqual(h[0]["type"], "code")

        # Test URL classification
        cm.record_text("https://github.com/shakeel-777/lexi-copilot")
        h2 = cm.get_history(5)
        self.assertEqual(h2[0]["type"], "url")

        # Test Color classification
        cm.record_text("#ff5733")
        h3 = cm.get_history(5)
        self.assertEqual(h3[0]["type"], "color")


class TestIntentRoutingProductivity(unittest.TestCase):

    def test_conversions_route_to_reply(self):
        queries = ["100 usd in inr", "100 km in miles", "100 c in f", "time in tokyo"]
        for q in queries:
            r = I.route(q)
            self.assertEqual(r.kind, I.REPLY, f"Query '{q}' should route to REPLY")
            self.assertTrue(len(r.text) > 0)

    def test_focus_intents(self):
        r_start = I.route("focus 25 minutes")
        self.assertEqual(r_start.kind, I.ACTION)
        self.assertEqual(r_start.name, "focus_timer")
        self.assertEqual(r_start.args["action"], "start")
        self.assertEqual(r_start.args["minutes"], 25)

        r_pom = I.route("pomodoro")
        self.assertEqual(r_pom.kind, I.ACTION)
        self.assertEqual(r_pom.name, "focus_timer")
        self.assertEqual(r_pom.args["action"], "start")

        r_stop = I.route("stop timer")
        self.assertEqual(r_stop.kind, I.ACTION)
        self.assertEqual(r_stop.name, "focus_timer")
        self.assertEqual(r_stop.args["action"], "stop")

    def test_clipboard_history_intents(self):
        r_hist = I.route("clipboard history")
        self.assertEqual(r_hist.kind, I.ACTION)
        self.assertEqual(r_hist.name, "clipboard_history")
        self.assertEqual(r_hist.args["action"], "list")

        r_copy = I.route("copy item 2")
        self.assertEqual(r_copy.kind, I.ACTION)
        self.assertEqual(r_copy.name, "clipboard_history")
        self.assertEqual(r_copy.args["action"], "copy")
        self.assertEqual(r_copy.args["index"], 2)

    def test_download_status_intent(self):
        r_dl = I.route("downloads status")
        self.assertEqual(r_dl.kind, I.ACTION)
        self.assertEqual(r_dl.name, "download_status")

    def test_bare_close_clarifies_and_never_kills_unprompted(self):
        for term in ("close", "quit", "kill", "exit"):
            r = I.route(term)
            self.assertEqual(r.kind, I.CLARIFY, f"'{term}' must clarify, never kill an app blindly")
            self.assertIn("Close what?", r.text)

    def test_contradictory_open_and_close_sequence_rejected(self):
        # Normalised typo: "openc hrome then close it"
        r1 = I.route("openc hrome then close it")
        self.assertEqual(r1.kind, I.REPLY)
        self.assertIn("I won't open and immediately close", r1.text)

        r2 = I.route("open chrome and close chrome")
        self.assertEqual(r2.kind, I.REPLY)
        self.assertIn("I won't open and immediately close", r2.text)

    def test_clipboard_intents_safe_routing(self):
        # Bare clipboard
        r_bare = I.route("clipboard")
        self.assertEqual((r_bare.kind, r_bare.name, r_bare.args), (I.ACTION, "clipboard_history", {"action": "list"}))

        # Open clipboard
        r_open = I.route("open clipboard")
        self.assertEqual((r_open.kind, r_open.name, r_open.args), (I.ACTION, "clipboard_history", {"action": "list"}))

        # Close clipboard
        r_close = I.route("close clipboard")
        self.assertEqual(r_close.kind, I.REPLY)
        self.assertIn("nothing to close", r_close.text)


if __name__ == "__main__":
    unittest.main()
