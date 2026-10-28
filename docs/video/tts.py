"""Speak every scene of narration.json with Kokoro (voice `am_michael`) to
<out>/<id>.wav. `scripts/video.py --voice` runs it and normalises the result.

It runs under its OWN interpreter, not the repo's: Kokoro needs `mlx-audio`
(Apple silicon) and `misaki[en]`, which have no business in runcoach's
dependencies. Point `RUNCOACH_TTS_PYTHON` at that interpreter.

    $RUNCOACH_TTS_PYTHON docs/video/tts.py docs/video/narration.json <out>

Two pitfalls, both measured: espeak-ng cuts its data path at 160 characters,
so a venv deep in a temp tree needs its data copied somewhere short and named
in `RUNCOACH_ESPEAK_DATA` (a symlink does not help, the path is resolved); and
`preshed`/`cymem`/`murmurhash` must be the classic releases
(3.0.9 / 2.0.8 / 1.0.10) or spaCy's English model fails to load on 3.12.
"""
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

import misaki.espeak  # noqa: F401  (must be imported before the data path is set)
from misaki.espeak import EspeakWrapper

if os.environ.get("RUNCOACH_ESPEAK_DATA"):
    EspeakWrapper.set_data_path(os.environ["RUNCOACH_ESPEAK_DATA"])

from mlx_audio.tts.generate import generate_audio  # noqa: E402

MODEL = "mlx-community/Kokoro-82M-bf16"
VOICE = "am_michael"

scenes = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
only = set(sys.argv[3:])            # optional scene ids: re-speak just those
out = Path(sys.argv[2])
out.mkdir(parents=True, exist_ok=True)
for s in scenes:
    if only and s["id"] not in only:
        continue
    with tempfile.TemporaryDirectory() as tmp:
        cwd = os.getcwd()
        os.chdir(tmp)               # generate_audio writes into the working directory
        try:
            generate_audio(text=s["say"], model=MODEL, voice=VOICE, speed=1.0, lang_code="a",
                           file_prefix=s["id"], audio_format="wav", join_audio=True, verbose=False)
        finally:
            os.chdir(cwd)
        shutil.move(str(Path(tmp) / f"{s['id']}.wav"), out / f"{s['id']}.wav")
    print(s["id"], "ok")
