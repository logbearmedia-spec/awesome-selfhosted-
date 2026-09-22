"""Command line entry point."""

from __future__ import annotations

import argparse
import sys
from datetime import date as Date
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from . import captions, imagegen, pipeline, planner, safety, storage
from .config import Config, ConfigError, load
from .publisher import InstagramPublisher, PublishError
from .state import State

DEFAULT_CONFIG = "config/persona.toml"


def _load(args: argparse.Namespace) -> tuple[Config, State]:
    cfg = load(args.config)
    state = State.load(cfg.state_path)
    return cfg, state


def _today(cfg: Config) -> Date:
    return datetime.now(timezone.utc).astimezone(ZoneInfo(cfg.schedule.timezone)).date()


def cmd_plan(args: argparse.Namespace) -> int:
    cfg, state = _load(args)
    start = Date.fromisoformat(args.date) if args.date else _today(cfg)
    for plan in planner.plan_range(cfg, state, start, args.days):
        marker = "posted" if state.has_slot(plan.date, plan.slot) else "queued"
        print(f"{plan.date} slot {plan.slot} [{marker}] {plan.pillar} / {plan.theme}")
        print(f"  scene:   {plan.scene}")
        print(f"  prompt:  {plan.image_prompt}")
        print(f"  tags:    {' '.join(plan.hashtags)}")
        print()
    return 0


def _report(result: pipeline.RunResult) -> None:
    if result.status == "skipped":
        print(f"skipped: {result.reason}")
        return
    plan = result.plan
    assert plan is not None
    print(f"{result.status}: {plan.date} slot {plan.slot} ({plan.pillar} / {plan.theme})")
    if result.local_path:
        print(f"  image:   {result.local_path}")
    print(f"  url:     {result.media_url}")
    if result.caption:
        print(f"  writer:  {result.caption.writer}")
        indented = "\n".join(f"  | {line}" for line in result.caption.text.splitlines())
        print(indented)
    if result.record and result.record.permalink:
        print(f"  link:    {result.record.permalink}")


def cmd_run(args: argparse.Namespace) -> int:
    cfg, state = _load(args)
    today = _today(cfg)

    if args.slot is not None:
        day = Date.fromisoformat(args.date) if args.date else today
        targets = [(day, args.slot)]
    elif args.date:
        day = Date.fromisoformat(args.date)
        targets = [(day, slot) for slot in range(len(cfg.schedule.times))]
    else:
        now = datetime.now(timezone.utc)
        due = planner.slots_for(cfg, now)
        if not due:
            print("nothing due right now")
            return 0
        targets = [(today, due[0])]

    failures = 0
    for day, slot in targets:
        try:
            result = pipeline.run_slot(
                cfg,
                state,
                day,
                slot,
                dry_run=args.dry_run,
                force=args.force,
                skip_publish=args.no_publish,
            )
            _report(result)
        except (
            safety.SafetyError,
            imagegen.ImageError,
            storage.StorageError,
            captions.CaptionError,
            PublishError,
            ConfigError,
        ) as exc:
            print(f"error on {day} slot {slot}: {exc}", file=sys.stderr)
            failures += 1
    return 1 if failures else 0


def cmd_status(args: argparse.Namespace) -> int:
    cfg, state = _load(args)
    live = [p for p in state.posts if not p.dry_run]
    print(f"persona:   {cfg.persona.name} (@{cfg.persona.handle})")
    print(f"config:    {cfg.source}")
    print(f"state:     {cfg.state_path}")
    print(f"published: {len(live)}  (plus {len(state.posts) - len(live)} dry runs)")
    print(f"schedule:  {', '.join(cfg.schedule.times)} {cfg.schedule.timezone}")
    print(f"today:     {len(state.posts_on(_today(cfg).isoformat()))} post(s) recorded")
    recent = state.recent(5)
    if recent:
        print("\nmost recent:")
        for post in reversed(recent):
            tag = "dry" if post.dry_run else "live"
            link = post.permalink or post.media_url
            print(f"  {post.date} slot {post.slot} [{tag}] {post.pillar}/{post.theme} {link}")
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    cfg, state = _load(args)
    problems = 0

    print(f"config parsed: {cfg.source}")
    print(f"pillars:       {len(cfg.persona.pillars)}")
    scenes = sum(len(p.scenes) for p in cfg.persona.pillars)
    print(f"scenes:        {scenes}")
    if scenes < len(cfg.schedule.times) * 14:
        print("  note: fewer than two weeks of distinct scenes; add more to keep the "
              "feed from repeating itself")

    if cfg.missing_env:
        print(f"unset env vars: {', '.join(cfg.missing_env)}")

    reference = imagegen.reference_path(cfg)
    if reference is None:
        print("reference image: not configured (the character will drift between posts)")
    elif reference.is_file():
        print(f"reference image: {reference} ({reference.stat().st_size} bytes)")
    else:
        print(f"reference image: MISSING at {reference}")
        problems += 1

    if cfg.storage.provider == "local" and not cfg.storage.public_base_url:
        print("storage: local with no public_base_url — dry runs work, publishing "
              "will not (Instagram fetches media over HTTP)")
        problems += 1

    if not cfg.safety.require_disclosure:
        print("safety: AI disclosure is disabled in config, but the pipeline enforces "
              "it on every caption regardless")

    if args.check_instagram:
        try:
            publisher = InstagramPublisher(cfg)
            used = publisher.quota_usage()
            print(f"instagram: reachable, {used}/50 posts used in the last 24h")
        except (ConfigError, PublishError) as exc:
            print(f"instagram: {exc}")
            problems += 1

    print(f"\n{'problems found: ' + str(problems) if problems else 'all checks passed'}")
    return 1 if problems else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="influencer",
        description="Run an Instagram page for an AI character.",
    )
    parser.add_argument(
        "-c", "--config", default=DEFAULT_CONFIG, help=f"config file (default: {DEFAULT_CONFIG})"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    plan_parser = subparsers.add_parser("plan", help="show upcoming posts without making them")
    plan_parser.add_argument("--days", type=int, default=7)
    plan_parser.add_argument("--date", help="start date, YYYY-MM-DD")
    plan_parser.set_defaults(func=cmd_plan)

    run_parser = subparsers.add_parser("run", help="produce and publish due posts")
    run_parser.add_argument("--date", help="YYYY-MM-DD; defaults to today")
    run_parser.add_argument("--slot", type=int, help="slot index within the day")
    run_parser.add_argument("--dry-run", action="store_true", help="render but never call Instagram")
    run_parser.add_argument(
        "--no-publish", action="store_true", help="render for real, but do not publish"
    )
    run_parser.add_argument("--force", action="store_true", help="re-run an already posted slot")
    run_parser.set_defaults(func=cmd_run)

    status_parser = subparsers.add_parser("status", help="summarise the account's history")
    status_parser.set_defaults(func=cmd_status)

    doctor_parser = subparsers.add_parser("doctor", help="check the setup")
    doctor_parser.add_argument(
        "--check-instagram", action="store_true", help="also call the Graph API"
    )
    doctor_parser.set_defaults(func=cmd_doctor)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2
    except (planner.PlannerError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
