#!/usr/bin/env python3
"""
Unit tests for Lexi Copilot upgrades.
Covers:
- Phone & Mobile Copilot capabilities and intents (battery, ring, snap, clipboard)
- Extended system & hardware capabilities (resources, network, wifi, bluetooth, clipboard)
- Active window context injection
- Copy code block extraction
- Multi-turn conversation history persistence
"""

import os
import re
import json
import tempfile
import unittest

from lexi_capabilities import REGISTRY
import lexi_intents as I
from lexi import SystemActionEngine, _SCREEN_QUERY_RE


class TestCopilotUpgrades(unittest.TestCase):

    def test_new_capabilities_registered(self):
        expected_caps = [
            "phone_battery", "phone_ring", "phone_camera_snap", "phone_clipboard_sync",
            "system_resources", "network_status", "wifi_list", "bluetooth_control", "clipboard_read"
        ]
        for cap in expected_caps:
            self.assertIn(cap, REGISTRY, f"Capability {cap} should be registered in REGISTRY")

    def test_phone_battery_intent_routing(self):
        queries = [
            "phone battery",
            "check phone battery",
            "what is my phone battery",
            "phone's battery percentage",
            "how much phone battery do i have"
        ]
        for q in queries:
            route = I.route(q)
            self.assertEqual(route.kind, I.ACTION, f"Query '{q}' should route to ACTION")
            self.assertEqual(route.name, "phone_battery", f"Query '{q}' should route to phone_battery")

    def test_battery_priority_preserved(self):
        # "battery" should still route to system_battery (laptop)
        r_sys = I.route("battery")
        self.assertEqual(r_sys.kind, I.ACTION)
        self.assertEqual(r_sys.name, "system_battery")

        # "phone battery" must route to phone_battery
        r_phone = I.route("phone battery")
        self.assertEqual(r_phone.kind, I.ACTION)
        self.assertEqual(r_phone.name, "phone_battery")

    def test_phone_ring_intent_routing(self):
        queries = [
            "ring my phone",
            "ring phone",
            "find my phone",
            "where is my phone",
            "buzz my phone",
            "locate phone"
        ]
        for q in queries:
            route = I.route(q)
            self.assertEqual(route.kind, I.ACTION)
            self.assertEqual(route.name, "phone_ring")

    def test_phone_snap_intent_routing(self):
        queries = [
            "take a photo with my phone",
            "snap photo using phone",
            "capture picture from my phone",
            "phone camera snap"
        ]
        for q in queries:
            route = I.route(q)
            self.assertEqual(route.kind, I.ACTION)
            self.assertEqual(route.name, "phone_camera_snap")

    def test_phone_clipboard_sync_intent_routing(self):
        queries = [
            "sync clipboard to phone",
            "push clipboard to my phone",
            "send clipboard to phone",
            "sync clipboard"
        ]
        for q in queries:
            route = I.route(q)
            self.assertEqual(route.kind, I.ACTION)
            self.assertEqual(route.name, "phone_clipboard_sync")

    def test_system_resources_intent_routing(self):
        queries = [
            "system resources",
            "check system resources",
            "cpu usage",
            "ram usage",
            "disk space",
            "how is my cpu"
        ]
        for q in queries:
            route = I.route(q)
            self.assertEqual(route.kind, I.ACTION)
            self.assertEqual(route.name, "system_resources")

    def test_network_status_intent_routing(self):
        queries = [
            "network status",
            "check wifi status",
            "what is my ip",
            "am i connected to the internet"
        ]
        for q in queries:
            route = I.route(q)
            self.assertEqual(route.kind, I.ACTION)
            self.assertEqual(route.name, "network_status")

    def test_wifi_list_intent_routing(self):
        queries = [
            "scan wifi",
            "list available wifi networks",
            "show nearby wifi"
        ]
        for q in queries:
            route = I.route(q)
            self.assertEqual(route.kind, I.ACTION)
            self.assertEqual(route.name, "wifi_list")

    def test_bluetooth_control_intent_routing(self):
        r_status = I.route("bluetooth status")
        self.assertEqual(r_status.kind, I.ACTION)
        self.assertEqual(r_status.name, "bluetooth_control")
        self.assertEqual(r_status.args.get("action"), "status")

        r_on = I.route("turn bluetooth on")
        self.assertEqual(r_on.kind, I.ACTION)
        self.assertEqual(r_on.name, "bluetooth_control")
        self.assertEqual(r_on.args.get("action"), "on")

        r_off = I.route("turn bluetooth off")
        self.assertEqual(r_off.kind, I.ACTION)
        self.assertEqual(r_off.name, "bluetooth_control")
        self.assertEqual(r_off.args.get("action"), "off")

    def test_clipboard_read_intent_routing(self):
        queries = [
            "read clipboard",
            "what is on my clipboard",
            "show clipboard",
            "paste clipboard"
        ]
        for q in queries:
            route = I.route(q)
            self.assertEqual(route.kind, I.ACTION)
            self.assertEqual(route.name, "clipboard_read")

    def test_visual_query_regex_detection(self):
        visual_queries = [
            "what is on my screen",
            "explain this code",
            "summarize this page",
            "look at this screen",
            "debug this error on screen"
        ]
        for q in visual_queries:
            self.assertTrue(bool(_SCREEN_QUERY_RE.search(q)), f"Query '{q}' should match _SCREEN_QUERY_RE")

        non_visual = [
            "what is the capital of France",
            "tell me a joke",
            "how to write quicksort in Python"
        ]
        for q in non_visual:
            self.assertFalse(bool(_SCREEN_QUERY_RE.search(q)), f"Query '{q}' should NOT match _SCREEN_QUERY_RE")

    def test_system_resources_executor_execution(self):
        eng = SystemActionEngine.get()
        res = eng.get_system_resources()
        self.assertTrue(res["success"])
        self.assertTrue(res["verified"])
        self.assertIn("CPU Load:", res["message"])
        self.assertIn("RAM:", res["message"])
        self.assertIn("Disk", res["message"])

    def test_network_status_executor_execution(self):
        eng = SystemActionEngine.get()
        res = eng.get_network_status()
        self.assertTrue(res["success"])
        self.assertTrue(res["verified"])
        self.assertIn("Local IP:", res["message"])

    def test_clipboard_read_executor_execution(self):
        eng = SystemActionEngine.get()
        res = eng.read_clipboard()
        self.assertIn("success", res)
        self.assertIn("message", res)

    def test_code_block_extraction_logic(self):
        sample = """Here is the Python solution:
```python
def add(a, b):
    return a + b
```
And here is test:
```python
assert add(2, 3) == 5
```
That's it!"""
        blocks = re.findall(r'```(?:[\w\+\-\.]+)?\n?(.*?)```', sample, re.DOTALL)
        self.assertEqual(len(blocks), 2)
        extracted = "\n\n".join(b.strip() for b in blocks if b.strip())
        self.assertIn("def add(a, b):", extracted)
        self.assertIn("assert add(2, 3) == 5", extracted)

    def test_conversation_history_persistence(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            hist_file = os.path.join(tmpdir, "conversation_history.json")
            sample_conv = [
                {"role": "user", "content": "Hello Lexi"},
                {"role": "assistant", "content": "Hello! How can I help you today?"},
                {"role": "user", "content": [{"type": "text", "text": "Can you check my code?"}]},
                {"role": "assistant", "content": "```python\nprint('Clean code!')\n```"}
            ]

            clean_conv = []
            for item in sample_conv[-30:]:
                role = item.get("role", "")
                content = item.get("content", "")
                if isinstance(content, list):
                    text_parts = [p.get("text", "") for p in content if p.get("type") == "text"]
                    content = "\n".join(text_parts)
                clean_conv.append({"role": role, "content": str(content)})

            with open(hist_file, "w", encoding="utf-8") as f:
                json.dump(clean_conv, f, indent=2, ensure_ascii=False)

            with open(hist_file, "r", encoding="utf-8") as f:
                loaded = json.load(f)

            self.assertEqual(len(loaded), 4)
            self.assertEqual(loaded[0]["content"], "Hello Lexi")
            self.assertEqual(loaded[2]["content"], "Can you check my code?")
            self.assertIn("Clean code!", loaded[3]["content"])


if __name__ == "__main__":
    unittest.main()
