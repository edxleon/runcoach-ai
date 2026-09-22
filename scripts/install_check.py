"""Install runcoach the way a user does, then use it the way a user does.

The test suite runs `uv run pytest` from the source tree, which proves the
code and nothing about the package: a data file missing from the wheel, an
entry point that does not resolve, a prompt that tracebacks without a
terminal - none of that is visible from inside the checkout. This script is
the other half. It builds the wheel, installs it with `uv tool install` into
a throw-away tool directory, and then drives the installed executable through
the README's quick start against an empty RUNCOACH_HOME:

    --version · serve --demo (HTTP from outside) · doctor · sync · login with
    no terminal · the MCP handshake over stdio (tools/list)

Standard library only, so it runs under whatever `python` the runner has;
the package's own interpreter is the one uv installs for the tool. CI runs it
on Linux, macOS and Windows - the three machines this repository's author
does not have in front of them.

    python scripts/install_check.py            # build + install + check
    python scripts/install_check.py --no-build # check whatever `runcoach` is on PATH
"""
from __future__ import annotations

import http.client
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXPECTED_TOOLS = 13   # 11 read-only + sync_garmin + apply_workout
failures: list[str] = []


def check(cond: bool, what: str, detail: str = "") -> None:
    print(f"  [{'ok' if cond else '!!'}] {what}" + (f"\n       {detail}" if detail and not cond else ""))
    if not cond:
        failures.append(what)


def run(cmd: list[str], *, env: dict, stdin=None, timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, env=env, stdin=stdin, capture_output=True, text=True,
                          timeout=timeout, encoding="utf-8", errors="replace")


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def install(tool_dir: Path, bin_dir: Path) -> Path:
    print("== build the wheel")
    shutil.rmtree(ROOT / "dist", ignore_errors=True)
    out = run(["uv", "build", "--wheel", "--quiet"], env=os.environ.copy())
    check(out.returncode == 0, "uv build --wheel", out.stderr[-400:])
    wheels = sorted((ROOT / "dist").glob("*.whl"))
    check(len(wheels) == 1, "exactly one wheel built", str(wheels))
    print(f"== uv tool install {wheels[0].name} (into a throw-away tool dir)")
    env = os.environ | {"UV_TOOL_DIR": str(tool_dir), "UV_TOOL_BIN_DIR": str(bin_dir)}
    out = run(["uv", "tool", "install", "--quiet", str(wheels[0])], env=env)
    check(out.returncode == 0, "uv tool install", out.stderr[-400:])
    exe = bin_dir / ("runcoach.exe" if os.name == "nt" else "runcoach")
    check(exe.is_file(), f"executable at {exe}")
    return exe


def main() -> int:
    build = "--no-build" not in sys.argv
    # Windows holds a lock on a `.pyd` for a moment after the process that
    # loaded it exits; the checks are done by then and the directory is a
    # throw-away, so a failed delete is not a finding.
    with tempfile.TemporaryDirectory(prefix="runcoach-install-", ignore_cleanup_errors=True) as tmp:
        tmp_p = Path(tmp)
        home = tmp_p / "home"
        if build:
            exe = install(tmp_p / "tools", tmp_p / "bin")
        else:
            found = shutil.which("runcoach")
            check(found is not None, "runcoach on PATH")
            exe = Path(found or "runcoach")
        if failures:
            return report()
        env = os.environ | {"RUNCOACH_HOME": str(home), "PYTHONUTF8": "1"}
        rc = lambda *args, **kw: run([str(exe), *args], env=env, **kw)  # noqa: E731

        print("== runcoach --version")
        out = rc("--version")
        check(out.returncode == 0 and out.stdout.strip(), "prints a version", out.stderr[-300:])

        print("== runcoach serve --demo (HTTP from outside the process)")
        port = free_port()
        proc = subprocess.Popen([str(exe), "serve", "--demo", "--port", str(port), "--no-browser"],
                                env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        try:
            state = None
            for _ in range(80):
                time.sleep(0.25)
                try:
                    c = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
                    c.request("GET", "/api/state")
                    r = c.getresponse()
                    if r.status == 200:
                        state = json.loads(r.read())
                        break
                except OSError:
                    continue
            check(state is not None, "demo answers on the port it was given")
            if state:
                check(state.get("demo") is True and len(state.get("runs", [])) >= 30,
                      "demo state has the synthetic athlete", json.dumps(state)[:200])
                check(state.get("today", {}).get("verdict") in {"GO", "EASY", "REST"},
                      "a verdict is computed")
                check({"today", "runs", "vo2max", "zones", "aerobic"} <= set(state),
                      "every tab's data block is present")
                # The one data file the app survives losing WITHOUT a word:
                # `templates()` returns [] when templates.json is not in the
                # wheel, and the Coach tab simply has no buttons. Checking that
                # the key exists caught nothing; the count is what a user sees.
                ids = {t.get("id") for t in state.get("templates", [])}
                check(len(ids) == 6 and {"train-today", "analyze-run", "plan-session", "plan-week"} <= ids,
                      "the six coach templates shipped inside the wheel", str(sorted(ids)))
        finally:
            proc.terminate()
            try:
                proc.wait(10)
            except subprocess.TimeoutExpired:
                proc.kill()
        check((home / "demo").is_dir(), "demo data lives under <home>/demo, not in the real home")

        print("== runcoach doctor on an empty home")
        out = rc("doctor", "--offline")
        check(out.returncode == 1, "exit 1 (something is missing)", f"exit={out.returncode}")
        check("runcoach login" in out.stdout and "runcoach sync" in out.stdout,
              "names the two commands that fix it", out.stdout[-400:])

        print("== runcoach sync with no session")
        out = rc("sync")
        check(out.returncode == 2, "exit 2 (Garmin stopped us)", f"exit={out.returncode} {out.stderr[-200:]}")
        check("runcoach login" in out.stderr + out.stdout, "points at `runcoach login`")

        print("== runcoach login with no terminal")
        out = rc("login", stdin=subprocess.DEVNULL, timeout=60)
        check(out.returncode == 1, "exit 1", f"exit={out.returncode}")
        check("Traceback" not in out.stderr, "no traceback", out.stderr[-400:])
        check("interactive" in out.stderr, "says it needs a terminal", out.stderr[-300:])

        print("== runcoach mcp: initialize + tools/list over stdio")
        msgs = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                        "clientInfo": {"name": "install_check", "version": "0"}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        ]
        try:
            out = subprocess.run(
                [str(exe), "mcp"], env=env, input="\n".join(json.dumps(m) for m in msgs) + "\n",
                capture_output=True, text=True, timeout=60, encoding="utf-8", errors="replace")
        except subprocess.TimeoutExpired:
            out = None
        tools: list[str] = []
        if out is not None:
            for line in out.stdout.splitlines():
                try:
                    m = json.loads(line)
                except ValueError:
                    continue
                if m.get("id") == 2:
                    tools = [t["name"] for t in m["result"]["tools"]]
        check(out is not None, "the server answers and exits when stdin closes")
        check(len(tools) == EXPECTED_TOOLS, f"tools/list returns {EXPECTED_TOOLS} tools",
              f"{len(tools)}: {tools}")
        check("sync_garmin" in tools and "get_training_readiness" in tools,
              "the sync tool and the readiness tool are among them")
    return report()


def report() -> int:
    print()
    if failures:
        print(f"{len(failures)} check(s) failed:\n  - " + "\n  - ".join(failures))
        return 1
    print("install check: clean")
    return 0


if __name__ == "__main__":
    sys.exit(main())
