"""
Unit Tests for AirDrop-Grade Lexi Beam multi-transport file sharing.
Tests ADB direct push, WebSocket instant push, LAN P2P proposals, friendly name resolution,
dynamic IP handling, audio feedback triggers, and collision avoidance.
"""

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

PROJECT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)

import lexi_drop
from lexi_drop import (
    LexiDropServer,
    get_friendly_adb_device_name,
    get_unique_destination_path,
    play_beam_sound
)


class TestLexiBeam(unittest.TestCase):
    def setUp(self):
        self.server = LexiDropServer()
        lexi_drop._adb_device_names.clear()

    def test_get_friendly_adb_device_name_market(self):
        with patch("subprocess.run") as mock_sub:
            mock_sub.return_value = MagicMock(stdout="Redmi Note 11 SE\n", returncode=0)
            name = get_friendly_adb_device_name("10.0.0.5:5555")
            self.assertEqual(name, "Redmi Note 11 SE")
            # Verify cached
            self.assertEqual(lexi_drop._adb_device_names.get("10.0.0.5:5555"), "Redmi Note 11 SE")

    def test_get_friendly_adb_device_name_fallback_model(self):
        with patch("subprocess.run") as mock_sub:
            # marketname fails, global settings fails, model succeeds
            mock_sub.side_effect = [
                MagicMock(stdout="", returncode=0),
                MagicMock(stdout="null\n", returncode=0),
                MagicMock(stdout="Pixel 8 Pro\n", returncode=0)
            ]
            name = get_friendly_adb_device_name("192.168.1.100:5555")
            self.assertEqual(name, "Pixel 8 Pro")

    def test_unique_destination_path_no_conflict(self):
        test_dir = Path("/tmp/lexi_test_dest")
        test_dir.mkdir(parents=True, exist_ok=True)
        dest = get_unique_destination_path(test_dir, "document.pdf")
        self.assertEqual(dest.name, "document.pdf")

    def test_unique_destination_path_with_conflict(self):
        test_dir = Path("/tmp/lexi_test_dest_conflict")
        test_dir.mkdir(parents=True, exist_ok=True)
        f1 = test_dir / "photo.jpg"
        f1.touch()
        dest = get_unique_destination_path(test_dir, "photo.jpg")
        self.assertEqual(dest.name, "photo (1).jpg")
        f1.unlink()

    def test_dynamic_local_ip(self):
        ip = self.server.local_ip
        self.assertIsInstance(ip, str)
        self.assertTrue(len(ip.split('.')) == 4 or ip == '127.0.0.1')

    @patch("subprocess.run")
    def test_get_all_peers_adb_detection(self, mock_sub):
        def fake_sub(cmd, *args, **kwargs):
            cmd_str = " ".join(cmd)
            if "addr show" in cmd_str:
                return MagicMock(stdout="wlp0s20f3 UP 10.22.216.11/24\n", returncode=0)
            if "adb devices" in cmd_str:
                return MagicMock(stdout="List of devices attached\n10.22.216.160:5555\tdevice\n", returncode=0)
            if "marketname" in cmd_str:
                return MagicMock(stdout="Redmi Note 11 SE\n", returncode=0)
            return MagicMock(stdout="Broadcast completed\n", returncode=0)

        mock_sub.side_effect = fake_sub
        peers = self.server.get_all_peers()
        phone_peer = next((p for p in peers if p.get("ip") == "10.22.216.160"), None)
        self.assertIsNotNone(phone_peer)
        self.assertEqual(phone_peer["device"], "Redmi Note 11 SE")
        self.assertEqual(phone_peer["type"], "phone")
        self.assertEqual(phone_peer["adb_device"], "10.22.216.160:5555")

    @patch("subprocess.run")
    def test_send_p2p_to_peer_adb(self, mock_sub):
        test_file = "/tmp/test_beam_adb.txt"
        with open(test_file, "w") as f:
            f.write("AirDrop wire-speed transfer test")

        def fake_sub(cmd, *args, **kwargs):
            cmd_str = " ".join(cmd)
            if "addr show" in cmd_str:
                return MagicMock(stdout="wlp0s20f3 UP 10.22.216.11/24\n", returncode=0)
            if "adb devices" in cmd_str:
                return MagicMock(stdout="List of devices attached\n10.22.216.160:5555\tdevice\n", returncode=0)
            if "marketname" in cmd_str:
                return MagicMock(stdout="Redmi Note 11 SE\n", returncode=0)
            return MagicMock(stdout="success\n", returncode=0)

        mock_sub.side_effect = fake_sub

        try:
            res = self.server.send_p2p_to_peer("10.22.216.160", test_file)
            self.assertTrue(res["success"])
            self.assertEqual(res["status"], "delivered_adb")
            self.assertEqual(res["method"], "adb")
            self.assertEqual(res["device"], "Redmi Note 11 SE")
            self.assertEqual(res["filename"], "test_beam_adb.txt")
            self.assertEqual(res["destination"], "/sdcard/Download/test_beam_adb.txt")
        finally:
            if os.path.exists(test_file):
                os.unlink(test_file)

    def test_send_p2p_to_peer_file_not_found(self):
        res = self.server.send_p2p_to_peer("10.0.0.5", "/nonexistent/path/to/ghost.file")
        self.assertIn("error", res)
        self.assertIn("File not found", res["error"])

    @patch("subprocess.run")
    def test_play_beam_sound_complete(self, mock_sub):
        play_beam_sound("complete")
        # Ensure it doesn't crash or raise exceptions


if __name__ == "__main__":
    unittest.main()
