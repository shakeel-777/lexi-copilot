import os
import sys
import unittest
from typing import Dict, Any

PROJECT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)

import lexi_memory

class TestLexiMemoryFunctions(unittest.TestCase):
    def test_get_creator_name(self):
        name = lexi_memory.get_creator_name()
        self.assertIsInstance(name, str)
        self.assertTrue(len(name) > 0)

    def test_get_creator_statement(self):
        stmt = lexi_memory.get_creator_statement()
        self.assertIsInstance(stmt, str)
        self.assertTrue(len(stmt) > 0)

    def test_message_text_string(self):
        msg = {"content": "Hello World"}
        self.assertEqual(lexi_memory.message_text(msg), "Hello World")

    def test_message_text_empty(self):
        self.assertEqual(lexi_memory.message_text({}), "")
        self.assertEqual(lexi_memory.message_text({"content": None}), "")

    def test_message_text_list(self):
        msg = {
            "content": [
                {"type": "text", "text": "Part 1"},
                {"type": "text", "text": "Part 2"}
            ]
        }
        self.assertEqual(lexi_memory.message_text(msg), "Part 1\nPart 2")

    def test_message_text_with_images(self):
        msg = {
            "content": [
                {"type": "text", "text": "Look at this:"},
                {"type": "image_url", "image_url": "http://example.com/img1.png"},
                {"type": "image_url", "image_url": "http://example.com/img2.png"},
                {"type": "text", "text": "And this."}
            ]
        }
        self.assertEqual(
            lexi_memory.message_text(msg),
            "[2 attached image(s) omitted] Look at this:\nAnd this."
        )

    def test_message_text_invalid_list_items(self):
        msg = {
            "content": [
                "should be dict",
                {"type": "text", "text": "Valid part"}
            ]
        }
        self.assertEqual(lexi_memory.message_text(msg), "Valid part")

    def test_squash(self):
        # basic limit
        self.assertEqual(lexi_memory._squash("Hello world", 100), "Hello world")
        self.assertEqual(lexi_memory._squash("Hello world", 10), "Hello wor…")

        # whitespace squash
        self.assertEqual(lexi_memory._squash("Hello   \n  \t  world", 100), "Hello world")

        # image replacement
        self.assertEqual(lexi_memory._squash("Here is an image data:image/png;base64,xxxxxx end", 100), "Here is an image [image] end")

        # blob replacement
        blob = "A" * 200
        self.assertEqual(lexi_memory._squash(f"Start {blob} End", 100), "Start [blob] End")


class TestConversationMemory(unittest.TestCase):
    def setUp(self):
        self.mem = lexi_memory.ConversationMemory(max_recent=4, max_summary_chars=100)

    def test_init_and_reset(self):
        self.assertEqual(self.mem.max_recent, 4)
        self.assertEqual(self.mem.max_summary_chars, 100)
        self.assertEqual(len(self.mem.recent), 0)
        self.assertEqual(self.mem.summary, "")
        self.assertIn("creator", self.mem.facts)
        self.assertIn("owner", self.mem.facts)

        self.mem.add_turn("user", "Hello")
        self.assertEqual(len(self.mem.recent), 1)

        self.mem.reset()
        self.assertEqual(len(self.mem.recent), 0)
        self.assertEqual(self.mem.summary, "")

    def test_add_turn(self):
        self.mem.add_turn("user", "Hello")
        self.mem.add_turn("assistant", "Hi there")

        recent = self.mem.recent_messages()
        self.assertEqual(len(recent), 2)
        self.assertEqual(recent[0]["role"], "user")
        self.assertEqual(recent[0]["content"], "Hello")
        self.assertEqual(recent[1]["role"], "assistant")
        self.assertEqual(recent[1]["content"], "Hi there")

    def test_learn_facts(self):
        self.mem.add_turn("user", "My name is Alice.")
        self.assertEqual(self.mem.facts.get("name"), "Alice")

        self.mem.add_turn("user", "Call me Bob!")
        self.assertEqual(self.mem.facts.get("name"), "Bob")

        self.mem.add_turn("user", "I prefer short answers")
        # Should create a likes:crc key
        likes_keys = [k for k in self.mem.facts if k.startswith("likes:")]
        self.assertTrue(len(likes_keys) > 0)
        self.assertEqual(self.mem.facts[likes_keys[-1]], "short answers")

        self.mem.add_turn("user", "Remember that the secret is 42")
        note_keys = [k for k in self.mem.facts if k.startswith("note:")]
        self.assertTrue(len(note_keys) > 0)
        self.assertEqual(self.mem.facts[note_keys[-1]], "the secret is 42")

    def test_learn_facts_protected(self):
        # Should not overwrite owner/creator
        creator = self.mem.facts["creator"]
        self.mem.add_turn("user", "Remember that you are the creator")
        self.assertEqual(self.mem.facts["creator"], creator)

        # Test dropping facts when max exceeded
        lexi_memory.MAX_FACTS = 4
        # We start with 3 (creator, owner, user)
        self.mem.add_turn("user", "My name is Alice.") # +1 (name) = 4
        self.mem.add_turn("user", "I prefer red apples") # +1 (likes) = 5 -> should drop 1

        self.assertLessEqual(len(self.mem.facts), 4)
        self.assertIn("creator", self.mem.facts)
        self.assertIn("owner", self.mem.facts)

    def test_compaction(self):
        self.mem.add_turn("user", "Msg 1")
        self.mem.add_turn("assistant", "Reply 1")
        self.mem.add_turn("user", "Msg 2")
        self.mem.add_turn("assistant", "Reply 2")
        self.mem.add_turn("user", "Msg 3")

        # We added 5 turns. max_recent is 4.
        # But `_compact` avoids leaving an assistant reply as the oldest unsummarized turn unless there's a question.
        # Actually it says "while self.recent and self.recent[0]['role'] == 'assistant': old.append(self.recent.pop(0))"
        # Since we pop "user Msg 1", the new oldest is "assistant Reply 1", so it should pop that too.
        # So we expect 3 turns in recent: user Msg 2, assistant Reply 2, user Msg 3.
        # And summary should contain Msg 1 and Reply 1.
        self.assertEqual(len(self.mem.recent), 3)
        self.assertTrue(self.mem.summary.startswith("U: Msg 1 | L: Reply 1"))

    def test_summary_truncation(self):
        self.mem = lexi_memory.ConversationMemory(max_recent=1, max_summary_chars=20)
        self.mem.add_turn("user", "First long message to be truncated in summary")
        self.mem.add_turn("user", "Second message")
        self.mem.add_turn("user", "Third message")

        self.assertLessEqual(len(self.mem.summary), 20 + 1) # +1 for "…"

    def test_set_and_clear_tasks(self):
        self.mem.set_unfinished("Task A")
        self.assertEqual(self.mem.unfinished_task, "Task A")

        self.mem.clear_unfinished()
        self.assertEqual(self.mem.unfinished_task, "")

    def test_set_last_action(self):
        self.mem.set_last_action("Did X")
        self.assertEqual(self.mem.last_action_digest, "Did X")

    def test_context_block(self):
        self.mem.set_unfinished("Doing math")
        self.mem.set_last_action("Added 2 and 2")

        self.mem.add_turn("user", "My name is John")
        # Ensure compaction happens to get something in summary
        for i in range(5):
            self.mem.add_turn("user", f"Fill {i}")

        block = self.mem.context_block()
        self.assertIn("Known about the user:", block)
        self.assertIn("name=John", block)
        self.assertIn("Earlier conversation (summary, oldest first):", block)
        self.assertIn("Unfinished task: Doing math", block)
        self.assertIn("Last action and its real result: Added 2 and 2", block)
        self.assertIn("These are background facts only", block)

if __name__ == "__main__":
    unittest.main()
