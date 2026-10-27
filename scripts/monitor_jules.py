#!/usr/bin/env python3
"""Monitor Google Jules session, pull patches, run tests, and sync to Windows VM."""

import subprocess
import time
import sys
import os
from pathlib import Path

SESSION_ID = "10584949307285797106"
REPO_DIR = Path("/home/shakeel/.local/share/mochi-copilot")
LOG_FILE = REPO_DIR / "jules_monitor.log"

def log(msg):
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    formatted = f"[{timestamp}] {msg}"
    print(formatted, flush=True)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(formatted + "\n")

def get_session_status():
    try:
        res = subprocess.run(
            ["jules", "remote", "list", "--session"],
            cwd=str(REPO_DIR),
            capture_output=True,
            text=True,
            timeout=30
        )
        for line in res.stdout.splitlines():
            if SESSION_ID[:10] in line:
                return line.strip()
    except Exception as e:
        log(f"Error querying jules: {e}")
    return None

def main():
    log(f"Starting Jules monitor for session {SESSION_ID}")
    for attempt in range(120): # Monitor up to 1 hour
        status_line = get_session_status()
        log(f"Check {attempt + 1}: {status_line or 'Status check pending'}")
        
        # Check diff availability
        try:
            diff_res = subprocess.run(
                ["jules", "remote", "pull", "--session", SESSION_ID],
                cwd=str(REPO_DIR),
                capture_output=True,
                text=True,
                timeout=30
            )
            if "No diff found" not in diff_res.stdout and diff_res.stdout.strip():
                log("Diff detected from Jules! Applying patch...")
                apply_res = subprocess.run(
                    ["jules", "remote", "pull", "--session", SESSION_ID, "--apply"],
                    cwd=str(REPO_DIR),
                    capture_output=True,
                    text=True,
                    timeout=30
                )
                log(f"Apply result: {apply_res.stdout} {apply_res.stderr}")
                
                # Run unit tests
                test_res = subprocess.run(
                    [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-p", "test_*.py"],
                    cwd=str(REPO_DIR),
                    capture_output=True,
                    text=True,
                    timeout=60
                )
                log(f"Test output:\n{test_res.stdout}\n{test_res.stderr}")
                if test_res.returncode == 0:
                    log("All unit tests passed successfully!")
                    break
                else:
                    log(f"Tests failed with code {test_res.returncode}. Continuing monitoring...")
        except Exception as e:
            log(f"Error checking diff: {e}")

        time.sleep(30)

    log("Jules monitor loop ended.")

if __name__ == "__main__":
    main()
