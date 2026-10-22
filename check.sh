#!/usr/bin/env bash
# One-command health check for Lexi. Run from the project folder: ./check.sh
set -u
cd "$(dirname "$0")" || exit 1
status=0
echo "== compile =="
python3 -m py_compile lexi.py lexi_actions.py lexi_apps.py lexi_assistant.py lexi_capabilities.py \
  lexi_policy.py lexi_intents.py lexi_memory.py lexi_browser_host.py lexi_whatsapp_playwright.py lexi_drop.py || status=1
echo "== unit tests =="
python3 -W ignore::ResourceWarning -m unittest test_lexi_core tests.test_lexi_chrome_bridge tests.test_close_named_tabs \
  tests.test_lexi_apps tests.test_terminal_tasks tests.test_phone_mirror || status=1
echo "== service (informational) =="
systemctl --user status lexi-island.service --no-pager 2>/dev/null | head -3 || echo "service not available here"
[ $status -eq 0 ] && echo "ALL CHECKS PASSED" || echo "CHECKS FAILED"
exit $status
