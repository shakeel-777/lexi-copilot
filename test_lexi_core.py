import time
import unittest

import lexi_actions as A
import lexi_intents as I
from lexi_assistant import LexiAssistant
from lexi_capabilities import REGISTRY, SchemaError, model_actions_to_plan, validate_structured
from lexi_memory import ConversationMemory


def ok(msg="done", **kw):
    return dict({"success": True, "verified": True, "message": msg}, **kw)


def make_assistant(**overrides):
    calls = []

    def rec(name, result):
        def _f(args):
            calls.append((name, dict(args)))
            return result(args) if callable(result) else result
        return _f

    execs = {
        "set_volume": rec("set_volume", ok("Volume: 60%")),
        "set_brightness": rec("set_brightness", ok("Brightness: 60%")),
        "lock_screen": rec("lock_screen", ok("Locked")),
        "open_target": rec("open_target", ok("Launched")),
        "media_control": rec("media_control", ok("Playing")),
        "spotify_play": rec("spotify_play", ok("Playing Believer")),
        "youtube_play": rec("youtube_play", ok("Playing on YouTube")),
        "close_chrome": rec("close_chrome", ok("Closed Chrome.")),
        "whatsapp_draft": rec("whatsapp_draft", ok("drafted")),
        "whatsapp_send": rec("whatsapp_send", ok("sent")),
        "whatsapp_cancel": rec("whatsapp_cancel", ok("cancelled")),
        "system_battery": rec("system_battery", ok("Battery is at 85% (discharging).")),
        "media_now_playing": rec("media_now_playing", ok("Currently playing 'Believer' on Spotify.")),
        "take_screenshot": rec("take_screenshot", ok("Screenshot saved to Pictures/Screenshots/test.png.")),
        "system_sleep": rec("system_sleep", ok("Putting system to sleep.")),
        "system_restart": rec("system_restart", ok("Restarting system.")),
        "system_poweroff": rec("system_poweroff", ok("Shutting down system.")),
    }
    execs.update(overrides)
    return LexiAssistant(execs), calls


class IntentParsing(unittest.TestCase):
    def r(self, text, last=None, pending=None):
        return I.route(text, last, pending)

    def test_play_resolves_spotify_without_saying_spotify(self):
        for t in ("play Believer", "can you play Believer", "could you please play Believer?", "Hey Lexi, play Believer please"):
            r = self.r(t)
            self.assertEqual((r.kind, r.name), (I.ACTION, "spotify_play"), t)
            self.assertEqual(r.args["query"], "Believer")

    def test_play_on_youtube(self):
        r = self.r("play Believer on youtube fullscreen")
        self.assertEqual(r.name, "youtube_play")
        self.assertEqual(r.args, {"query": "Believer", "fullscreen": "yes"})

    def test_volume_variants(self):
        self.assertEqual(self.r("set volume to 40%").args, {"percent": 40})
        self.assertEqual(self.r("make it louder", None).args, {"mode": "up", "step": 10})
        self.assertEqual(self.r("can you turn the volume down by 20").args, {"mode": "down", "step": 20})
        self.assertEqual(self.r("mute").args, {"mode": "mute"})
        self.assertEqual(self.r("volume 150").kind, I.CLARIFY)

    def test_brightness_and_lock(self):
        self.assertEqual(self.r("brightness 70").name, "brightness_set")
        self.assertEqual(self.r("make it dimmer").name, "brightness_change")
        self.assertEqual(self.r("lock the screen").name, "lock_screen")

    def test_media_controls(self):
        self.assertEqual(self.r("pause").args, {"command": "pause"})
        self.assertEqual(self.r("skip this song").args, {"command": "next"})
        self.assertEqual(self.r("play").args, {"command": "play"})
        self.assertEqual(self.r("go back").args, {"command": "previous"})

    def test_open_app_and_site(self):
        self.assertEqual((self.r("open chrome").name, self.r("open chrome").args), ("open_app", {"name": "chrome"}))
        self.assertEqual(self.r("open youtube").args, {"site": "youtube.com"})
        self.assertEqual(self.r("open github.com/anthropics").args, {"site": "github.com/anthropics"})
        self.assertEqual(self.r("open").kind, I.LLM)  # no object; LLM/clarify, never a guess

    def test_natural_language_open_variants(self):
        for phrase in ("open whatsapp", "open up whatsapp", "pull up whatsapp", "bring up whatsapp", "switch to whatsapp", "whatsapp"):
            r = self.r(phrase)
            self.assertEqual((r.kind, r.name, r.args), (I.ACTION, "open_app", {"name": "whatsapp"}), phrase)
        for app_name in ("calc", "calculator", "spotify", "chrome", "terminal", "files"):
            r = self.r(app_name)
            self.assertEqual(r.kind, I.ACTION, app_name)
        for site_name in ("youtube", "gmail", "github", "reddit"):
            r = self.r(site_name)
            self.assertEqual(r.kind, I.ACTION, site_name)

    def test_youtube_natural_query_extraction(self):
        r = self.r("play kuttabomma youtube fullvideo fullscreen")
        self.assertEqual((r.kind, r.name, r.args), (I.ACTION, "youtube_play", {"query": "kuttabomma", "fullscreen": "yes"}))
        r2 = self.r("play believer on yt")
        self.assertEqual((r2.kind, r2.name, r2.args), (I.ACTION, "youtube_play", {"query": "believer", "fullscreen": "no"}))

    def test_compound_sequence_commands(self):
        r = self.r("open spotify and play believer")
        self.assertEqual((r.kind, r.name, r.args), (I.ACTION, "spotify_play", {"query": "believer"}))
        r2 = self.r("mute and lock screen")
        self.assertEqual(r2.kind, I.SEQUENCE)
        self.assertEqual(len(r2.steps), 2)
        self.assertEqual(r2.steps[0].name, "volume_change")
        self.assertEqual(r2.steps[1].name, "lock_screen")

    def test_open_n_apps_and_vague_filter(self):
        for text in ("open 5 things", "open 5 app in my lap", "open 5 apps on my laptop"):
            r = self.r(text)
            self.assertEqual(r.kind, I.SEQUENCE, text)
            self.assertEqual(len(r.steps), 5, text)
            for step in r.steps:
                self.assertEqual(step.kind, I.ACTION)
                self.assertEqual(step.name, "open_app")
        for vague in ("open something", "open whatever", "open some apps"):
            r = self.r(vague)
            self.assertEqual(r.kind, I.LLM, vague)

    def test_close_chrome_uses_the_real_quit_action(self):
        for text in ("close chrome", "quit Google Chrome", "kill chrome browser"):
            route = self.r(text)
            self.assertEqual((route.kind, route.name, route.args), (I.ACTION, "close_chrome", {}), text)

    def test_whatsapp_is_draft_only(self):
        r = self.r("can you send hi to Mummy")
        self.assertEqual((r.kind, r.name), (I.ACTION, "whatsapp_draft"))
        self.assertEqual(r.args, {"contact": "Mummy", "message": "hi"})
        self.assertEqual(self.r("send hello there to Dad on whatsapp").args["contact"], "Dad")
        self.assertEqual(self.r("message Mummy saying I'll be late").args["message"], "I'll be late")

    def test_questions_and_chat_fall_through_to_llm(self):
        for t in ("can you see the last activity", "why did you play that", "what do you think of Believer",
                  "how does the volume slider work", "we did a great job", "send me the report by email to bob"):
            self.assertEqual(self.r(t).kind, I.LLM, t)

    def test_long_text_is_not_routed(self):
        self.assertEqual(self.r("play " + "x" * 350).kind, I.LLM)

    def test_ambiguous_followup_asks_one_question(self):
        self.assertEqual(self.r("do it").kind, I.CLARIFY)               # nothing to repeat
        self.assertEqual(self.r("send").kind, I.CLARIFY)
        rec = A.ActionRecord("lock_screen", {}, state=A.VERIFIED, finished=time.time())
        self.assertEqual(self.r("make it up", rec).kind, I.CLARIFY)     # volume or brightness?

    def test_relative_up_resolves_by_last_family(self):
        vol = A.ActionRecord("spotify_play", {"query": "x"}, state=A.VERIFIED, finished=time.time())
        bri = A.ActionRecord("brightness_set", {"percent": 50}, state=A.VERIFIED, finished=time.time())
        self.assertEqual(self.r("make it up", vol).name, "volume_change")
        self.assertEqual(self.r("make it up", bri).name, "brightness_change")

    def test_chatgpt_ask_and_search_routing(self):
        # Combined open + ask / prompt
        for phrase in ("open chatgpt and ask who are u", "open chatgpt and prompt who are u",
                       "open chatgpt and ask who are u in chrome"):
            r = self.r(phrase)
            self.assertEqual((r.kind, r.name, r.args), (I.ACTION, "open_site", {"site": "chatgpt.com/?q=who+are+u"}), phrase)

        # Direct ask / prompt
        r = self.r("ask chatgpt who are you")
        self.assertEqual((r.kind, r.name, r.args), (I.ACTION, "open_site", {"site": "chatgpt.com/?q=who+are+you"}))
        r2 = self.r("ask who are you in chatgpt")
        self.assertEqual((r2.kind, r2.name, r2.args), (I.ACTION, "open_site", {"site": "chatgpt.com/?q=who+are+you"}))

    def test_search_engines_and_modifiers(self):
        # Google search variants
        for phrase in ("open google and search cute cats", "search google for cute cats",
                       "search for cute cats on google", "search cute cats in chrome",
                       "google cute cats", "search for cute cats"):
            r = self.r(phrase)
            self.assertEqual((r.kind, r.name, r.args), (I.ACTION, "open_site", {"site": "google.com/search?q=cute+cats"}), phrase)

        # YouTube, Reddit, GitHub, Spotify
        r_yt = self.r("open youtube and search lofi beats")
        self.assertEqual((r_yt.kind, r_yt.name, r_yt.args), (I.ACTION, "open_site", {"site": "youtube.com/results?search_query=lofi+beats"}))
        r_reddit = self.r("open reddit and search linux")
        self.assertEqual((r_reddit.kind, r_reddit.name, r_reddit.args), (I.ACTION, "open_site", {"site": "reddit.com/search/?q=linux"}))
        r_gh = self.r("open github and search mochi-copilot")
        self.assertEqual((r_gh.kind, r_gh.name, r_gh.args), (I.ACTION, "open_site", {"site": "github.com/search?q=mochi-copilot"}))
        r_sp = self.r("search spotify for believer")
        self.assertEqual((r_sp.kind, r_sp.name, r_sp.args), (I.ACTION, "spotify_play", {"query": "believer"}))

    def test_conversational_modifier_followup(self):
        # Without previous open_site
        r = self.r("in chrome")
        self.assertEqual(r.kind, I.CLARIFY)
        self.assertIn("search or open in Chrome", r.text)

        # With previous open_site
        last = A.ActionRecord("open_site", {"site": "chatgpt.com/?q=who+are+u"}, state=A.VERIFIED, finished=time.time())
        r_follow = self.r("in chrome", last=last)
        self.assertEqual(r_follow.kind, I.REPLY)
        self.assertIn("default browser", r_follow.text)


class CapabilitySchemas(unittest.TestCase):
    def test_valid_and_clamped(self):
        self.assertEqual(validate_structured("volume_set", {"percent": "40"}), {"percent": 40})
        with self.assertRaises(SchemaError):
            validate_structured("volume_set", {"percent": 101})
        with self.assertRaises(SchemaError):
            validate_structured("brightness_set", {"percent": 1})

    def test_unknown_and_extra_args_rejected(self):
        with self.assertRaises(SchemaError):
            validate_structured("rm_rf", {})
        with self.assertRaises(SchemaError):
            validate_structured("lock_screen", {"cmd": "x"})
        with self.assertRaises(SchemaError):
            validate_structured("spotify_play", {"query": "x", "selector": "#a"})

    def test_injection_in_strings_rejected(self):
        for bad in ("a; rm -rf ~", "x $(whoami)", "`id`", "a | b", "javascript:alert(1)", "xdotool key Return"):
            with self.assertRaises(SchemaError, msg=bad):
                validate_structured("spotify_play", {"query": bad})
        with self.assertRaises(SchemaError):
            validate_structured("open_app", {"name": "../../bin/sh"})

    def test_whatsapp_message_allows_punctuation_but_not_nul(self):
        out = validate_structured("whatsapp_draft", {"contact": "Mummy", "message": "Hi! I'm late :) see you; ok?"})
        self.assertIn("late", out["message"])

    def test_model_cannot_use_keys_typing_or_unregistered_actions(self):
        acts = [
            {"type": "key", "attrs": {"key": "Return"}, "body": ""},
            {"type": "type", "attrs": {"text": "hi"}, "body": ""},
            {"type": "", "attrs": {"name": "run_shell"}, "body": "{}"},
            {"type": "open", "attrs": {}, "body": "/etc/passwd"},
            {"type": "open", "attrs": {}, "body": "javascript:alert(1)"},
        ]
        accepted, rejected = model_actions_to_plan(acts)
        self.assertEqual(accepted, [])
        self.assertEqual(len(rejected), len(acts))

    def test_model_whatsapp_becomes_draft_never_send(self):
        accepted, _ = model_actions_to_plan([
            {"type": "whatsapp", "attrs": {"contact": "Mom"}, "body": "hi"}])
        self.assertEqual(accepted[0][0], "whatsapp_draft")
        accepted, _ = model_actions_to_plan([{"type": "", "attrs": {"name": "whatsapp_send", "contact": "Mom"}, "body": ""}])
        a, _ = make_assistant()
        reply = a.handle_model_actions([{"type": "", "attrs": {"name": "whatsapp_send", "contact": "Mom"}, "body": ""}])
        self.assertIn("confirmation", reply)

    def test_registered_json_and_legacy_forms_accepted(self):
        accepted, rej = model_actions_to_plan([
            {"type": "", "attrs": {"name": "volume_set"}, "body": '{"percent": 30}'},
            {"type": "volume", "attrs": {"level": "70%"}, "body": ""},
            {"type": "media", "attrs": {"command": "play Believer"}, "body": ""},
        ])
        self.assertEqual([n for n, _ in accepted], ["volume_set", "volume_set", "spotify_play"])
        self.assertEqual(rej, [])

    def test_registry_is_complete(self):
        for cap in REGISTRY.values():
            self.assertTrue(cap.executor and cap.failure_text and cap.verification)
        self.assertTrue(REGISTRY["whatsapp_send"].sensitive)


class RetryBoundaries(unittest.TestCase):
    def test_do_it_retries_only_previous_failed_nonsensitive(self):
        n = {"i": 0}

        def flaky(args):
            n["i"] += 1
            return {"success": False, "message": "no player"} if n["i"] == 1 else ok("Playing")
        a, calls = make_assistant(media_control=flaky)
        self.assertIn("couldn't", a.handle_user_text("pause"))
        self.assertEqual(a.last_action.state, A.FAILED)
        self.assertIn("Playing", a.handle_user_text("do it"))
        self.assertEqual(n["i"], 2)
        self.assertEqual(a.last_action.state, A.VERIFIED)

    def test_do_it_after_success_does_not_replay_old_request(self):
        a, calls = make_assistant()
        a.handle_user_text("lock the screen")
        before = len(calls)
        reply = a.handle_user_text("do it")
        self.assertEqual(len(calls), before)
        self.assertTrue(reply.startswith("Do what"))

    def test_stale_failure_not_retried(self):
        rec = A.ActionRecord("media_control", {"command": "play"}, state=A.FAILED,
                             finished=time.time() - 3600)
        ok_, why = A.can_retry(rec)
        self.assertFalse(ok_)

    def test_whatsapp_never_silently_retried(self):
        a, calls = make_assistant(whatsapp_draft=lambda args: {"success": False, "message": "no chat"})
        a.handle_user_text("send hi to Mummy")
        before = len(calls)
        reply = a.handle_user_text("try again")
        self.assertEqual(len(calls), before)
        self.assertIn("fresh", reply)

    def test_second_failure_is_bounded(self):
        a, calls = make_assistant(media_control=lambda args: {"success": False, "message": "no"})
        a.handle_user_text("pause")                              # attempt 1 fails
        a.handle_user_text("try again")                          # one retry, fails
        self.assertEqual(a.last_action.attempts, 2)
        self.assertIn("failed twice", a.handle_user_text("try again"))
        self.assertEqual(a.last_action.attempts, 2)              # no third run


class WhatsAppFlow(unittest.TestCase):
    def test_draft_then_explicit_confirm(self):
        a, calls = make_assistant()
        reply = a.handle_user_text("can you send hi to Mummy")
        self.assertIn("NOT been sent", reply)
        self.assertEqual([c[0] for c in calls], ["whatsapp_draft"])
        # vague yes / do it must not send
        for vague in ("yes", "ok", "do it", "go ahead"):
            a.handle_user_text(vague)
        self.assertNotIn("whatsapp_send", [c[0] for c in calls])
        self.assertIn("sent", a.handle_user_text("send it").lower())
        self.assertEqual([c[0] for c in calls][-1], "whatsapp_send")
        self.assertIsNone(a.pending)

    def test_cancel(self):
        a, calls = make_assistant()
        a.handle_user_text("send hi to Mummy")
        self.assertIn("won't send", a.handle_user_text("cancel"))
        self.assertNotIn("whatsapp_send", [c[0] for c in calls])
        self.assertIsNone(a.handle_user_text("send it"))            # nothing pending: never sends

    def test_confirmation_expires(self):
        a, calls = make_assistant()
        a.handle_user_text("send hi to Mummy")
        a.pending.created -= 10_000
        self.assertIsNone(a.handle_user_text("send it"))       # falls to LLM, nothing sent
        self.assertNotIn("whatsapp_send", [c[0] for c in calls])

    def test_new_request_discards_unsent_draft(self):
        a, calls = make_assistant()
        a.handle_user_text("send hi to Mummy")
        a.handle_user_text("pause")
        self.assertIsNone(a.pending)
        self.assertIn("whatsapp_cancel", [c[0] for c in calls])

    def test_send_failure_is_reported_honestly(self):
        a, _ = make_assistant(whatsapp_send=lambda args: {"success": False, "message": "draft is gone"})
        a.handle_user_text("send hi to Mummy")
        reply = a.handle_user_text("send it")
        self.assertNotIn("Sent", reply)
        self.assertIn("couldn't send", reply)


class Truthfulness(unittest.TestCase):
    def test_failure_never_reported_as_success(self):
        a, _ = make_assistant(spotify_play=lambda args: {"success": False, "message": "Spotify isn't running"})
        reply = a.handle_user_text("play Believer")
        self.assertEqual(a.last_action.state, A.FAILED)
        self.assertIn("couldn't", reply)

    def test_unverified_success_carries_caveat(self):
        a, _ = make_assistant(open_target=lambda args: {"success": True, "verified": False, "message": "Opened foo"})
        reply = a.handle_user_text("open foo")
        self.assertIn("couldn't confirm", reply)

    def test_executor_exception_becomes_failure(self):
        def boom(args):
            raise RuntimeError("dbus gone")
        a, _ = make_assistant(set_volume=boom)
        reply = a.handle_user_text("volume 40")
        self.assertEqual(a.last_action.state, A.FAILED)
        self.assertIn("couldn't", reply)

    def test_missing_executor_is_failure(self):
        a = LexiAssistant({})
        self.assertIn("couldn't", a.handle_user_text("lock the screen"))

    def test_make_it_louder_after_play(self):
        a, calls = make_assistant()
        a.handle_user_text("play Believer")
        a.handle_user_text("make it louder")
        self.assertEqual(calls[-1], ("set_volume", {"mode": "up", "step": 10}))

    def test_unsupported_model_action_one_honest_sentence(self):
        a, calls = make_assistant()
        reply = a.handle_model_actions([{"type": "key", "attrs": {}, "body": "f"}])
        self.assertTrue(reply.startswith("I can't do that"))
        self.assertEqual(calls, [])

    def test_gui_input_via_shell_is_blocked_by_policy_not_run(self):
        a, calls = make_assistant()
        reply = a.handle_model_actions([{"type": "command", "attrs": {}, "body": "sleep 1 && xdotool key f"}])
        self.assertIn("GUI input", reply)
        self.assertFalse([c for c in calls if c[0] == "terminal_task"])

    def test_model_chain_stops_after_failure(self):
        a, calls = make_assistant(media_control=lambda args: {"success": False, "message": "no"})
        a.handle_model_actions([
            {"type": "media", "attrs": {"command": "pause"}, "body": ""},
            {"type": "lock", "attrs": {}, "body": ""},
        ])
        self.assertEqual([c[0] for c in calls], [])  # lock never ran
        self.assertEqual(a.last_action.state, A.FAILED)


class MemoryCompaction(unittest.TestCase):
    def test_recent_window_is_bounded_and_summary_grows_slowly(self):
        m = ConversationMemory()
        for i in range(60):
            m.add_turn("user", f"question number {i} about topic {i}")
            m.add_turn("assistant", f"answer number {i}")
        self.assertLessEqual(len(m.recent), m.max_recent)
        self.assertLessEqual(len(m.summary), m.max_summary_chars + 5)
        self.assertIn("answer number 59", m.recent[-1]["content"])

    def test_images_and_base64_never_stored(self):
        m = ConversationMemory()
        big = "A" * 5000
        m.add_turn("user", [{"type": "text", "text": "what is this"},
                            {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + big}}])
        m.add_turn("user", "here: data:image/png;base64," + big)
        dump = repr(m.recent) + m.summary
        self.assertNotIn(big[:100], dump)
        self.assertIn("image", dump)

    def test_facts_extracted_locally(self):
        m = ConversationMemory()
        m.add_turn("user", "my name is Shakeel.")
        m.add_turn("user", "remember that mummy is saved as Mummy in WhatsApp")
        block = m.context_block()
        self.assertIn("Shakeel", block)
        self.assertIn("Mummy", block)

    def test_after_20_turns_task_and_last_action_survive_without_replay(self):
        a, calls = make_assistant(whatsapp_draft=lambda args: {"success": False, "message": "no chat"})
        a.handle_user_text("send hi to Mummy")
        for i in range(20):
            a.record_llm_exchange(f"tell me a fact {i}", f"fact {i}")
        block = a.memory.context_block()
        self.assertIn("whatsapp_draft", block)             # last action still known
        self.assertIn("Unfinished task", block)             # current task still known
        self.assertIn("Do NOT re-run", block)
        n_before = len(calls)
        msgs = a.build_messages({"role": "user", "content": "thanks"})
        self.assertEqual(len(calls), n_before)              # nothing replayed
        self.assertLessEqual(len(msgs), a.memory.max_recent + 2)
        self.assertEqual(msgs[0]["role"], "system")

    def test_tools_prompt_is_registry_driven_and_hides_send(self):
        a, _ = make_assistant()
        p = a.tools_prompt()
        self.assertIn("volume_set", p)
        self.assertNotIn("whatsapp_send", p)
        self.assertNotIn("xdotool key", p)
        self.assertLess(len(p), 3000)


if __name__ == "__main__":
    unittest.main()


class CloseTabsIntents(unittest.TestCase):
    def r(self, text, pending=None):
        return I.route(text, None, pending)

    def test_named_close_routes_to_single_site(self):
        for t, target in (
            ("close whatsapp", "whatsapp"),
            ("can you please close WhatsApp", "whatsapp"),
            ("hey lexi, close the whatsapp tab", "whatsapp"),
            ("close instagram", "instagram"),
            ("close the insta tab", "instagram"),
            ("quit instagram please", "instagram"),
        ):
            r = self.r(t)
            self.assertEqual((r.kind, r.name, r.args), (I.ACTION, "close_tabs", {"target": target}), t)

    def test_close_all_and_both_need_the_mass_close_action(self):
        for t in ("close all", "close everything", "close all tabs", "close both",
                  "close whatsapp and instagram", "close them all", "close all my tabs in chrome"):
            r = self.r(t)
            self.assertEqual((r.kind, r.name, r.args), (I.ACTION, "close_tabs_all", {}), t)

    def test_unsupported_targets_are_refused_honestly_not_guessed(self):
        for t in ("close firefox", "close gmail", "close photoshop"):
            r = self.r(t)
            self.assertEqual(r.kind, I.REPLY, t)
            self.assertIn("WhatsApp and Instagram", r.text)

    def test_known_apps_route_to_close_app(self):
        for t, key in (("close spotify", "spotify"), ("quit the file manager", "files"),
                       ("close the terminal", "terminal")):
            r = self.r(t)
            self.assertEqual((r.kind, r.name, r.args), (I.ACTION, "close_app", {"name": key}), t)

    def test_vague_close_asks_which_one(self):
        for t in ("close the tab", "close this window", "close my tabs"):
            self.assertEqual(self.r(t).kind, I.CLARIFY, t)

    def test_questions_and_past_tense_are_not_commands(self):
        for t in ("how do I close whatsapp", "I closed instagram yesterday", "why won't whatsapp close",
                  "is whatsapp closed"):
            self.assertEqual(self.r(t).kind, I.LLM, t)


class CloseTabsFlow(unittest.TestCase):
    def setUp(self):
        self.closed = []
        self.assistant, self.calls = make_assistant(**{
            "close_tabs": lambda a: (self.closed.append(("one", dict(a))) or ok("Closed 1 WhatsApp tab.")),
            "close_tabs_all": lambda a: (self.closed.append(("all", dict(a))) or ok("Closed 2 tabs.")),
        })

    def test_single_site_close_runs_immediately_with_enum_arg(self):
        reply = self.assistant.handle_user_text("close whatsapp")
        self.assertEqual(self.closed, [("one", {"target": "whatsapp"})])
        self.assertIn("Closed", reply)

    def test_close_chrome_runs_the_chrome_executor(self):
        reply = self.assistant.handle_user_text("close chrome")
        self.assertIn(("close_chrome", {}), self.calls)
        self.assertIn("Closed Chrome", reply)

    def test_close_all_never_runs_without_explicit_confirmation(self):
        reply = self.assistant.handle_user_text("close all")
        self.assertEqual(self.closed, [])
        self.assertIn("confirm", reply.lower())
        self.assertIn("only those two sites", reply)
        self.assertEqual(self.assistant.pending.name, "close_tabs_all")

    def test_vague_yes_and_send_it_do_not_authorise_close_all(self):
        self.assistant.handle_user_text("close all")
        for t in ("yes", "ok", "sure", "do it", "go ahead"):
            reply = self.assistant.handle_user_text(t)
            self.assertIn("confirm", reply.lower(), t)
        self.assertIsNone(self.assistant.handle_user_text("send it"))
        self.assertEqual(self.closed, [])
        self.assertIsNotNone(self.assistant.pending)

    def test_confirm_runs_exactly_once(self):
        self.assistant.handle_user_text("close all")
        reply = self.assistant.handle_user_text("confirm")
        self.assertEqual(self.closed, [("all", {})])
        self.assertIn("Closed", reply)
        self.assertIsNone(self.assistant.pending)
        self.assertIsNone(self.assistant.handle_user_text("confirm"))   # nothing pending any more
        self.assertEqual(len(self.closed), 1)

    def test_cancel_closes_nothing_and_does_not_touch_whatsapp_draft_state(self):
        self.assistant.handle_user_text("close all")
        reply = self.assistant.handle_user_text("cancel")
        self.assertEqual(self.closed, [])
        self.assertIn("open", reply.lower())
        self.assertNotIn(("whatsapp_cancel", {}), self.calls)
        self.assertIsNone(self.assistant.pending)

    def test_confirmation_expires(self):
        self.assistant.handle_user_text("close all")
        self.assistant.pending.created -= A.CONFIRM_WINDOW_SEC + 5
        self.assertIsNone(self.assistant.handle_user_text("confirm"))
        self.assertEqual(self.closed, [])

    def test_unrelated_request_discards_pending_close_all(self):
        self.assistant.handle_user_text("close all")
        self.assistant.handle_user_text("set volume to 30")
        self.assertIsNone(self.assistant.pending)
        self.assertIsNone(self.assistant.handle_user_text("confirm"))
        self.assertEqual(self.closed, [])

    def test_whatsapp_send_confirmation_cannot_confirm_a_close_all(self):
        self.assistant.handle_user_text("close all")
        self.assistant.handle_user_text("send it")
        self.assertEqual(self.closed, [])
        self.assertFalse(any(n == "whatsapp_send" for n, _ in self.calls))

    def test_close_all_confirmation_cannot_send_a_whatsapp_draft(self):
        self.assistant.handle_user_text("whatsapp Mom saying hello")
        self.assistant.handle_user_text("close all")            # replaces the draft
        self.assistant.handle_user_text("confirm")
        self.assertFalse(any(n == "whatsapp_send" for n, _ in self.calls))
        self.assertEqual(self.closed, [("all", {})])

    def test_failure_is_reported_honestly(self):
        a, _ = make_assistant(close_tabs=lambda args: {"success": False, "verified": False,
                                                       "message": "The bridge is not connected."})
        reply = a.handle_user_text("close instagram")
        self.assertNotIn("Closed", reply)
        self.assertIn("not connected", reply)

    def test_unverified_close_carries_caveat(self):
        a, _ = make_assistant(close_tabs=lambda args: {"success": True, "verified": False,
                                                       "message": "Closed 1 WhatsApp tab."})
        reply = a.handle_user_text("close whatsapp")
        self.assertIn("couldn't confirm", reply)

    def test_failed_single_close_can_be_retried_once(self):
        a, _ = make_assistant(close_tabs=lambda args: {"success": False, "message": "bridge down"})
        a.handle_user_text("close whatsapp")
        self.assertEqual(a.last_action.state, A.FAILED)
        a.handle_user_text("try again")
        self.assertEqual(a.last_action.attempts, 2)


class CloseTabsModelSafety(unittest.TestCase):
    def test_model_cannot_trigger_close_all(self):
        called = []
        a, _ = make_assistant(close_tabs_all=lambda args: called.append(args) or ok())
        a.handle_model_actions([{"type": "x", "attrs": {"name": "close_tabs_all"}, "body": ""}])
        self.assertEqual(called, [])

    def test_model_close_is_enum_only(self):
        for body in ('{"target": "facebook"}', '{"target": "https://evil.example"}',
                     '{"target": "whatsapp", "url": "https://x.com"}', '{"target": "all"}', '{}'):
            accepted, rejected = model_actions_to_plan(
                [{"type": "x", "attrs": {"name": "close_tabs"}, "body": body}])
            self.assertEqual(accepted, [], body)
            self.assertTrue(rejected, body)
        accepted, _ = model_actions_to_plan(
            [{"type": "x", "attrs": {"name": "close_tabs"}, "body": '{"target": "WhatsApp"}'}])
        self.assertEqual(accepted, [("close_tabs", {"target": "whatsapp"})])

    def test_close_all_is_hidden_from_the_model_prompt_but_single_close_is_listed(self):
        prompt = LexiAssistant({}).tools_prompt()
        self.assertIn("close_tabs(", prompt)
        self.assertNotIn("close_tabs_all", prompt)

    def test_registry_marks_close_all_sensitive_and_confirmed(self):
        cap = REGISTRY["close_tabs_all"]
        self.assertEqual(cap.confirmation, "required")
        self.assertTrue(cap.sensitive)
        self.assertFalse(cap.retry_safe)
        self.assertEqual(cap.schema, {})
        self.assertEqual(set(REGISTRY["close_tabs"].schema["target"].choices), {"whatsapp", "instagram"})


class SystemAndDesktopActions(unittest.TestCase):
    def setUp(self):
        self.assistant, self.calls = make_assistant()

    def test_battery_routing_and_execution(self):
        for text in ("battery", "battery level", "what's my battery level", "check battery", "battery percentage"):
            r = I.route(text)
            self.assertEqual((r.kind, r.name), (I.ACTION, "system_battery"), text)
        reply = self.assistant.handle_user_text("what is my battery level")
        self.assertIn(("system_battery", {}), self.calls)
        self.assertIn("Battery is at 85%", reply)

    def test_media_now_playing_routing_and_execution(self):
        for text in ("what's playing", "what song is playing", "now playing", "current song", "current track"):
            r = I.route(text)
            self.assertEqual((r.kind, r.name), (I.ACTION, "media_now_playing"), text)
        reply = self.assistant.handle_user_text("what's playing")
        self.assertIn(("media_now_playing", {}), self.calls)
        self.assertIn("Currently playing 'Believer'", reply)

    def test_screenshot_routing_and_execution(self):
        for text in ("take a screenshot", "screenshot", "capture screen", "take screenshot of the screen"):
            r = I.route(text)
            self.assertEqual((r.kind, r.name), (I.ACTION, "take_screenshot"), text)
        reply = self.assistant.handle_user_text("take a screenshot")
        self.assertIn(("take_screenshot", {}), self.calls)
        self.assertIn("Screenshot saved", reply)

    def test_sleep_requires_confirmation(self):
        for text in ("sleep", "suspend", "put computer to sleep"):
            r = I.route(text)
            self.assertEqual((r.kind, r.name), (I.ACTION, "system_sleep"), text)
        reply = self.assistant.handle_user_text("sleep")
        self.assertFalse(any(n == "system_sleep" for n, _ in self.calls))
        self.assertIn("confirm", reply.lower())
        self.assertIn("sleep", reply.lower())
        self.assertIsNotNone(self.assistant.pending)
        self.assertEqual(self.assistant.pending.name, "system_sleep")

        # Confirm
        confirm_reply = self.assistant.handle_user_text("confirm")
        self.assertIn(("system_sleep", {}), self.calls)
        self.assertIn("sleep", confirm_reply.lower())
        self.assertIsNone(self.assistant.pending)

    def test_restart_requires_confirmation(self):
        for text in ("restart", "reboot", "restart computer", "reboot my laptop"):
            r = I.route(text)
            self.assertEqual((r.kind, r.name), (I.ACTION, "system_restart"), text)
        reply = self.assistant.handle_user_text("restart")
        self.assertFalse(any(n == "system_restart" for n, _ in self.calls))
        self.assertIn("confirm", reply.lower())
        self.assertIn("restart", reply.lower())

        # Cancel
        cancel_reply = self.assistant.handle_user_text("cancel")
        self.assertFalse(any(n == "system_restart" for n, _ in self.calls))
        self.assertIn("cancelled", cancel_reply.lower())
        self.assertIsNone(self.assistant.pending)

    def test_poweroff_requires_confirmation(self):
        for text in ("shut down", "power off", "shutdown", "power off my pc"):
            r = I.route(text)
            self.assertEqual((r.kind, r.name), (I.ACTION, "system_poweroff"), text)
        reply = self.assistant.handle_user_text("shut down")
        self.assertFalse(any(n == "system_poweroff" for n, _ in self.calls))
        self.assertIn("confirm", reply.lower())
        self.assertIn("shut down", reply.lower())

        # Vague confirmation is refused
        for vague in ("yes", "ok", "sure", "do it"):
            vague_reply = self.assistant.handle_user_text(vague)
            self.assertIn("confirm", vague_reply.lower())
            self.assertFalse(any(n == "system_poweroff" for n, _ in self.calls))

        # Confirm
        confirm_reply = self.assistant.handle_user_text("confirm")
        self.assertIn(("system_poweroff", {}), self.calls)
        self.assertIn("shutting down", confirm_reply.lower())

    def test_router_only_capabilities_hidden_from_prompt(self):
        prompt = self.assistant.tools_prompt()
        for name in ("system_battery", "media_now_playing", "take_screenshot",
                     "system_sleep", "system_restart", "system_poweroff"):
            self.assertTrue(REGISTRY[name].router_only, f"{name} should be router_only")
            self.assertNotIn(f"{name}(", prompt, f"{name} should not be in tools_prompt")


class ProductivityAndQuickActions(unittest.TestCase):
    def test_google_suite_shortcuts(self):
        for text, exp_site in (
            ("start a meeting", "meet.google.com/new"),
            ("open google meet", "meet.google.com/new"),
            ("new google doc", "docs.new"),
            ("create document", "docs.new"),
            ("new sheet", "sheets.new"),
            ("create a google sheet", "sheets.new"),
            ("new slide", "slides.new"),
            ("create a slide", "slides.new"),
        ):
            r = I.route(text)
            self.assertEqual((r.kind, r.name, r.args), (I.ACTION, "open_site", {"site": exp_site}), text)

    def test_maps_and_directions(self):
        r1 = I.route("directions to Central Park")
        self.assertEqual((r1.kind, r1.name), (I.ACTION, "open_site"))
        self.assertIn("maps/search/Central+Park", r1.args["site"])

        r2 = I.route("where is Eiffel Tower")
        self.assertEqual((r2.kind, r2.name), (I.ACTION, "open_site"))
        self.assertIn("maps/search/Eiffel+Tower", r2.args["site"])

    def test_weather_queries(self):
        r1 = I.route("weather")
        self.assertEqual((r1.kind, r1.name), (I.ACTION, "open_site"))
        self.assertIn("weather+today", r1.args["site"])

        r2 = I.route("weather in London")
        self.assertEqual((r2.kind, r2.name), (I.ACTION, "open_site"))
        self.assertIn("weather+in+London", r2.args["site"])

    def test_define_word(self):
        r = I.route("define serendipity")
        self.assertEqual((r.kind, r.name), (I.ACTION, "open_site"))
        self.assertIn("define+serendipity", r.args["site"])

        r2 = I.route("meaning of resilient")
        self.assertEqual((r2.kind, r2.name), (I.ACTION, "open_site"))
        self.assertIn("define+resilient", r2.args["site"])


class LocalReplMathAndEtiquette(unittest.TestCase):
    def test_safe_math_evaluation(self):
        cases = [
            ("what is 25 * 4", "25 * 4 = 100"),
            ("calculate 10 + 20 * 3", "10 + 20 * 3 = 70"),
            ("15% of 850", "15% of 850 = 127.5"),
            ("calculate 2 ^ 8", "2 ^ 8 = 256"),
            ("what is 100 / 4", "100 / 4 = 25"),
            ("10 / 0", "Division by zero is undefined."),
        ]
        for query, expected in cases:
            r = I.route(query)
            self.assertEqual(r.kind, I.REPLY, query)
            self.assertEqual(r.text, expected, query)

    def test_time_and_date(self):
        for query in ("what time is it", "what is the time", "current time", "what is today's date", "today's date", "what day is today"):
            r = I.route(query)
            self.assertEqual(r.kind, I.REPLY, query)
            self.assertTrue(r.text.startswith("It's "))

    def test_conversational_etiquette(self):
        r_thanks = I.route("thank you so much")
        self.assertEqual(r_thanks.kind, I.REPLY)
        self.assertEqual(r_thanks.text, "You're welcome!")

        r_who = I.route("who are you")
        self.assertEqual(r_who.kind, I.REPLY)
        self.assertIn("I'm Lexi", r_who.text)

        r_help = I.route("help")
        self.assertEqual(r_help.kind, I.REPLY)
        self.assertIn("I can control volume", r_help.text)

