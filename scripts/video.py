"""Regenerate the README demo video, docs/video/runcoach-demo.mp4.

A picture nobody can regenerate is documentation that can only rot, and a
video is a picture that also talks. Everything in it comes from the repo: the
screenshots `scripts/screenshots.py` takes of the synthetic demo athlete, the
coach card stored next to them, tool texts captured from that same athlete,
and a voice spoken from `docs/video/narration.json`.

    uv run python scripts/video.py               # compose + render from what is committed
    uv run python scripts/video.py --capture     # re-capture the tool texts first
    uv run python scripts/video.py --voice       # re-speak the narration first (needs Kokoro)
    uv run python scripts/video.py --voice hook  # ...or just the scenes named

Needs ffmpeg/ffprobe and Node (HyperFrames renders through `npx`, version
pinned below). `--voice` also needs `RUNCOACH_TTS_PYTHON`, see docs/video/tts.py.
The demo athlete has no Garmin account, so `apply_workout` refuses in demo
mode: the two "On Garmin" lines of the chat scene are written in the exact
format a real apply printed on 2026-10-28, and the frame says so.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

VIDEO = ROOT / "docs" / "video"
ASSETS = VIDEO / "assets"
SHOTS = ROOT / "docs" / "screenshots"
OUT = VIDEO / "runcoach-demo.mp4"
POSTER = VIDEO / "poster.png"
#: Pinned: HyperFrames renders deterministically only against itself.
HYPERFRAMES = "hyperframes@0.8.181"
#: The poster is the coach card's answer - the frame that says what the app is.
POSTER_AT_S = 17.5


def run(cmd: list[str], **kw) -> None:
    print("  $", " ".join(cmd[:4]), "..." if len(cmd) > 4 else "")
    subprocess.run(cmd, check=True, **kw)


def need(tool: str) -> str:
    found = shutil.which(tool)
    if not found:
        sys.exit(f"{tool} is not on PATH - the video needs it")
    return found


def copy_app_pictures() -> None:
    """The app's own screenshots and coach card, copied in: HyperFrames serves
    the video folder and nothing above it."""
    for name in ("today.png", "runs.png", "trend.png", "coach.png", "demo-card.json"):
        shutil.copy2(SHOTS / name, ASSETS / name)


def capture() -> None:
    """Tool texts from the demo athlete, on the coach card's frozen day."""
    import screenshots
    from runcoach import demo, tools

    day = screenshots.card_day()
    screenshots.freeze_today(day)
    home = Path(tempfile.mkdtemp(prefix="runcoach-video-"))
    os.environ["RUNCOACH_HOME"], os.environ["RUNCOACH_DEMO"] = str(home), "1"
    try:
        store = demo.seed()
        tomorrow = (day + timedelta(days=1)).isoformat()
        prop = tools.propose_workout(store, "easy", None, 40, tomorrow, None, None)
    finally:
        shutil.rmtree(home, ignore_errors=True)
    name = prop.splitlines()[0].split("  (")[0]
    (ASSETS / "captured.json").write_text(json.dumps({
        "day": tomorrow,
        "propose": prop,
        "apply": [f'On Garmin: "{name}" (workout …) scheduled for {tomorrow}, pushed to the watch.',
                  "read back from Garmin and verified: structure and targets as proposed."],
        "undo": "Taken off the calendar: 1 session(s).",
    }, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")


def voice(only: list[str]) -> None:
    """Speak, then normalise: -18 LUFS per clip lands the whole film near -16."""
    py = os.environ.get("RUNCOACH_TTS_PYTHON")
    if not py:
        sys.exit("--voice needs RUNCOACH_TTS_PYTHON (an interpreter with mlx-audio + misaki[en])")
    with tempfile.TemporaryDirectory() as raw:
        run([py, str(VIDEO / "tts.py"), str(VIDEO / "narration.json"), raw, *only])
        for wav in Path(raw).glob("*.wav"):
            run([need("ffmpeg"), "-hide_banner", "-loglevel", "error", "-y", "-i", str(wav),
                 "-af", "loudnorm=I=-18:TP=-2:LRA=11", "-ar", "24000", "-ac", "1",
                 "-c:a", "libmp3lame", "-b:a", "96k", str(ASSETS / "audio" / f"{wav.stem}.mp3")])


def main() -> int:
    need("ffprobe")
    npx = need("npx")
    args = sys.argv[1:]
    copy_app_pictures()
    if "--capture" in args:
        capture()
    if "--voice" in args:
        voice([a for a in args[args.index("--voice") + 1:] if not a.startswith("--")])
    run([sys.executable, str(VIDEO / "build.py")])
    env = {**os.environ, "HYPERFRAMES_SKIP_SKILLS": "1"}
    run([npx, "-y", HYPERFRAMES, "telemetry", "disable"], cwd=VIDEO, env=env, stdout=subprocess.DEVNULL)
    run([npx, "-y", HYPERFRAMES, "check"], cwd=VIDEO, env=env)
    run([npx, "-y", HYPERFRAMES, "render", "-o", str(OUT), "-f", "30", "-q", "standard"],
        cwd=VIDEO, env=env)
    run([need("ffmpeg"), "-hide_banner", "-loglevel", "error", "-y", "-ss", str(POSTER_AT_S),
         "-i", str(OUT), "-frames:v", "1", "-vf", "scale=1280:-1", str(POSTER)])
    shutil.rmtree(VIDEO / ".hyperframes", ignore_errors=True)   # renderer state: local paths
    print(f"written {OUT.relative_to(ROOT)} and {POSTER.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
