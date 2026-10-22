# Lexi fix pass, 2026-10-21

All 139 tests pass (`./check.sh`). Nothing was run against a live desktop; no real
Chrome was touched. Please reload the service after copying files over.

## 1. "close chrome" no longer lies  (lexi_apps.py, new)
Root cause confirmed: `close_chrome()` pgrep'd for `google-chrome(-stable)`; this machine's
process is `chrome` (`/opt/google/chrome/chrome`), so it found nothing, and the "not running"
branch was returned as success-sounding text.
- New `lexi_apps.py`: reads /proc, matches by comm name OR exe path, picks the **main**
  process (ignores `--type=` renderer/gpu/zygote helpers and crashpad), sends SIGTERM,
  then re-reads the process table until the main process is gone or a 6 s timeout.
- Replies: "Closed Chrome (all its windows)." only after verified exit; "Chrome isn't open."
  if absent (nothing signalled); "Chrome is still open ... I didn't force it." if it lingers.
  Never SIGKILL.
- Same layer now serves `close spotify`, `close files`, `close terminal`
  (new `close_app` capability). Terminal asks for confirmation first.
- Not changed: WhatsApp/Instagram tab closing still goes through the Chrome extension.
  Arbitrary sites/individual windows are still unsupported (needs bridge/GNOME extension work).

## 2 + 4. One action architecture  (lexi_policy.py new; lexi_capabilities/actions/assistant)
Root cause of "file creation is unreliable": the live path builds its prompt from
`LexiAssistant.tools_prompt()` (which said "No shell commands") and runs model output through
`model_actions_to_plan()` (which rejected `command` tags). The terminal-access paragraph added
to the saved prompt by `load_config()` was never sent, and the legacy command path was bypassed.
- New capability `terminal_task` (one bash command, reports exit code + real output).
  Legacy `<action type="command">` tags are kept as a compatibility adapter that maps onto it.
- One execution policy, `lexi_policy.classify_command()` -> blocked / confirm / allow:
  - blocked (no dialog ever): recursive delete of `/` (incl. `/*`, `//`, `bash -c`, `eval`),
    xdotool/ydotool/wtype-style GUI input.
  - confirm: rm, sudo, dd, mkfs, curl|sh, recursive chmod/chown, find -delete, mv-overwrite,
    and `>` onto an **existing** file.
  - allow: everything else (mkdir, touch, cp, cat, ls, git, `echo x > newfile`, ...).
- `run_action()` applies the policy; the GTK dialog (`_confirm_risky`) is wired in as the
  confirmer. Without a confirmer a risky command is NOT run.
- Prompt now documents terminal_task and close_app. Model step cap raised 3 -> 5.
- Free-text filter no longer mangles shell text (`document.txt` used to trip the JS-token filter).

## 3. Dead `$` shortcut fixed
`handle_direct_command()` still began with an early return printing "Terminal commands are
disabled", so the restored `$ <command>` handler below it was unreachable. Removed.

## Behaviour changes to be aware of
- `echo x > file` is no longer a confirmation prompt when `file` does not exist (creating a
  file is ordinary work). Overwriting an existing file still asks. One existing test was updated
  to use an existing file; its intent (overwrite asks) is preserved.
- Old tests that asserted "close spotify / terminal are refused" and "shell commands from the
  model are rejected" were updated, because those restrictions were the bugs.

## Remaining limitations
- Terminal commands time out at 15 s (`run_command` default); long installs will report a timeout.
- `close_app` is process-level: it closes every window of that app, and says so.
- Chrome session-restore prompts may appear after SIGTERM, depending on Chrome settings.
- Not exercised in the live GTK UI or against the real `/proc` of the owner's machine.
