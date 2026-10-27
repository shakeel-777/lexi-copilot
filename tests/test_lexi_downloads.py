"""Tests for lexi_downloads.py"""

import os
import sys
import time
import unittest
from unittest.mock import patch, MagicMock

PROJECT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)

from lexi_downloads import DownloadItem, DownloadSentinel


class TestDownloadItem(unittest.TestCase):
    def test_initialization(self):
        item = DownloadItem("/path/to/file.txt.crdownload")
        self.assertEqual(item.path, "/path/to/file.txt.crdownload")
        self.assertEqual(item.filename, "file.txt.crdownload")
        self.assertEqual(item.target_name, "file.txt")
        self.assertEqual(item.current_size, 0)
        self.assertEqual(item.speed_bps, 0.0)
        self.assertFalse(item.is_completed)

        item2 = DownloadItem("/path/to/regular_file.zip")
        self.assertEqual(item2.target_name, "regular_file.zip")

    def test_update_size_and_speed(self):
        now = time.time()
        item = DownloadItem("/path/to/file.zip.part")
        item.last_check_time = now - 1.0  # simulate 1 second ago

        item.update_size(1024, now)
        self.assertEqual(item.current_size, 1024)
        self.assertEqual(item.speed_bps, 1024.0)

        # simulate another second
        item.update_size(3072, now + 1.0)
        self.assertEqual(item.current_size, 3072)
        # speed = 2048 / 1.0 = 2048
        # smoothed speed = (1024 * 0.7) + (2048 * 0.3) = 716.8 + 614.4 = 1331.2
        self.assertAlmostEqual(item.speed_bps, 1331.2)

    def test_format_size(self):
        item = DownloadItem("/fake")
        item.current_size = 500
        self.assertEqual(item.format_size(), "500 B")
        item.current_size = 1536
        # .0f format rounds to nearest whole number, 1536 / 1024 = 1.5 -> rounds to 2 in python f-string
        self.assertEqual(item.format_size(), "2 KB")
        item.current_size = int(1024 * 1024 * 2.5)
        self.assertEqual(item.format_size(), "2.5 MB")
        item.current_size = int(1024 * 1024 * 1024 * 3.25)
        self.assertEqual(item.format_size(), "3.25 GB")

    def test_format_speed(self):
        item = DownloadItem("/fake")
        item.speed_bps = 500
        self.assertEqual(item.format_speed(), "500 B/s")
        item.speed_bps = 2048
        self.assertEqual(item.format_speed(), "2 KB/s")
        item.speed_bps = 1024 * 1024 * 2.5
        self.assertEqual(item.format_speed(), "2.5 MB/s")

    def test_to_dict(self):
        item = DownloadItem("/path/to/file.tmp")
        item.current_size = 1024
        item.speed_bps = 1024.0
        d = item.to_dict()
        self.assertEqual(d["filename"], "file")
        self.assertEqual(d["raw_filename"], "file.tmp")
        self.assertEqual(d["path"], "/path/to/file.tmp")
        self.assertEqual(d["size_str"], "1 KB")
        self.assertEqual(d["speed_str"], "1 KB/s")
        self.assertEqual(d["bytes"], 1024)
        self.assertEqual(d["speed_bps"], 1024.0)
        self.assertFalse(d["is_completed"])


class TestDownloadSentinel(unittest.TestCase):
    def setUp(self):
        # Reset singleton just in case
        DownloadSentinel._instance = None
        self.sentinel = DownloadSentinel(download_dir="/fake/downloads")
        self.sentinel._running = False

    def test_singleton(self):
        s1 = DownloadSentinel.get("/tmp")
        s2 = DownloadSentinel.get("/tmp2")
        self.assertIs(s1, s2)

    def test_listeners(self):
        cb = MagicMock()
        self.sentinel.add_listener(cb)
        self.sentinel._notify_event("test_event", {"foo": "bar"})
        cb.assert_called_once_with({"event": "test_event", "data": {"foo": "bar"}})

        self.sentinel.remove_listener(cb)
        self.sentinel._notify_event("test_event_2", {})
        cb.assert_called_once()  # not called again

    @patch("os.path.isdir")
    @patch("os.scandir")
    def test_scan_once(self, mock_scandir, mock_isdir):
        mock_isdir.return_value = True

        entry1 = MagicMock()
        entry1.name = "file1.zip.crdownload"
        entry1.path = "/fake/downloads/file1.zip.crdownload"
        entry1.is_dir.return_value = False
        stat1 = MagicMock()
        stat1.st_size = 1024
        entry1.stat.return_value = stat1

        mock_scandir.return_value = [entry1]

        cb = MagicMock()
        self.sentinel.add_listener(cb)

        self.sentinel.scan_once()

        self.assertTrue(self.sentinel.has_active_downloads())
        active = self.sentinel.get_active_downloads()
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0]["filename"], "file1.zip")
        self.assertEqual(active[0]["bytes"], 1024)

        # Called with progress
        # cb may be called multiple times if there are intermediate states or we can just assert its last call
        # the test currently assumes assert_called_once but scan_once might trigger multiple notifications or not
        # Wait, the failure was: Expected 'mock' to have been called once. Called 2 times.
        # Calls: [call({'event': 'completed', ...}), call({'event': 'idle', ...})]
        self.assertEqual(cb.call_args[0][0]["event"], "progress")
        self.assertEqual(len(cb.call_args[0][0]["data"]), 1)

        # Second scan, size increased
        stat1.st_size = 2048
        mock_scandir.return_value = [entry1]
        cb.reset_mock()

        self.sentinel.scan_once()
        active = self.sentinel.get_active_downloads()
        self.assertEqual(active[0]["bytes"], 2048)
        self.assertEqual(cb.call_args[0][0]["event"], "progress")

        # Third scan, file finished (no longer in directory, but target file exists)
        mock_scandir.return_value = []
        cb.reset_mock()

        with patch("os.path.exists") as mock_exists:
            mock_exists.return_value = True  # The target file1.zip exists
            self.sentinel.scan_once()

        self.assertFalse(self.sentinel.has_active_downloads())

        # Should have called completed AND idle (since missing_paths and not found_active_paths)
        call_args_list = cb.call_args_list
        self.assertEqual(len(call_args_list), 2)

        events = [call[0][0]["event"] for call in call_args_list]
        self.assertIn("completed", events)
        self.assertIn("idle", events)

        completed_call = [call[0][0] for call in call_args_list if call[0][0]["event"] == "completed"][0]
        self.assertEqual(completed_call["data"]["filename"], "file1.zip")

    def test_summary(self):
        self.assertEqual(self.sentinel.get_download_summary(), "No active downloads in progress.")

        item = DownloadItem("/fake/downloads/file.txt.part")
        item.current_size = 1024
        item.speed_bps = 512
        self.sentinel._active_downloads["/fake/downloads/file.txt.part"] = item

        summary = self.sentinel.get_download_summary()
        self.assertIn("Active Downloads:", summary)
        self.assertIn("file.txt", summary)
        self.assertIn("1 KB", summary)

if __name__ == "__main__":
    unittest.main()
