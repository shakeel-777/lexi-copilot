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

    def test_provider_info_resolution(self):
        try:
            import PySide6
        except ImportError:
            self.skipTest("PySide6 not installed in current environment")
        from lexi_win_island import get_provider_info, build_api_headers

        # 1. OpenRouter
        cfg_or = {"provider": "openrouter", "api_key": "sk-or-test12345"}
        info_or = get_provider_info(cfg_or)
        self.assertEqual(info_or["provider"], "openrouter")
        self.assertEqual(info_or["api_base"], "https://openrouter.ai/api/v1")
        self.assertEqual(info_or["model"], "deepseek/deepseek-v4.1-flash")
        headers_or = build_api_headers(info_or)
        self.assertIn("Authorization", headers_or)
        self.assertEqual(headers_or["Authorization"], "Bearer sk-or-test12345")
        self.assertEqual(headers_or["HTTP-Referer"], "https://lexi.desktop")
        self.assertEqual(headers_or["X-Title"], "Lexi Screen Copilot")
        self.assertIn("Mozilla", headers_or["User-Agent"])

        # 2. DeepSeek
        cfg_ds = {"provider": "deepseek", "deepseek_api_key": "sk-ds-test"}
        info_ds = get_provider_info(cfg_ds)
        self.assertEqual(info_ds["provider"], "deepseek")
        self.assertEqual(info_ds["api_base"], "https://api.deepseek.com")
        self.assertEqual(info_ds["model"], "deepseek-flash")
        headers_ds = build_api_headers(info_ds)
        self.assertIn("Mozilla", headers_ds["User-Agent"])

        # 3. Groq
        cfg_groq = {"provider": "groq", "groq_api_key": "gsk_test"}
        info_groq = get_provider_info(cfg_groq)
        self.assertEqual(info_groq["provider"], "groq")
        self.assertEqual(info_groq["api_base"], "https://api.groq.com/openai/v1")
        self.assertEqual(info_groq["model"], "llama-3.3-70b-versatile")

    def test_media_status_caching(self):
        import time
        self.actions._cached_media = {"playing": True, "artist": "TestArtist", "track": "TestTrack"}
        self.actions._cached_media_time = time.time()
        media = self.actions.get_media_status()
        self.assertTrue(media.get("playing"))
        self.assertEqual(media.get("artist"), "TestArtist")

    def test_media_ignored_titles(self):
        ignored_samples = [
            "Administrator: X:\\windows\\system32\\cmd.exe - python.exe",
            "Windows PowerShell - test script",
            "Command Prompt - ping 8.8.8.8",
            "Visual Studio Code - lexi.py",
            "Task Manager"
        ]
        ignored_keywords = (
            "cmd.exe", "powershell", "terminal", "command prompt",
            "visual studio", "code", "notepad", "settings", "explorer",
            "task manager", "registry editor", "python", "gvncviewer",
            "qemu", "hiren", "administrator:", "bash", "git", "setup"
        )
        for s in ignored_samples:
            lower = s.lower()
            self.assertTrue(any(ig in lower for ig in ignored_keywords), f"Failed to match ignore rule for {s}")


if __name__ == "__main__":
    unittest.main()
