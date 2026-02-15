#!/usr/bin/env python3
"""
ShiftMatch — Daily Email Scheduler
Runs as a standalone daemon. Sends top-5-match emails at the configured time (default 8:01 PM).

Usage:
    python3 run_scheduler.py          # Start the scheduler daemon
    python3 run_scheduler.py --test   # Send one email immediately (test mode)
"""

import json
import os
import sys
import time
from datetime import datetime

import schedule

from email_notifier import load_config, send_daily_matches

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "email_config.json")


def job():
    print(f"\n[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Running daily ShiftMatch email job...")
    try:
        config = load_config()
        users = config.get("users", [])
        if not users:
            print("  No users subscribed. Skipping.")
            return
        print(f"  {len(users)} user(s) subscribed.")
        send_daily_matches(config)
    except Exception as exc:
        print(f"  [ERROR] Job failed: {exc}")
    print("  Job complete.\n")


def test_now():
    """Send emails immediately for all subscribed users (test mode)."""
    print("ShiftMatch Email — TEST MODE (sending now)\n")
    try:
        config = load_config()
    except FileNotFoundError as e:
        print(f"[ERROR] {e}")
        print("Create email_config.json from email_config.example.json first.")
        return

    users = config.get("users", [])
    smtp = config.get("smtp", {})

    print(f"  Config: {CONFIG_PATH}")
    print(f"  Users subscribed: {len(users)}")
    print(f"  SMTP host: {smtp.get('host', '(not set)')}")
    print(f"  SMTP user: {smtp.get('username', '(not set)')}")
    smtp_pw = smtp.get("password", "")
    print(f"  SMTP pass: {'****' + smtp_pw[-4:] if len(smtp_pw) > 4 else '(not set)'}")
    print()

    if not users:
        print("[WARN] No users in email_config.json. Nothing to send.")
        return

    if not smtp.get("username") or not smtp.get("password"):
        print("[WARN] SMTP credentials not configured. Fill in smtp.username and smtp.password in email_config.json.")
        print("       For Gmail, use an App Password: https://myaccount.google.com/apppasswords")
        return

    job()
    print("Test complete.")


def main():
    try:
        config = load_config()
    except FileNotFoundError as e:
        print(f"[ERROR] {e}")
        print("Create email_config.json from email_config.example.json first.")
        return

    schedule_time = config.get("schedule_time", "20:01")
    users = config.get("users", [])

    print(f"ShiftMatch Daily Email Scheduler")
    print(f"  Schedule: every day at {schedule_time}")
    print(f"  Users subscribed: {len(users)}")
    print(f"  Config: {CONFIG_PATH}")
    print(f"  Press Ctrl+C to stop.\n")

    schedule.every().day.at(schedule_time).do(job)

    while True:
        schedule.run_pending()
        time.sleep(30)


if __name__ == "__main__":
    if "--test" in sys.argv:
        test_now()
    else:
        main()
