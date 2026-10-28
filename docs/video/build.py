"""Write index.html (the HyperFrames composition) from narration.json, the
measured length of every voice clip and the captured tool texts. Run through
`scripts/video.py`, which first copies the app's screenshots and coach card in.

Timing comes from the audio, never the other way round: a scene lasts as long
as its sentence takes to say, plus a short lead and tail, and every caption and
every reveal inside a scene is placed on the estimated start of its sentence.

    python docs/video/build.py     # needs ffprobe on PATH
"""
from __future__ import annotations

import html
import json
import re
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
LEAD, TAIL, CLOSE_TAIL = 0.45, 0.55, 2.0


def duration(path: Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                          "-of", "csv=p=0", str(path)], capture_output=True, text=True, check=True)
    return float(out.stdout.strip())


def sentences(text: str) -> list[str]:
    parts = [p.strip() for p in re.split(r"(?<=[.?!\"])\s+(?=[A-Z\"0-9])", text) if p.strip()]
    merged: list[str] = []
    for p in parts:            # very short fragments ride along with the next one
        if merged and len(merged[-1]) < 28:
            merged[-1] = f"{merged[-1]} {p}"
        else:
            merged.append(p)
    return merged


def timeline() -> tuple[list[dict], float]:
    scenes = json.loads((HERE / "narration.json").read_text(encoding="utf-8"))
    t = 0.0
    for i, s in enumerate(scenes):
        s["audio"] = duration(HERE / "assets" / "audio" / f"{s['id']}.mp3")
        tail = CLOSE_TAIL if i == len(scenes) - 1 else TAIL
        s["start"], s["dur"] = round(t, 3), round(LEAD + s["audio"] + tail, 3)
        a0 = s["start"] + LEAD
        caps = sentences(s["show"])
        weights = [max(len(c), 8) for c in caps]
        total, acc, s["caps"] = sum(weights), 0.0, []
        for i, (c, w) in enumerate(zip(caps, weights, strict=True)):
            c0 = a0 + s["audio"] * acc / total
            acc += w
            c1 = a0 + s["audio"] * acc / total
            # Only the scene's last line lingers; earlier ones hand over exactly.
            hang = 0.25 if i == len(caps) - 1 else 0.0
            s["caps"].append({"text": c, "t0": round(c0, 3), "t1": round(c1 + hang, 3)})
        t += s["dur"]
    return scenes, round(t, 3)


def esc(s: str) -> str:
    return html.escape(s, quote=True)


def typed(cmd: str, cls: str = "cmd") -> str:
    return (f'<div class="line"><span class="prompt">$</span> '
            f'<span class="type {cls}" data-len="{len(cmd)}">{esc(cmd)}</span></div>')


def out(text: str, cls: str = "out") -> str:
    return f'<div class="line {cls}">{esc(text)}</div>'


def render(*, captions_on: bool = False) -> str:
    scenes, total = timeline()
    by_id = {s["id"]: s for s in scenes}
    cap = json.loads((HERE / "assets" / "captured.json").read_text(encoding="utf-8"))
    card = json.loads((HERE / "assets" / "demo-card.json").read_text(encoding="utf-8"))
    css = (HERE / "scenes.css").read_text(encoding="utf-8")

    def section(sid: str, body: str) -> str:
        s = by_id[sid]
        return (f'<section id="s-{sid}" class="clip scene" data-start="{s["start"]}" '
                f'data-duration="{s["dur"]}"><div class="inner">{body}</div></section>')

    hook = section("hook", """
      <div class="center-col">
        <h1 class="question"><span class="type q" data-len="21">Should I train today?</span></h1>
        <div class="datawall">
          <span class="dw d1">Sleep <b>7 h 43 min</b></span><span class="dw d2">HRV <b>BALANCED</b></span>
          <span class="dw d3">Body Battery <b>66</b></span><span class="dw d4">ACWR <b>1.17</b></span>
          <span class="dw d5">Resting HR <b>48</b></span><span class="dw d6">VO2max <b>53.6</b></span>
        </div>
        <div class="punch">All the data. No answer.</div>
      </div>""")

    b0 = card["bullets"][0]
    reason_a = b0.split(":")[0] + "."                       # "Recovery says GO, spacing says EASY."
    reason_b = "The brake is yesterday's session alone - not fatigue."
    assert "The brake is yesterday's session alone" in b0 and b0.rstrip(".").endswith("not fatigue"), b0
    answer = section("answer", f"""
      <div class="center-col">
        <div class="tap-btn">Should I train today?</div>
        <div class="card big-card">
          <div class="card-meta">Coach card · written by Claude from the app's own tools · demo athlete</div>
          <div class="card-head">{esc(card["headline"])}</div>
          <div class="card-verdict">{esc(card["verdict"])}</div>
          <div class="reason"><span class="ra">{esc(reason_a)}</span> <span class="rb">{esc(reason_b)}</span></div>
        </div>
      </div>""")

    today = section("today", """
      <div class="col-text">
        <div class="chips"><span class="chip go">GO</span><span class="chip easy">EASY</span><span class="chip rest">REST</span></div>
        <h2 class="t1">The verdict is readable code.</h2>
        <pre class="code t2"><span class="k">if</span> hrv_status <span class="k">in</span> (<span class="s">"LOW"</span>, <span class="s">"POOR"</span>):
    flags.append((<span class="s">"rest"</span>, <span class="s">"hrv"</span>, …))
<span class="k">if</span> body_battery_high &lt; <span class="n">40</span>:
    flags.append((<span class="s">"rest"</span>, <span class="s">"body_battery"</span>, …))
<span class="k">if</span> delta &gt;= <span class="n">7</span>:   <span class="c"># resting HR above baseline</span>
    flags.append((<span class="s">"rest"</span>, <span class="s">"resting_hr"</span>, …))</pre>
        <div class="path t2">src/runcoach/logic.py</div>
        <h2 class="t3">The AI explains it. It never invents it.</h2>
      </div>
      <div class="phone today-phone"><div class="vp" data-layout-allow-overflow><img src="assets/today.png" alt=""></div>
        <div class="ring ring-verdict"></div></div>""")

    strategy = section("strategy", """
      <div class="strat">
        <h2 class="s1">Coached for VO2max,<br>not for kilometres.</h2>
        <div class="s2 split">
          <div class="bar"><div class="seg easy-seg">80 % easy</div><div class="seg hard-seg">20 % hard</div></div>
        </div>
        <div class="s3 week">
          <div class="day">Mon</div><div class="day hard">Tue<small>threshold</small></div><div class="day">Wed</div>
          <div class="day hard">Thu<small>VO2max</small></div><div class="day">Fri</div><div class="day">Sat</div>
          <div class="day hard">Sun<small>long run</small></div>
          <div class="gap g1">48 h</div>
        </div>
        <div class="s4 doubt">When in doubt: the lighter session.</div>
      </div>""")

    def crop(img: str, x: int, y: int, w: int, h: int, s: float, cls: str) -> str:
        return (f'<div class="crop {cls}" data-layout-allow-overflow style="width:{w * s:.0f}px;height:{h * s:.0f}px">'
                f'<img src="assets/{img}" alt="" style="width:{520 * s:.0f}px;margin-left:{-x * s:.0f}px;'
                f'margin-top:{-y * s:.0f}px"></div>')

    runs = section("runs", f"""
      <div class="pair">
        <div class="panel p1"><div class="label">Runs</div><h3>The real structure of every session</h3>
          {crop("runs.png", 32, 144, 456, 162, 1.85, "c-runs")}</div>
        <div class="panel p2"><div class="label">Trend</div><h3>VO2max as steps: only real measurements</h3>
          {crop("trend.png", 34, 202, 452, 292, 1.5, "c-vo2")}</div>
      </div>""")

    prop = cap["propose"].splitlines()
    chat = section("chat", f"""
      <div class="term chat-term">
        <div class="bar"><i></i><i></i><i></i><span>Claude Code · runcoach MCP</span></div>
        <div class="body">
          <div class="blk h1"><div class="line user"><span class="prompt">&gt;</span> <span class="type ask" data-len="40">plan me an easy 40 minutes for tomorrow</span></div></div>
          <div class="blk h2">
            <div class="line tool">⏺ runcoach · propose_workout <span class="dim">(easy, 40 min, {esc(cap["day"])})</span></div>
            <div class="res">{esc(prop[0])}<br>{esc(prop[1])}<br>{esc(prop[2])}<br><span class="warn">NOT on Garmin yet.</span></div>
          </div>
          <div class="blk h3"><div class="line said">⏺ Here's the session: 40 minutes easy, heart rate 130–148. It is not on your watch yet. Shall I put it there?</div></div>
          <div class="blk h4"><div class="line user"><span class="prompt">&gt;</span> <span class="type yes" data-len="3">yes</span></div></div>
          <div class="blk h5">
            <div class="line tool">⏺ runcoach · apply_workout</div>
            <div class="res">{esc(cap["apply"][0])}<br><span class="okc">{esc(cap["apply"][1])}</span></div>
          </div>
        </div>
      </div>
      <div class="foot">Tool output of the demo athlete. The apply lines are as printed by a real run on a real watch, 2026-10-28.</div>""")

    watch = section("watch", f"""
      <div class="watch big-watch">
        <div class="bezel"><div class="screen">
          <div class="ws ws1"><div class="w-small">TOMORROW</div><div class="w-title">Easy run 40 min</div>
            <div class="w-big">40:00</div><div class="w-small">HR 130–148</div></div>
          <div class="ws ws2"><div class="w-small">CALENDAR</div><div class="w-title">Nothing scheduled</div></div>
        </div></div>
      </div>
      <div class="col-text watch-text">
        <div class="status w1"><span class="tick">✓</span> pushed to the watch</div>
        <div class="status w1b"><span class="tick">✓</span> read back and verified</div>
        <div class="line user w2"><span class="prompt">&gt;</span> take it back off</div>
        <div class="status w3 dim">{esc(cap["undo"])}</div>
      </div>""")

    try_ = section("try", f"""
      <div class="facts-row">
        <span class="f1">your own machine</span><span class="f2">your Claude subscription</span>
        <span class="f3">no API key</span><span class="f4">no cloud</span>
      </div>
      <div class="term try-term">
        <div class="bar"><i></i><i></i><i></i><span>Terminal</span></div>
        <div class="body">
          <div class="blk b1">{typed("uv tool install git+https://github.com/edxleon/runcoach-ai")}</div>
          <div class="blk b2">{typed("runcoach serve --demo")}
            {out("runcoach 0.2.0 (demo data) - http://127.0.0.1:8765/", "ok")}
            {out("data: ~/.runcoach/demo   stop with Ctrl+C")}</div>
        </div>
      </div>
      <div class="demo-note">a synthetic athlete · no Garmin account · nothing to sign up for</div>""")

    close = section("close", """
      <div class="close-box">
        <div class="logo">runcoach</div>
        <div class="story">Built by a runner whose VO2max would not move.</div>
        <div class="tags"><span>Local-first</span><span>Open source · MIT</span><span>Garmin + Claude</span></div>
        <div class="url">github.com/edxleon/runcoach-ai</div>
        <div class="fine">Not affiliated with Garmin. A training tool, not medical advice.</div>
      </div>""")

    # Off by default: the narration and the text in the frame carry it. The
    # caption timings are still computed - every reveal is anchored on them.
    captions = "".join(
        f'<div id="cap-{s["id"]}-{i}" class="cap clip" data-start="{c["t0"]}" data-duration="{round(c["t1"] - c["t0"], 3)}">'
        f'<span>{esc(c["text"])}</span></div>'
        for s in scenes for i, c in enumerate(s["caps"])) if captions_on else ""
    audio = "".join(
        f'<audio id="a-{s["id"]}" src="assets/audio/{s["id"]}.mp3" data-start="{round(s["start"] + LEAD, 3)}" '
        f'data-duration="{round(s["audio"], 3)}" data-volume="1"></audio>'
        for s in scenes)
    times = {s["id"]: {"t": s["start"], "a": round(s["start"] + LEAD, 3), "end": round(s["start"] + s["dur"], 3), "aud": s["audio"],
                       "c": [c["t0"] for c in s["caps"]]} for s in scenes}

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=1920, height=1080">
<script src="https://cdn.jsdelivr.net/npm/gsap@3.14.2/dist/gsap.min.js"></script>
<style>
{css}
</style>
</head>
<body>
<div id="root" data-composition-id="main" data-start="0" data-duration="{total}" data-width="1920" data-height="1080">
  <div id="chrome" class="clip" data-start="0" data-duration="{total}">
    <div class="wordmark">runcoach <span>v0.2</span></div>
  </div>
  {hook}{answer}{today}{strategy}{runs}{chat}{watch}{try_}{close}
  {captions}
  {audio}
</div>
<script>
const T = {json.dumps(times)};
for (const [id, v] of Object.entries(T)) v.c = new Proxy(v.c, {{ get(a, k) {{
  if (typeof k === "string" && /^\\d+$/.test(k) && !(k in a)) throw new Error(`scene ${{id}} has no caption ${{k}}`);
  return a[k]; }} }});
const tl = gsap.timeline({{ paused: true }});
const up = (sel, at, d = 0.5) => tl.from(sel, {{ opacity: 0, y: 24, duration: d, ease: "power2.out" }}, at);
const at = (id, f) => T[id].a + f * T[id].aud;   // a point inside the scene's speech
const fadeOut = (id) => tl.to(`#s-${{id}} .inner`, {{ opacity: 0, duration: 0.35 }}, T[id].end - 0.35);
const type = (sel, at, cps = 38) => document.querySelectorAll(sel).forEach((el) => {{
  const n = +el.dataset.len;
  tl.fromTo(el, {{ width: "0ch" }}, {{ width: n + "ch", duration: n / cps, ease: `steps(${{n}})` }}, at);
}});

// hook
type("#s-hook .q", T.hook.a, 16);
document.querySelectorAll("#s-hook .dw").forEach((el, i) => up(el, at("hook", 0.22) + i * 0.32, 0.35));
up("#s-hook .punch", T.hook.c[1]);
tl.to("#s-hook .datawall", {{ opacity: 0.35, duration: 0.5 }}, T.hook.c[1]);
fadeOut("hook");

// answer
up("#s-answer .tap-btn", T.answer.t, 0.35);
tl.to("#s-answer .tap-btn", {{ scale: 0.94, duration: 0.12, yoyo: true, repeat: 1 }}, T.answer.a + 0.6);
up("#s-answer .big-card", T.answer.a + 1.0, 0.5);
up("#s-answer .reason", at("answer", 0.62), 0.45);
tl.fromTo("#s-answer .rb", {{ color: "#cfd3dd" }}, {{ color: "#64d2a3", duration: 0.4 }}, at("answer", 0.8));
fadeOut("answer");

// today
up("#s-today .today-phone", T.today.t, 0.6);
up("#s-today .chips", at("today", 0.38));
tl.from("#s-today .ring-verdict", {{ opacity: 0, scale: 1.08, duration: 0.4 }}, at("today", 0.42));
up("#s-today .t1", T.today.a);
up("#s-today .t2", T.today.a + 0.3);
up("#s-today .t3", at("today", 0.62));
fadeOut("today");

// strategy
up("#s-strategy .s1", T.strategy.t + 0.1, 0.5);
up("#s-strategy .s2", T.strategy.c[1] - 0.2, 0.3);
tl.fromTo("#s-strategy .easy-seg", {{ width: "0%" }}, {{ width: "80%", duration: 0.7, ease: "power2.out" }}, T.strategy.c[1]);
tl.fromTo("#s-strategy .hard-seg", {{ width: "0%" }}, {{ width: "20%", duration: 0.45, ease: "power2.out" }}, T.strategy.c[1] + 0.6);
up("#s-strategy .s3", T.strategy.c[1] + 1.1, 0.4);
up("#s-strategy .g1", T.strategy.c[1] + 2.0, 0.3);
up("#s-strategy .s4", T.strategy.c[2], 0.4);
fadeOut("strategy");

// runs
up("#s-runs .p1", T.runs.t, 0.5);
up("#s-runs .p2", at("runs", 0.45), 0.5);
fadeOut("runs");

// chat
up("#s-chat .term", T.chat.t, 0.45);
type("#s-chat .ask", T.chat.c[1], 26);
up("#s-chat .h2", T.chat.c[1] + 1.8, 0.35);
up("#s-chat .h3", T.chat.c[2], 0.35);
const yesAt = T.chat.c[2] + 0.55 * (T.chat.end - T.chat.c[2]);
up("#s-chat .h4", yesAt - 0.05, 0.1);
type("#s-chat .yes", yesAt, 8);
up("#s-chat .h5", yesAt + 0.6, 0.35);
up("#s-chat .foot", yesAt + 0.6, 0.35);
fadeOut("chat");

// watch
up("#s-watch .big-watch", T.watch.t, 0.6);
up("#s-watch .ws1", T.watch.t + 0.3, 0.5);
up("#s-watch .w1", T.watch.a + 0.3, 0.35);
up("#s-watch .w1b", T.watch.a + 1.0, 0.35);
up("#s-watch .w2", T.watch.c[1], 0.35);
tl.to("#s-watch .ws1", {{ opacity: 0, duration: 0.4 }}, T.watch.c[1] + 0.7);
up("#s-watch .ws2", T.watch.c[1] + 1.0, 0.4);
up("#s-watch .w3", T.watch.c[1] + 1.0, 0.4);
fadeOut("watch");

// try
["f1", "f2"].forEach((f, i) => up(`#s-try .${{f}}`, T.try.a + i * 0.9, 0.35));
["f3", "f4"].forEach((f, i) => up(`#s-try .${{f}}`, T.try.c[1] + i * 0.6, 0.35));
up("#s-try .term", T.try.c[2] - 0.4, 0.4);
type("#s-try .b1 .type", T.try.c[2] - 0.1, 90);
type("#s-try .b2 .type", T.try.c[2] + 0.7, 30);
document.querySelectorAll("#s-try .b2 .out").forEach((el, i) => up(el, T.try.c[2] + 1.5 + i * 0.25, 0.25));
up("#s-try .demo-note", T.try.c[2] + 1.9, 0.4);
fadeOut("try");

// close
up("#s-close .logo", T.close.t + 0.1, 0.7);
up("#s-close .story", T.close.a + 0.6, 0.5);
document.querySelectorAll("#s-close .tags span").forEach((el, i) => up(el, T.close.a + 1.4 + i * 0.3, 0.35));
up("#s-close .url", T.close.a + 2.4, 0.5);
up("#s-close .fine", T.close.a + 3.0, 0.5);

window.__timelines = window.__timelines || {{}};
window.__timelines["main"] = tl;
tl.seek(0);
</script>
</body>
</html>
"""


if __name__ == "__main__":
    import sys

    (HERE / "index.html").write_text(render(captions_on="--captions" in sys.argv), encoding="utf-8")
    scenes, total = timeline()
    print(f"index.html written - {total:.1f} s, " + ", ".join(f"{s['id']} {s['dur']:.1f}" for s in scenes))
