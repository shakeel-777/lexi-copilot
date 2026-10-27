import unittest
import os
import sys

PROJECT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)

import lexi_memory as M

class TestLexiMemoryPureFunctions(unittest.TestCase):
    def test_get_creator_name(self):
        name = M.get_creator_name()
        self.assertEqual(name, "Shakeel")

    def test_get_creator_statement(self):
        statement = M.get_creator_statement()
        self.assertIsInstance(statement, str)

    def test_message_text_string(self):
        msg = {"content": "hello world"}
        self.assertEqual(M.message_text(msg), "hello world")

        msg2 = {"content": None}
        self.assertEqual(M.message_text(msg2), "")

    def test_message_text_list(self):
        msg = {
            "content": [
                {"type": "text", "text": "part 1"},
                {"type": "text", "text": "part 2"},
                {"type": "image_url", "url": "..."}
            ]
        }
        text = M.message_text(msg)
        self.assertIn("part 1\npart 2", text)
        self.assertIn("[1 attached image(s) omitted]", text)

    def test_squash(self):
        self.assertEqual(M._squash("hello   world", 50), "hello world")
        self.assertEqual(M._squash("a" * 100, 10), "a" * 9 + "…")

        long_blob = "A" * 200
        self.assertEqual(M._squash(f"test {long_blob} end", 50), "test [blob] end")
        self.assertEqual(M._squash("data:image/png;base64,123", 50), "[image]")

if __name__ == '__main__':
    unittest.main()
