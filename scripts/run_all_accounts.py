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
import time

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from core.account_loader import discover_accounts  # noqa: E402

MAX_ITERATIONS_PER_ACCOUNT = 10   # safety cap — enough for 40 clips per video
PAUSE_BETWEEN_RUNS_S = 2          # brief pause between scheduler calls


def run_account(account_name: str) -> bool:
    """Run generation_scheduler for one account until fully done.

    Returns True on success, False if the scheduler returned a non-zero
    exit code (pipeline failure).
    """
    print(f"\n{'='*60}")
    print(f"  Account: {account_name}")
    print(f"{'='*60}")

    for iteration in range(1, MAX_ITERATIONS_PER_ACCOUNT + 1):
        print(f"\n[{account_name}] Run {iteration}/{MAX_ITERATIONS_PER_ACCOUNT} ...")
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

        # Check if there are still unrendered clips for any video in this account
        still_pending = _has_unrendered_clips(account_name)
        if not still_pending:
            print(f"[{account_name}] All clips rendered and metadata exported. Done.")
            return True

        print(f"[{account_name}] Clips still pending — continuing (iteration {iteration})...")
        time.sleep(PAUSE_BETWEEN_RUNS_S)

    print(f"[{account_name}] WARNING: hit max iterations ({MAX_ITERATIONS_PER_ACCOUNT}). "
          "Some clips may still be unrendered.", file=sys.stderr)
    return True  # not a hard failure — will resume on next scheduled run


def _has_unrendered_clips(account_name: str) -> bool:
    """Return True if this account has any clips in DB with no video_path."""
    try:
        import sqlite3
        db_path = os.path.join(_PROJECT_ROOT, "output", "shorts_factory.db")
        if not os.path.exists(db_path):
            return False
        conn = sqlite3.connect(db_path)
        c = conn.cursor()
        # Find video_ids for this account by matching output path prefix
        output_prefix = os.path.join(_PROJECT_ROOT, "output", account_name) + os.sep
        c.execute(
            "SELECT COUNT(*) FROM clips c "
            "JOIN videos v ON c.video_id = v.video_id "
            "WHERE (c.video_path IS NULL OR c.video_path = '') "
            "AND v.file_path LIKE ?",
            (f"%{account_name}%",)
        )
        count = c.fetchone()[0]
        conn.close()
        return count > 0
    except Exception as exc:
        print(f"[{account_name}] WARNING: could not check unrendered clips — {exc}")
        return False


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
