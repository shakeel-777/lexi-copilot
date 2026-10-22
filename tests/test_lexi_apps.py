"""Tests for lexi_apps: no real process is ever signalled or killed."""

import os
import signal
import sys
import tempfile
import unittest

PROJECT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)

import lexi_apps as A  # noqa: E402

CHROME_EXE = "/opt/google/chrome/chrome"


def chrome_tree(main=100):
    """What the owner's machine really looks like: comm 'chrome', exe under /opt."""
    return [
        A.Proc(main, 1, "chrome", CHROME_EXE, [CHROME_EXE]),
        A.Proc(main + 1, main, "chrome", CHROME_EXE, [CHROME_EXE, "--type=zygote"]),
        A.Proc(main + 2, main + 1, "chrome", CHROME_EXE, [CHROME_EXE, "--type=renderer"]),
        A.Proc(main + 3, main + 1, "chrome", CHROME_EXE, [CHROME_EXE, "--type=gpu-process"]),
        A.Proc(main + 4, main, "chrome_crashpad", "/opt/google/chrome/chrome_crashpad_handler",
               ["chrome_crashpad_handler"]),
        A.Proc(900, 1, "bash", "/usr/bin/bash", ["bash"]),
    ]


class FakeSystem:
    """Fake process table + kill + clock. `exits_on_term` decides if the app obeys SIGTERM."""

    def __init__(self, procs, exits_on_term=True):
        self.procs = list(procs)
        self.exits_on_term = exits_on_term
        self.signals = []
        self.now = 0.0

    def list_fn(self):
        return list(self.procs)

    def kill_fn(self, pid, sig):
        self.signals.append((pid, sig))
        if self.exits_on_term and sig == signal.SIGTERM:
            self.procs = [p for p in self.procs if p.pid != pid and p.ppid != pid
                          and not (p.ppid in {q.pid for q in self.procs if q.ppid == pid})]

    def sleep_fn(self, s):
        self.now += s

    def clock_fn(self):
        return self.now

    def controller(self, **kw):
        return A.AppController(self.list_fn, self.kill_fn, self.sleep_fn, self.clock_fn, **kw)


class ResolveTests(unittest.TestCase):
    def test_names(self):
        for n in ("chrome", "Google Chrome", "the browser", "my chrome"):
            self.assertEqual(A.resolve_app(n).key, "chrome", n)
        self.assertEqual(A.resolve_app("spotify").key, "spotify")
        self.assertEqual(A.resolve_app("file manager").key, "files")
        self.assertIsNone(A.resolve_app("photoshop"))


class DiscoveryTests(unittest.TestCase):
    def test_real_chrome_process_name_is_found(self):
        mains = A.find_main_procs(A.APPS["chrome"], chrome_tree(), self_pid=-1)
        self.assertEqual([p.pid for p in mains], [100])

    def test_helpers_and_crashpad_are_not_main(self):
        mains = A.find_main_procs(A.APPS["chrome"], chrome_tree(), self_pid=-1)
        self.assertTrue(all("--type=" not in " ".join(p.argv) for p in mains))

    def test_old_launcher_names_are_not_required(self):
        # The original bug: only google-chrome(-stable) was searched.
        procs = [A.Proc(5, 1, "chrome", CHROME_EXE, [CHROME_EXE])]
        self.assertTrue(A.find_main_procs(A.APPS["chrome"], procs, self_pid=-1))

    def test_unrelated_process_not_matched(self):
        procs = [A.Proc(7, 1, "chromedriver", "/usr/bin/chromedriver", ["chromedriver"])]
        self.assertFalse(A.find_main_procs(A.APPS["chrome"], procs, self_pid=-1))

    def test_list_procs_reads_a_proc_tree(self):
        with tempfile.TemporaryDirectory() as root:
            d = os.path.join(root, "321")
            os.makedirs(d)
            open(os.path.join(d, "comm"), "w").write("chrome\n")
            open(os.path.join(d, "cmdline"), "wb").write(CHROME_EXE.encode() + b"\0")
            open(os.path.join(d, "stat"), "w").write("321 (chrome) S 1 321 321 0 -1 0")
            os.symlink(CHROME_EXE, os.path.join(d, "exe"))
            os.makedirs(os.path.join(root, "self"))          # non-numeric entry is ignored
            procs = A.list_procs(root)
        self.assertEqual(len(procs), 1)
        p = procs[0]
        self.assertEqual((p.pid, p.ppid, p.comm, p.exe), (321, 1, "chrome", CHROME_EXE))
        self.assertTrue(A.find_main_procs(A.APPS["chrome"], procs, self_pid=-1))


class QuitTests(unittest.TestCase):
    def test_browser_absent_is_not_open_and_nothing_signalled(self):
        sysm = FakeSystem([A.Proc(900, 1, "bash", "/usr/bin/bash", ["bash"])])
        res = sysm.controller().quit(A.APPS["chrome"])
        self.assertTrue(res["success"])
        self.assertFalse(res["closed"])
        self.assertFalse(res["was_running"])
        self.assertIn("isn't open", res["message"])
        self.assertEqual(sysm.signals, [])

    def test_browser_present_and_exits_is_verified_closed(self):
        sysm = FakeSystem(chrome_tree())
        res = sysm.controller().quit(A.APPS["chrome"])
        self.assertTrue(res["success"] and res["verified"] and res["closed"])
        self.assertIn("Closed Chrome", res["message"])
        self.assertEqual(sysm.signals, [(100, signal.SIGTERM)])     # main process only
        self.assertFalse(A.find_main_procs(A.APPS["chrome"], sysm.list_fn(), self_pid=-1))

    def test_browser_stays_open_is_failure_not_closed(self):
        sysm = FakeSystem(chrome_tree(), exits_on_term=False)
        res = sysm.controller(timeout=2.0).quit(A.APPS["chrome"])
        self.assertFalse(res["success"])
        self.assertFalse(res["closed"])
        self.assertFalse(res["verified"])
        self.assertIn("still open", res["message"])
        self.assertNotIn("Closed", res["message"])
        self.assertGreaterEqual(sysm.now, 2.0)                      # it really waited
        self.assertTrue(all(sig == signal.SIGTERM for _, sig in sysm.signals))   # never SIGKILL

    def test_helper_only_left_does_not_count_as_main_but_still_not_false_success_when_main_lives(self):
        procs = chrome_tree()
        sysm = FakeSystem(procs, exits_on_term=False)
        res = sysm.controller(timeout=1.0).quit(A.APPS["chrome"])
        self.assertFalse(res["success"])

    def test_permission_denied_is_reported(self):
        sysm = FakeSystem(chrome_tree())

        def deny(pid, sig):
            raise PermissionError()
        ctl = A.AppController(sysm.list_fn, deny, sysm.sleep_fn, sysm.clock_fn)
        res = ctl.quit(A.APPS["chrome"])
        self.assertFalse(res["success"])
        self.assertIn("permission", res["message"])

    def test_process_vanishing_during_signal_then_gone_is_closed(self):
        sysm = FakeSystem(chrome_tree())

        def vanish(pid, sig):
            sysm.procs = []
            raise ProcessLookupError()
        ctl = A.AppController(sysm.list_fn, vanish, sysm.sleep_fn, sysm.clock_fn)
        res = ctl.quit(A.APPS["chrome"])
        self.assertTrue(res["closed"])

    def test_unknown_app_is_refused_honestly(self):
        res = A.quit_app("photoshop", FakeSystem([]).controller())
        self.assertFalse(res["success"])
        self.assertIn("don't know how to close", res["message"])

    def test_spotify_quit(self):
        sp = "/opt/spotify/spotify"
        sysm = FakeSystem([A.Proc(50, 1, "spotify", sp, [sp]),
                           A.Proc(51, 50, "spotify", sp, [sp, "--type=renderer"])])
        res = A.quit_app("spotify", sysm.controller())
        self.assertTrue(res["closed"])


if __name__ == "__main__":
    unittest.main()
