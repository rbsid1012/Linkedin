from __future__ import annotations

import argparse
import logging
import sys

from .config import ConfigError, check_resumes, load_config
from .tracker import Tracker


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="linkedin_applier", description=__doc__)
    ap.add_argument("-c", "--config", default="config.yaml")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("login", help="open a browser to sign in once (session is kept locally)")
    r = sub.add_parser("run", help="search and (optionally) apply")
    r.add_argument("--mode", choices=["scan", "rehearse", "apply"], default="scan",
                   help="scan: list matches only | rehearse: fill forms, never submit | apply: submit")
    r.add_argument("--profile", help="only run this profile")
    r.add_argument("--interactive", action="store_true", help="with unknown_question_policy=ask, prompt in terminal")
    sub.add_parser("report", help="show counts and the questions it couldn't answer")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    try:
        cfg = load_config(args.config)
    except ConfigError as e:
        print(f"config error: {e}", file=sys.stderr)
        return 2
    tracker = Tracker(cfg.settings.db_path)

    if args.cmd == "report":
        for prof, status, n in tracker.stats():
            print(f"{prof or '-':20} {status:14} {n}")
        qs = tracker.unanswered_summary()
        if qs:
            print("\nQuestions that blocked applications (add them to candidate.answers):")
            for q, n in qs:
                print(f"  {n:3}x  {q}")
        return 0

    if args.cmd == "login":
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            ctx = p.chromium.launch_persistent_context(str(cfg.settings.user_data_dir), headless=False)
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.goto("https://www.linkedin.com/login")
            input("Sign in in the browser window (handle any 2FA yourself), then press Enter here...")
            ctx.close()
        return 0

    problems = check_resumes(cfg) if args.mode != "scan" else []
    if problems:
        print("\n".join(problems), file=sys.stderr)
        return 2
    from .linkedin import run

    run(cfg, tracker, args.mode, args.profile, args.interactive)
    return 0
