"""Unit tests for Windows feature parity modules."""

import os
import sys
import unittest
from pathlib import Path

# Add windows directory to sys.path
win_dir = Path(__file__).parent.parent / "windows"
if str(win_dir) not in sys.path:
    sys.path.insert(0, str(win_dir))

from lexi_win_actions import SystemActionEngineWindows, PhoneMirrorManagerWindows
from lexi_win_drop import format_size, get_broadcast_ip


class TestWindowsParity(unittest.TestCase):
    def setUp(self):
        self.actions = SystemActionEngineWindows.get()
        self.mirror = PhoneMirrorManagerWindows.get()

    def test_singleton_instances(self):
        self.assertIs(SystemActionEngineWindows.get(), self.actions)
        self.assertIs(PhoneMirrorManagerWindows.get(), self.mirror)

    def test_format_size(self):
        self.assertEqual(format_size(0), "0 B")
        self.assertEqual(format_size(1024), "1.0 KB")
        self.assertEqual(format_size(1024 * 1024 * 50), "50.0 MB")
        self.assertEqual(format_size(1024 * 1024 * 1024 * 2), "2.0 GB")

    def test_broadcast_ip(self):
        self.assertEqual(get_broadcast_ip("192.168.1.45"), "192.168.1.255")
        self.assertEqual(get_broadcast_ip("10.0.0.5"), "10.0.0.255")

    def test_kill_process_protected(self):
        res = self.actions.kill_process("explorer.exe")
        self.assertFalse(res.get("success"))
        self.assertIn("critical", res.get("message", "").lower())

    def test_kill_process_empty(self):
        res = self.actions.kill_process("")
        self.assertFalse(res.get("success"))

    def test_inspect_port_invalid(self):
        res = self.actions.inspect_port(-1)
        self.assertFalse(res.get("success"))
        res2 = self.actions.inspect_port(999999)
        self.assertFalse(res2.get("success"))

    def test_media_control_invalid(self):
        res, msg = self.actions.media_control("invalid_action")
        self.assertFalse(res)

    def test_system_diagnostics(self):
        diag = self.actions.run_system_diagnostics()
        self.assertIsInstance(diag, dict)
        self.assertIn("scrcpy", diag)
        self.assertIn("adb", diag)
        self.assertIn("network", diag)
        self.assertIn("ai", diag)
        self.assertIn("all_ok", diag)
        self.assertIn("ip", diag["network"])
        self.assertEqual(diag["network"]["shareme_port"], 7890)

    def test_enable_wireless_adb_call(self):
        res = self.actions.enable_wireless_adb()
        self.assertIsInstance(res, dict)
        self.assertIn("success", res)
        self.assertIn("message", res)

    def test_hotkey_listener_instantiation(self):
        try:
            import PySide6
        except ImportError:
            self.skipTest("PySide6 not installed in current environment")
        from lexi_win_island import WinGlobalHotkeyListener
        listener = WinGlobalHotkeyListener()
        self.assertTrue(listener._running)
        listener.stop()


if __name__ == "__main__":
    unittest.main()
