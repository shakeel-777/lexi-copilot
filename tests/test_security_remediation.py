import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

import lexi_drop
from lexi_drop import get_unique_destination_path, is_safe_share_path
from lexi_productivity import ProcessManager
import lexi


class TestSecurityRemediation(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.test_dir = tempfile.TemporaryDirectory()
        self.dir_path = Path(self.test_dir.name)

    def tearDown(self):
        self.test_dir.cleanup()

    def test_path_traversal_prevention(self):
        # 1. Classical parent directory traversal
        res1 = get_unique_destination_path(self.dir_path, "../../.bashrc")
        self.assertTrue(res1.resolve().is_relative_to(self.dir_path.resolve()))
        self.assertNotIn("..", str(res1))
        self.assertFalse(res1.name.startswith("."))

        # 2. Absolute path traversal
        res2 = get_unique_destination_path(self.dir_path, "/etc/shadow")
        self.assertTrue(res2.resolve().is_relative_to(self.dir_path.resolve()))
        self.assertEqual(res2.parent.resolve(), self.dir_path.resolve())

        # 3. Windows-style backslash traversal
        res3 = get_unique_destination_path(self.dir_path, "..\\..\\windows\\system32\\calc.exe")
        self.assertTrue(res3.resolve().is_relative_to(self.dir_path.resolve()))
        self.assertEqual(res3.name, "calc.exe")

        # 4. Null bytes / control characters injection
        res4 = get_unique_destination_path(self.dir_path, "payload\x00.exe")
        self.assertTrue(res4.resolve().is_relative_to(self.dir_path.resolve()))
        self.assertNotIn("\x00", str(res4))

        # 5. Empty or purely dot strings
        res5 = get_unique_destination_path(self.dir_path, "...")
        self.assertTrue(res5.resolve().is_relative_to(self.dir_path.resolve()))
        self.assertTrue(res5.name.endswith(".bin"))

        # 6. Incremental unique counter on collision
        dummy_file = self.dir_path / "document.pdf"
        dummy_file.write_text("existing")
        res6 = get_unique_destination_path(self.dir_path, "document.pdf")
        self.assertEqual(res6.name, "document (1).pdf")
        self.assertTrue(res6.resolve().is_relative_to(self.dir_path.resolve()))

    def test_safe_share_path_validation(self):
        home = os.path.expanduser("~")

        # Dangerous system paths
        safe, reason = is_safe_share_path("/etc/passwd")
        self.assertFalse(safe)
        self.assertIn("forbidden", reason.lower())

        safe, _ = is_safe_share_path("/root/secrets.txt")
        self.assertFalse(safe)

        # Sensitive user directories
        safe, reason = is_safe_share_path(os.path.join(home, ".ssh", "id_rsa"))
        self.assertFalse(safe)
        self.assertIn("sensitive", reason.lower())

        safe, _ = is_safe_share_path(os.path.join(home, ".gnupg", "secring.gpg"))
        self.assertFalse(safe)

        safe, _ = is_safe_share_path(os.path.join(home, ".local", "share", "mochi-copilot", "config.json"))
        self.assertFalse(safe)

        safe, _ = is_safe_share_path(os.path.join(home, ".bashrc"))
        self.assertFalse(safe)

        # Safe normal user files
        safe, _ = is_safe_share_path(os.path.join(home, "Downloads", "vacation.jpg"))
        self.assertTrue(safe)

    def test_process_manager_kill_hardening(self):
        pm = ProcessManager.get()

        # Argument injection flags
        res1 = pm.kill_process("-u root")
        self.assertFalse(res1["success"])
        self.assertIn("flag", res1["message"].lower())

        res2 = pm.kill_process("--help")
        self.assertFalse(res2["success"])

        # Shell metacharacters / separators
        res3 = pm.kill_process("sleep 10; rm -rf /")
        self.assertFalse(res3["success"])
        self.assertIn("format", res3["message"].lower())

        # Protected core system processes
        for proc in ["systemd", "init", "bash", "python3", "lexi"]:
            res = pm.kill_process(proc)
            self.assertFalse(res["success"])
            self.assertIn("protected", res["message"].lower())

        # Protected critical PID 0/1
        res_pid1 = pm.kill_process("1")
        self.assertFalse(res_pid1["success"])
        self.assertIn("critical", res_pid1["message"].lower())

    def test_config_file_owner_permissions(self):
        test_cfg_path = os.path.join(self.dir_path, "test_config.json")
        with patch.object(lexi, 'CONFIG_FILE', test_cfg_path):
            sample_data = {"test_key": "secret_token_12345"}
            ok = lexi.save_config(sample_data)
            self.assertTrue(ok)
            self.assertTrue(os.path.exists(test_cfg_path))
            mode = os.stat(test_cfg_path).st_mode & 0o777
            self.assertEqual(mode, 0o600)

            # Test load_config enforces 0600 if altered
            os.chmod(test_cfg_path, 0o644)
            lexi.load_config()
            restored_mode = os.stat(test_cfg_path).st_mode & 0o777
            self.assertEqual(restored_mode, 0o600)

    def test_terminal_diagnose_secret_redaction(self):
        raw_cmd = 'curl -H "Authorization: Bearer sk-secret123456789" https://api.openai.com/v1 -d "token=abc987xyz"'
        temp_hist = self.dir_path / "mock_history"
        temp_hist.write_text(raw_cmd + "\n")

        with patch("os.path.expanduser", return_value=str(temp_hist)):
            diag = lexi.SystemActionEngine().execute_terminal_diagnose()
            self.assertTrue(diag["success"])
            msg = diag["message"]
            self.assertNotIn("sk-secret123456789", msg)
            self.assertNotIn("abc987xyz", msg)
            self.assertIn("***REDACTED***", msg)

    def test_p2p_download_scheme_validation(self):
        server = lexi_drop.LexiDropServer()

        # Reject file:// scheme SSRF
        with patch("urllib.request.urlopen") as mock_open:
            server._download_peer_file_sync("file:///etc/passwd", "passwd.txt", 100, "token123")
            mock_open.assert_not_called()

        # Reject gopher:// scheme
        with patch("urllib.request.urlopen") as mock_open:
            server._download_peer_file_sync("gopher://127.0.0.1:70", "data.bin", 100, "token456")
            mock_open.assert_not_called()


    async def test_handle_share_request_restrictions(self):
        server = lexi_drop.LexiDropServer()

        # Non-loopback caller: 403 Forbidden
        req_remote = MagicMock()
        req_remote.remote = "192.168.1.100"
        resp = await server.handle_share_request(req_remote)
        self.assertEqual(resp.status, 403)

        # Loopback caller with sensitive path: 403 Forbidden
        req_local_sens = MagicMock()
        req_local_sens.remote = "127.0.0.1"
        async def mock_json():
            return {"filepath": "/etc/shadow"}
        req_local_sens.json = mock_json
        resp2 = await server.handle_share_request(req_local_sens)
        self.assertEqual(resp2.status, 403)


if __name__ == '__main__':
    unittest.main()
