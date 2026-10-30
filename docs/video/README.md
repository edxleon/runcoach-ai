# The demo video

`runspector-demo.mp4` (89 s, 1080p, English voice-over; `--captions` burns in subtitles) is built from this folder by
`uv run python scripts/video.py`. That script copies in the app's screenshots, writes `index.html`
with `build.py`, and renders it with [HyperFrames](https://hyperframes.heygen.com). The HyperFrames
version is pinned and needs Node, ffmpeg and ffprobe.

- `narration.json`: one entry per scene. `say` is the text Kokoro speaks, spelled for the voice
  ("V O two max"). `show` is the caption text (only with `--captions`), whose timing also anchors every reveal. The length of each voice clip sets the length of its scene.
- `assets/audio/`: the spoken clips, normalised to -18 LUFS each. `--voice` re-speaks them through
  `tts.py`, which needs its own interpreter (`RUNSPECTOR_TTS_PYTHON`).
- `assets/captured.json`: tool output of the synthetic demo athlete, re-captured with `--capture`.
  The demo has no Garmin account, so the two "On Garmin" lines follow the format a real apply printed
  on 2026-10-28, and the frame says so.
- `assets/fonts/`: Inter and JetBrains Mono under the SIL Open Font License (texts beside them),
  bundled so that preview and render use the same glyphs.

Everything on screen is the demo athlete. No real health data, and nothing from a real account.
