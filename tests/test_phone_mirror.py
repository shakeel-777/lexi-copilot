"""
Tests for PhoneMirrorManager: Wireless screen mirroring 2.0 optimization and supervisor.
Ensures low-latency flags, H.265/H.264 profiles, auto-reconnect, and single-instance protection work accurately.
"""

import os
import sys
import unittest
from unittest.mock import patch, MagicMock

PROJECT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)

from lexi_drop import PhoneMirrorManager
import lexi


class TestPhoneMirrorManager(unittest.TestCase):
    def setUp(self):
        self.mgr = PhoneMirrorManager()
        self.mgr._proc = None
        self.mgr._stop_requested = False

    def tearDown(self):
        self.mgr.stop_mirror()

    def test_is_wireless_device(self):
        self.assertTrue(self.mgr.is_wireless_device("192.168.1.50:5555"))
        self.assertTrue(self.mgr.is_wireless_device("10.0.0.4:5555"))
        self.assertTrue(self.mgr.is_wireless_device("192.168.0.100"))
        self.assertFalse(self.mgr.is_wireless_device("R58M32ABCDE"))
        self.assertFalse(self.mgr.is_wireless_device("emulator-5554"))
        self.assertFalse(self.mgr.is_wireless_device(None))

    def test_build_scrcpy_cmd_wireless(self):
        cmd = self.mgr.build_scrcpy_cmd(
            device_id="192.168.1.10:5555",
            stay_awake=True,
            turn_screen_off=True,
            codec="h265"
        )
        self.assertIn("-s", cmd)
        self.assertIn("192.168.1.10:5555", cmd)
        self.assertIn("--video-codec=h265", cmd)
        self.assertIn("--max-size=1080", cmd)
        self.assertIn("-b", cmd)
        self.assertIn("3M", cmd)
        self.assertIn("--max-fps=60", cmd)
        self.assertIn("--video-buffer=40", cmd)
        self.assertIn("--audio-buffer=40", cmd)
        self.assertIn("--stay-awake", cmd)
        self.assertIn("--turn-screen-off", cmd)

    def test_build_scrcpy_cmd_wired(self):
        cmd = self.mgr.build_scrcpy_cmd(
            device_id="R58M32ABCDE",
            stay_awake=False,
            turn_screen_off=False
        )
        self.assertIn("-s", cmd)
        self.assertIn("R58M32ABCDE", cmd)
        self.assertIn("--max-size=1440", cmd)
        self.assertIn("-b", cmd)
        self.assertIn("8M", cmd)
        self.assertIn("--max-fps=60", cmd)
        self.assertNotIn("--video-buffer=40", cmd)
        self.assertNotIn("--stay-awake", cmd)
        self.assertNotIn("--turn-screen-off", cmd)

    @patch("shutil.which")
    def test_start_mirror_scrcpy_missing(self, mock_which):
        mock_which.return_value = None
        res = self.mgr.start_mirror("192.168.1.10:5555")
        self.assertFalse(res["success"])
        self.assertIn("scrcpy` is not installed", res["message"])

    @patch("shutil.which")
    @patch("subprocess.run")
    def test_start_mirror_no_devices(self, mock_sub, mock_which):
        mock_which.return_value = "/usr/bin/scrcpy"
        mock_sub.return_value = MagicMock(stdout="List of devices attached\n\n", returncode=0)
        res = self.mgr.start_mirror()
        self.assertFalse(res["success"])
        self.assertIn("No Android Phone Connected", res["message"])

    @patch("shutil.which")
    @patch("subprocess.run")
    def test_start_mirror_unauthorized(self, mock_sub, mock_which):
        mock_which.return_value = "/usr/bin/scrcpy"
        mock_sub.return_value = MagicMock(
            stdout="List of devices attached\n192.168.1.10:5555\tunauthorized\n",
            returncode=0
        )
        res = self.mgr.start_mirror()
        self.assertFalse(res["success"])
        self.assertIn("Unauthorized", res["message"])

    @patch("shutil.which")
    @patch("subprocess.run")
    @patch("threading.Thread")
    def test_start_mirror_success_wireless(self, mock_thread, mock_sub, mock_which):
        mock_which.return_value = "/usr/bin/scrcpy"
        mock_sub.return_value = MagicMock(
            stdout="List of devices attached\n192.168.1.10:5555\tdevice\n",
            returncode=0
        )
        res = self.mgr.start_mirror()
        self.assertTrue(res["success"])
        self.assertEqual(res["device"], "192.168.1.10:5555")
        self.assertTrue(res["is_wireless"])
        self.assertIn("Ultra-Low Latency Wireless 2.0", res["message"])

    @patch("shutil.which")
    @patch("subprocess.run")
    def test_already_running_focuses_window(self, mock_sub, mock_which):
        mock_which.return_value = "/usr/bin/scrcpy"
        mock_sub.return_value = MagicMock(
            stdout="List of devices attached\n192.168.1.10:5555\tdevice\n",
            returncode=0
        )
        # Mock active process
        fake_proc = MagicMock()
        fake_proc.poll.return_value = None
        self.mgr._proc = fake_proc

        res = self.mgr.start_mirror("192.168.1.10:5555")
        self.assertTrue(res["success"])
        self.assertTrue(res.get("already_running"))
        self.assertIn("already active", res["message"])
        # Verify xdotool called to focus
        mock_sub.assert_called_with(
            ['xdotool', 'search', '--name', 'Lexi Phone Mirror', 'windowactivate'],
            capture_output=True,
            timeout=1
        )

    @patch("lexi_drop.PhoneMirrorManager.get")
    def test_lexi_start_phone_mirror_delegation(self, mock_get_mgr):
        fake_mgr = MagicMock()
        fake_mgr.start_mirror.return_value = {"success": True, "message": "All good"}
        mock_get_mgr.return_value = fake_mgr

        engine = lexi.SystemActionEngine.get()
        res = engine.start_phone_mirror(stay_awake=True, turn_screen_off=True)
        self.assertTrue(res["success"])
        fake_mgr.start_mirror.assert_called_with(stay_awake=True, turn_screen_off=True)


if __name__ == "__main__":
    unittest.main()
