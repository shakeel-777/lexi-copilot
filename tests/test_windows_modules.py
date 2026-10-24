#!/usr/bin/env python3
import sys
import unittest
from pathlib import Path

# Add windows dir to path
sys.path.insert(0, str(Path(__file__).parent.parent / "windows"))

import lexi_win_actions
import lexi_win_drop


class TestWindowsSuite(unittest.TestCase):
    def test_win_actions_instantiation(self):
        eng = lexi_win_actions.SystemActionEngineWindows.get()
        self.assertIsNotNone(eng)

    def test_win_drop_server_instantiation(self):
        srv = lexi_win_drop.LexiWinDropServer(port=17899)
        self.assertIsNotNone(srv)
        self.assertEqual(srv.port, 17899)
        self.assertIn("B", lexi_win_drop.format_size(1024))
        self.assertEqual(lexi_win_drop.format_size(1024 * 1024), "1.0 MB")


if __name__ == '__main__':
    unittest.main()
