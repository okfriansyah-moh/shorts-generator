#!/usr/bin/env python3
"""Multi-account generation runner for Shorts Factory.

Discovers all enabled accounts under config/accounts/ and runs the
generation_scheduler for each one, looping until all clips are fully
rendered before moving to the next account.

Exit codes:
  0 — all accounts processed (or nothing to do)
  1 — one or more accounts failed
"""

from __future__ import annotations

import os
import subprocess
import sys

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from core.account_loader import discover_accounts  # noqa: E402

def run_account(account_name: str) -> bool:
    """Run generation_scheduler once for one account.

    Returns True on success, False if the scheduler returned a non-zero
    exit code (pipeline failure).
    """
    print(f"\n{'='*60}")
    print(f"  Account: {account_name}")
    print(f"{'='*60}")

    result = subprocess.run(
        [sys.executable, os.path.join(_PROJECT_ROOT, "scripts", "generation_scheduler.py"),
         "--account", account_name],
        cwd=_PROJECT_ROOT,
    )

    if result.returncode == 1:
        print(f"[{account_name}] ERROR: pipeline failed (exit 1) — stopping.", file=sys.stderr)
        return False

    if result.returncode == 2:
        print(f"[{account_name}] FATAL: config error (exit 2) — stopping.", file=sys.stderr)
        return False

    print(f"[{account_name}] Generation run completed.")
    return True


def main() -> int:
    os.chdir(_PROJECT_ROOT)

    accounts = discover_accounts()
    if not accounts:
        print("No accounts found under config/accounts/ — nothing to do.")
        return 0

    print(f"Found {len(accounts)} account(s): {', '.join(accounts)}")

    failed = []
    for account in accounts:
        success = run_account(account)
        if not success:
            failed.append(account)

    print(f"\n{'='*60}")
    if failed:
        print(f"DONE — {len(failed)} account(s) failed: {', '.join(failed)}")
        return 1
    print(f"DONE — all {len(accounts)} account(s) processed successfully.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
