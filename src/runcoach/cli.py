"""`runcoach` command line."""

from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import subprocess
import sys

from . import __version__, paths, planning, snapshot


def _utf8() -> None:
    """UTF-8 and LINE buffering. Without `line_buffering` nothing appears until the
    process exits once stdout is a file rather than a terminal — so `runcoach serve`
    under a service manager, nohup or Task Scheduler wrote a zero-byte log and gave
    no way to tell whether it had even come up."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
        except (AttributeError, ValueError):
            pass


def cmd_login(args) -> int:
    from . import auth

    return auth.verify() if args.verify else auth.interactive_login()


def cmd_sync(args) -> int:
    """Exit 0 fine, 2 Garmin stopped us (expired session, rate limit, no route),
    3 partial. Telling those apart matters: every failure used to be reported as a
    login failure, which sends the athlete into an interactive password-and-MFA
    re-login that cannot fix a rate limit."""
    from . import garmin, sync

    try:
        return sync.sync_now(args.days).exit_code
    except Exception as exc:  # noqa: BLE001 — a CLI prints a cause, it does not traceback
        name = type(exc).__name__
        if "Auth" in name or "login" in name.lower():
            hint = "The stored Garmin session is no longer valid. Run `runcoach login`."
        elif garmin.is_fatal(exc):
            hint = ("Garmin refused the request (rate limit or connection). Nothing was lost - "
                    "the sync is idempotent, so just run it again later.")
        else:
            hint = "Re-run with RUNCOACH_LOG=INFO for the full context."
        print(f"Sync failed: {name}: {exc}\n{hint}", file=sys.stderr)
        return 2


def cmd_serve(args) -> int:
    from .web import server

    return server.serve(host=args.host, port=args.port, demo=args.demo,
                        open_browser=not args.no_browser, sync_on_start=not args.no_sync)


def cmd_mcp(_args) -> int:
    from . import mcp_server

    mcp_server.main()
    return 0


def cmd_profile(args) -> int:
    path = paths.profile_path()
    try:
        profile = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        profile = {}
    for key in ("max_hr", "aerobic_ref_hr", "goal", "days_per_week", "long_run_day"):
        value = getattr(args, key)
        if value is not None:
            profile[key] = value
    path.write_text(json.dumps(profile, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(profile, indent=2, ensure_ascii=False))
    return 0


#: A sync that has not produced a new day in this long is treated as broken, not
#: as a quiet week: the watch writes a row for every day it is worn.
#: Imported, not re-declared: the web app shows its staleness banner at the same
#: number (via `snapshot.assemble`'s `stale_after_days`), and that banner points
#: the athlete at `runcoach doctor` to explain itself. Two literals meant doctor
#: answered `[ok]` at the very age that raised the banner.
STALE_AFTER_DAYS = snapshot.STALE_AFTER_DAYS


def cmd_doctor(args) -> int:
    """Everything a first run needs, checked in the order it fails in practice —
    and written so that `runcoach doctor || alert` is worth putting in a cron.

    It used to answer "all green, exit 0" for an installation whose nightly sync
    had died months earlier: it checked that a token FILE existed, never that the
    session still worked, and that the database had EVER held data, never that the
    data was recent. Token expiry freezing the data is the failure mode of this
    design, so those are exactly the two things it now tests."""
    from .store import Store

    ok = True

    def check(label: str, good: bool, hint: str = "", *, fatal: bool = True) -> None:
        """`fatal=False` reports without touching the exit code. The claude CLI is
        optional - the app reads, charts and syncs without it, only the coach
        cards need it - and a cron running `runcoach doctor || alert` should page
        on a dead Garmin session, not on a machine that never had Claude Code."""
        nonlocal ok
        if fatal:
            ok &= good
        print(f"  [{'ok' if good else '!!'}] {label}"
              + (f"\n       -> {hint}" if not good and hint else ""))

    # The home directory is the first thing that can be broken, and every check
    # below it depends on it. Unreadable, a read-only volume or a stale NFS mount
    # came out as a traceback - which is the one output shape a diagnostic command
    # must never produce, because it looks like the tool is the bug.
    #
    # The BANNER is inside the guard too. `paths.home()` does the `mkdir`, so
    # printing "data in {paths.home()}" first put the very call that fails ahead
    # of the try - and `RUNCOACH_HOME` pointing at an existing FILE still ended in
    # a raw `FileExistsError`, in the version whose comment said it would not.
    print(f"runcoach {__version__}")
    try:
        home = paths.home()
        print(f"  data in {home}")
        probe = home / ".doctor-probe"
        probe.write_text("x", encoding="utf-8")
        probe.unlink()
        check(f"data directory writable ({home})", True)
    except OSError as exc:
        where = os.environ.get("RUNCOACH_HOME") or "~/.runcoach"
        check(f"data directory unusable ({where}: {exc.strerror or exc})", False,
              "point RUNCOACH_HOME at a writable directory, or fix the permissions")
        return 1
    if os.name != "nt":
        mode = home.stat().st_mode & 0o777
        # Sleep, HRV, resting heart rate and the Garmin session tokens live here.
        # `paths.home()` only chmods on CREATION, so every install made before
        # that landed still carries the umask default - and nothing said so.
        check(f"data directory mode {mode:o}", mode & 0o077 == 0,
              f"other accounts on this machine can read your health data and Garmin "
              f"session: chmod 700 {home}", fatal=False)

    tz = os.environ.get("RUNCOACH_TZ")
    if tz:
        try:
            paths.local_tz()
            check(f"RUNCOACH_TZ={tz}", True)
        except Exception:  # noqa: BLE001 — any zoneinfo failure is the same answer
            check(f"RUNCOACH_TZ={tz!r} is not a known IANA zone", False,
                  "e.g. Europe/Berlin, America/New_York - or unset it for the system zone")

    claude = shutil.which("claude")
    check("claude CLI on PATH (coach cards run on your Claude subscription)", bool(claude),
          "install Claude Code and sign in: https://claude.com/claude-code", fatal=False)
    if claude:
        try:
            out = subprocess.run([claude, "--version"], capture_output=True, text=True, timeout=20)
            check(f"claude --version: {out.stdout.strip() or out.stderr.strip()}",
                  out.returncode == 0, fatal=False)
        except Exception as exc:  # noqa: BLE001 — reported as a failed check, not a traceback
            check(f"claude --version ({type(exc).__name__})", False, fatal=False)

    # The app strips these before spawning the agent, so that the README's "your
    # subscription, not an API key" holds. Without a word here that is a trap
    # with no way out: the CLI works in the user's own terminal, every coach
    # card fails in the app, and doctor said [ok] twice.
    from .web.agent import strips_billing

    stripped = sorted(n for n in os.environ if strips_billing(n))
    if stripped:
        check(f"billing variables in the environment: {', '.join(stripped)}", False,
              "coach cards deliberately run on your Claude SUBSCRIPTION, so these are "
              "removed before the agent starts. If you meant to bill through an API "
              "key or Bedrock/Vertex, this app is not set up for it; unset them to "
              "silence this check.", fatal=False)

    # The write path, which `doctor` used not to know existed at all: a package
    # that stopped half way, or a claim whose upload never answered, is exactly
    # the state a runner cannot see and cannot reason about. It is reported, not
    # failed - nothing here is broken, it is a question for the athlete.
    from . import plan

    try:
        store_for_plan = Store()
        unresolved = store_for_plan.unresolved_claims()
        open_n = len(plan.open_proposals(store_for_plan))
        half = [p for p in (plan.read(f.stem, store_for_plan)
                            for f in paths.proposals_dir().glob("p-*.json"))
                if p and p.get("status") == "applied" and plan.pending_of(p)]
        # From the DATABASE, not from a file: a proposal swept after the TTL
        # takes the only file-side trace of a half-written session with it, and
        # an uploaded workout that never got a day is the one the athlete will
        # not find on the watch.
        orphans = [w for w in store_for_plan.own_workouts() if not w["schedule_id"]]
    except Exception as exc:  # noqa: BLE001 — a diagnostic never tracebacks
        check(f"proposals unreadable ({type(exc).__name__})", False, fatal=False)
    else:
        check(f"proposals: {open_n} waiting for a yes, {len(half)} applied only in part",
              not half,
              "apply them again to add what is missing - what is already on Garmin is not "
              "written twice: " + ", ".join(p["id"] for p in half[:3]), fatal=False)
        if orphans:
            check(f"{len(orphans)} uploaded workout(s) sit in your Garmin library "
                  f"without a day", False,
                  "schedule them in Garmin Connect, or propose the session again: "
                  + ", ".join(f"{w['name']} ({w['workout_id']})" for w in orphans[:3]),
                  fatal=False)
        if unresolved:
            check(f"{len(unresolved)} upload(s) never gave an answer", False,
                  "Garmin may or may not have created these - look in your Garmin library, "
                  "then propose them again if they are missing: "
                  + ", ".join(f"{u['proposal_id']}#{u['item_index']}" for u in unresolved[:3]),
                  fatal=False)

    # The week planner runs without these - on defaults it lists as assumptions
    # - so this is a hint, not a failure.
    profile = snapshot.load_profile()
    missing = [k for k in ("days_per_week", "long_run_day") if profile.get(k) is None]
    check("profile for the week planner"
          + ("" if missing else f": {profile['days_per_week']} days, long run {profile['long_run_day']}"),
          not missing,
          f"weekly plans use defaults ({planning.DEFAULT_DAYS_PER_WEEK} days, long run "
          f"{planning.DEFAULT_LONG_RUN_DAY}) until you set: runcoach profile "
          f"--days-per-week N --long-run-day mon..sun", fatal=False)

    if not paths.garmin_session_present():
        check("Garmin session", False, "no tokens stored - run `runcoach login`")
    elif args.offline:
        check("Garmin session tokens present (not verified: --offline)", True)
    else:
        from . import garmin

        try:
            garmin.login()
            check("Garmin session still valid", True)
        except Exception as exc:  # noqa: BLE001 — the point is to report, not to raise
            check(f"Garmin session rejected ({type(exc).__name__})", False,
                  "the stored session has expired - run `runcoach login`")

    try:
        store = Store()
        counts, latest = store.counts(), store.latest_day()
    except Exception as exc:  # noqa: BLE001 — a corrupt DB is a FINDING, not a crash
        check(f"database unreadable ({type(exc).__name__}: {exc})", False,
              f"the file may be corrupt: move {paths.db_path()} aside and run "
              f"`runcoach sync --days 30` to rebuild it")
        return 1
    if latest is None:
        check(f"database schema v{store.schema_version()} - empty", False,
              "run `runcoach sync --days 30` (or try `runcoach serve --demo`)")
    else:
        behind = (paths.today() - latest).days
        check(f"database schema v{store.schema_version()} - {counts['days']} days, "
              f"{counts['activities']} activities, newest day {latest} ({behind} day(s) behind)",
              # `<`, not `<=`. The page raises its banner at `stale_days >=
              # STALE_AFTER_DAYS` and tells the athlete to run this command to
              # explain it - so at EXACTLY the threshold, `<=` had doctor answer
              # `[ok]` to a question the page had just raised. Sharing the
              # constant fixed the number and left the comparison: the same
              # contradiction, moved from 2 days to 3.
              behind < STALE_AFTER_DAYS,
              "the data is stale - run `runcoach sync` and check that your scheduled "
              "sync still runs")
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    _utf8()
    # `.upper()` because `RUNCOACH_LOG=debug` is the natural spelling and used to
    # kill every subcommand with a raw `ValueError: Unknown level: 'debug'`.
    logging.basicConfig(level=(os.environ.get("RUNCOACH_LOG") or "WARNING").upper(),
                        format="%(asctime)s %(levelname)s %(name)s %(message)s", stream=sys.stderr)
    ap = argparse.ArgumentParser(prog="runcoach", description=__doc__,
                                 formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    ap.add_argument("--version", action="version", version=__version__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("login", help="one-time Garmin login (interactive, MFA)")
    p.add_argument("--verify", action="store_true", help="only test the stored session")
    p.set_defaults(fn=cmd_login)

    p = sub.add_parser("sync", help="pull the last N days from Garmin",
                       formatter_class=argparse.ArgumentDefaultsHelpFormatter,
                       description="Exit 0 fine, 2 Garmin stopped us, 3 partial.")
    p.add_argument("--days", type=int, default=3, help="how many days back to re-fetch")
    p.set_defaults(fn=cmd_sync)

    p = sub.add_parser("serve", help="start the web app and sync in the background",
                       formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    p.add_argument("--host", default="127.0.0.1",
                   help="anything but loopback requires RUNCOACH_TOKEN")
    p.add_argument("--port", type=int, default=8765, help="TCP port to listen on")
    p.add_argument("--demo", action="store_true", help="synthetic data, no Garmin account needed")
    p.add_argument("--no-browser", action="store_true", help="do not open a browser window")
    p.add_argument("--no-sync", action="store_true", help="skip the sync on start")
    p.set_defaults(fn=cmd_serve)

    p = sub.add_parser("mcp", help="stdio MCP server for Claude Code / Claude Desktop")
    p.set_defaults(fn=cmd_mcp)

    p = sub.add_parser("profile", help="show or set the optional athlete profile")
    p.add_argument("--max-hr", type=int, dest="max_hr", help="measured maximum heart rate")
    p.add_argument("--aerobic-ref-hr", type=int, dest="aerobic_ref_hr",
                   help="reference HR for the aerobic-efficiency card")
    p.add_argument("--goal", help="free text, shown on the Today tab")
    p.add_argument("--days-per-week", type=int, choices=range(3, 8), dest="days_per_week",
                   metavar="N", help="running days per week, for the week planner (3-7)")
    p.add_argument("--long-run-day", choices=planning.WEEKDAYS, dest="long_run_day",
                   help="weekday of the long run, for the week planner")
    p.set_defaults(fn=cmd_profile)

    p = sub.add_parser("doctor", help="check the installation (exit 1 = something is wrong)")
    p.add_argument("--offline", action="store_true",
                   help="skip the Garmin session check (no network)")
    p.set_defaults(fn=cmd_doctor)

    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
