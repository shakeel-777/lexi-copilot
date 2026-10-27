import unittest
import os
import sys

# Ensure the parent directory is in sys.path so we can import lexi_capabilities
PROJECT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)

from lexi_capabilities import model_action_to_structured

class TestModelActionToStructured(unittest.TestCase):

    def test_preferred_form(self):
        # Valid JSON body
        act = {"type": "something", "attrs": {"name": "volume_set"}, "body": '{"percent": 40}'}
        res, err = model_action_to_structured(act)
        self.assertIsNone(err)
        self.assertEqual(res, ("volume_set", {"percent": 40}))

        # Attributes instead of body
        act2 = {"attrs": {"name": "brightness_set", "percent": 50}}
        res2, err2 = model_action_to_structured(act2)
        self.assertIsNone(err2)
        self.assertEqual(res2, ("brightness_set", {"percent": 50}))

        # Invalid JSON body
        act3 = {"attrs": {"name": "volume_set"}, "body": '{"percent": 40'}
        res3, err3 = model_action_to_structured(act3)
        self.assertIsNone(res3)
        self.assertIn("arguments were not valid JSON", err3)

        # Unregistered action
        act4 = {"attrs": {"name": "unknown_action"}}
        res4, err4 = model_action_to_structured(act4)
        self.assertIsNone(res4)
        self.assertIn("not a supported action", err4)

    def test_rejected_model_types(self):
        act = {"type": "key", "body": "ctrl+c"}
        res, err = model_action_to_structured(act)
        self.assertIsNone(res)
        self.assertIn("raw keyboard input from model output is not allowed", err)

        act2 = {"type": "type", "body": "hello"}
        res2, err2 = model_action_to_structured(act2)
        self.assertIsNone(res2)
        self.assertIn("raw typing from model output is not allowed", err2)

    def test_terminal_tasks(self):
        act = {"type": "command", "body": "ls -l"}
        res, err = model_action_to_structured(act)
        self.assertIsNone(err)
        self.assertEqual(res, ("terminal_task", {"command": "ls -l"}))

        act2 = {"type": "bash", "attrs": {"cmd": "echo hi"}}
        res2, err2 = model_action_to_structured(act2)
        self.assertIsNone(err2)
        self.assertEqual(res2, ("terminal_task", {"command": "echo hi"}))

        act3 = {"type": "shell"} # Empty command
        res3, err3 = model_action_to_structured(act3)
        self.assertIsNone(res3)
        self.assertIn("command was empty", err3)

    def test_volume_controls(self):
        act = {"type": "volume", "body": "mute"}
        res, err = model_action_to_structured(act)
        self.assertIsNone(err)
        self.assertEqual(res, ("volume_change", {"mode": "mute"}))

        act2 = {"type": "vol", "attrs": {"level": "+10"}}
        res2, err2 = model_action_to_structured(act2)
        self.assertIsNone(err2)
        self.assertEqual(res2, ("volume_change", {"mode": "up", "step": 10}))

        act3 = {"type": "audio", "body": "50"}
        res3, err3 = model_action_to_structured(act3)
        self.assertIsNone(err3)
        self.assertEqual(res3, ("volume_set", {"percent": 50}))

        act4 = {"type": "volume", "body": "invalid"}
        res4, err4 = model_action_to_structured(act4)
        self.assertIsNone(res4)
        self.assertIn("unrecognised volume level", err4)

    def test_brightness_controls(self):
        act = {"type": "brightness", "body": "75"}
        res, err = model_action_to_structured(act)
        self.assertIsNone(err)
        self.assertEqual(res, ("brightness_set", {"percent": 75}))

        act2 = {"type": "bright", "attrs": {"level": "-20"}}
        res2, err2 = model_action_to_structured(act2)
        self.assertIsNone(err2)
        self.assertEqual(res2, ("brightness_change", {"mode": "down", "step": 20}))

        act3 = {"type": "brightness", "body": "invalid"}
        res3, err3 = model_action_to_structured(act3)
        self.assertIsNone(res3)
        self.assertIn("unrecognised brightness level", err3)

    def test_lock_screen(self):
        act = {"type": "lock"}
        res, err = model_action_to_structured(act)
        self.assertIsNone(err)
        self.assertEqual(res, ("lock_screen", {}))

    def test_media_controls(self):
        act = {"type": "media", "body": "play-pause"}
        res, err = model_action_to_structured(act)
        self.assertIsNone(err)
        self.assertEqual(res, ("media_control", {"command": "toggle"}))

        act2 = {"type": "player", "attrs": {"command": "play my song"}}
        res2, err2 = model_action_to_structured(act2)
        self.assertIsNone(err2)
        self.assertEqual(res2, ("spotify_play", {"query": "my song"}))

        act3 = {"type": "media", "body": "skip"}
        res3, err3 = model_action_to_structured(act3)
        self.assertIsNone(err3)
        self.assertEqual(res3, ("media_control", {"command": "next"}))

    def test_browser_controls(self):
        act = {"type": "browser", "attrs": {"command": "youtube_play_fullscreen", "query": "cat videos"}}
        res, err = model_action_to_structured(act)
        self.assertIsNone(err)
        self.assertEqual(res, ("youtube_play", {"query": "cat videos", "fullscreen": "yes"}))

        act2 = {"type": "browser", "attrs": {"command": "unknown"}}
        res2, err2 = model_action_to_structured(act2)
        self.assertIsNone(res2)
        self.assertIn("unsupported browser command", err2)

    def test_open_controls(self):
        act = {"type": "open", "body": "spotify:search:my playlist"}
        res, err = model_action_to_structured(act)
        self.assertIsNone(err)
        self.assertEqual(res, ("spotify_play", {"query": "my playlist"}))

        act2 = {"type": "launch", "attrs": {"url": "https://www.google.com"}}
        res2, err2 = model_action_to_structured(act2)
        self.assertIsNone(err2)
        self.assertEqual(res2, ("open_site", {"site": "google.com"}))

        act3 = {"type": "run", "attrs": {"app": "calculator"}}
        res3, err3 = model_action_to_structured(act3)
        self.assertIsNone(err3)
        self.assertEqual(res3, ("open_app", {"name": "calculator"}))

        act4 = {"type": "open", "body": "/path/to/file"}
        res4, err4 = model_action_to_structured(act4)
        self.assertIsNone(res4)
        self.assertIn("opening files", err4)

    def test_whatsapp_drafting(self):
        act = {"type": "whatsapp", "attrs": {"contact": "John"}, "body": "Hello"}
        res, err = model_action_to_structured(act)
        self.assertIsNone(err)
        self.assertEqual(res, ("whatsapp_draft", {"contact": "John", "message": "Hello"}))

        act2 = {"type": "wa", "attrs": {"to": "Jane", "text": "Hi"}}
        res2, err2 = model_action_to_structured(act2)
        self.assertIsNone(err2)
        self.assertEqual(res2, ("whatsapp_draft", {"contact": "Jane", "message": "Hi"}))

        act3 = {"type": "whatsapp", "body": "Missing contact"}
        res3, err3 = model_action_to_structured(act3)
        self.assertIsNone(res3)
        self.assertIn("needs an exact contact name", err3)

    def test_rejected_mirror_actions(self):
        act = {"type": "mirror"}
        res, err = model_action_to_structured(act)
        self.assertIsNone(res)
        self.assertIn("phone mirroring is not available", err)

    def test_unsupported_actions(self):
        act = {"type": "unsupported_magic"}
        res, err = model_action_to_structured(act)
        self.assertIsNone(res)
        self.assertIn("unsupported action type", err)


if __name__ == "__main__":
    unittest.main()
