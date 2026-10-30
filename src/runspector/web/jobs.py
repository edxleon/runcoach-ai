"""Coach jobs and cards as plain JSON files under `~/.runspector/`.

    jobs/j-YYYYMMDD-HHMMSS-<hex>.json   one job, status inside the file
    jobs/j-….log                        what the agent run printed
    cards/j-….json                      the resulting card (same id as its job)

Files rather than memory so that a restart loses nothing and a card can be
inspected with any editor. Writes are atomic (tmp + replace); the HTTP threads
read while the worker thread writes.
"""

from __future__ import annotations

import logging
import re
import secrets
from datetime import datetime, timedelta
from pathlib import Path

from .. import paths

ACTIVE = ("queued", "running")
FINAL = ("done", "failed", "timeout", "cancelled")
log = logging.getLogger(__name__)

QUEUE_CAP = 3
RETENTION_DAYS = 14
RETENTION_MAX = 50
CARDS_MAX = 60

#: A job or card id. Always `fullmatch`: `match` with `$` lets a trailing
#: newline through. It is the path-traversal guard, so `_checked` applies it
#: inside every helper that turns an id into a file name.
ID_RE = re.compile(r"j-[0-9a-z-]+")


def _checked(job_id: str) -> str:
    if not isinstance(job_id, str) or not ID_RE.fullmatch(job_id):
        raise ValueError(f"not a job or card id: {job_id!r}")
    return job_id


def _now() -> datetime:
    return datetime.now().astimezone()


def new_id() -> str:
    for _ in range(5):
        jid = f"j-{_now():%Y%m%d-%H%M%S}-{secrets.token_hex(2)}"
        if not job_path(jid).exists():
            return jid
    return f"j-{_now():%Y%m%d-%H%M%S}-{secrets.token_hex(4)}"


def job_path(job_id: str) -> Path:
    return paths.jobs_dir() / f"{_checked(job_id)}.json"


def log_path(job_id: str) -> Path:
    return paths.jobs_dir() / f"{_checked(job_id)}.log"


def card_path(card_id: str) -> Path:
    return paths.cards_dir() / f"{_checked(card_id)}.json"


#: How long `_write_json` keeps trying to swap a file in. On Windows a reader
#: holding the target makes `os.replace` raise, and every page load reads these
#: files while the worker writes them. Six attempts with a linear backoff is
#: ~2 s, far longer than a JSON read of a few kilobytes.
_write_json = paths.write_json_atomic
_read_json = paths.read_json


# ── Jobs ─────────────────────────────────────────────────────────────────────

def new_job(prompt: str, *, title: str, kind: str, ctx: dict | None = None,
            parent: str | None = None, athlete_text: str | None = None) -> dict:
    """`athlete_text` is what the ATHLETE wrote for this job (a question, a
    follow-up) and nothing else - the one text a card may later propose to
    remember from. The prompt mixes it with model text and cannot serve."""
    job = {
        "id": new_id(), "title": title[:80], "prompt": prompt,
        "template_id": kind, "ctx": ctx or None, "parent": parent, "from_job": parent,
        "athlete_text": athlete_text,
        "created": _now().isoformat(timespec="seconds"), "started": None, "finished": None,
        "status": "queued", "exit": None, "cost_usd": None, "result_summary": None, "note": None,
    }
    write_job(job)
    return job


def write_job(job: dict) -> None:
    _write_json(job_path(job["id"]), job)


def read_job(job_id: str) -> dict | None:
    return _read_json(job_path(job_id)) if ID_RE.fullmatch(job_id) else None


def read_jobs(limit: int = 20) -> list[dict]:
    """Newest first (the id carries the timestamp)."""
    files = sorted(paths.jobs_dir().glob("j-*.json"), reverse=True)
    return [j for j in (_read_json(p) for p in files[:limit])
            if j and isinstance(j.get("id"), str) and ID_RE.fullmatch(j["id"])]


def _cancel_path(job_id: str) -> Path:
    return paths.jobs_dir() / f"{_checked(job_id)}.cancel"


def request_cancel(job_id: str) -> None:
    """A marker FILE, so the HTTP thread never writes the job file the worker owns."""
    _cancel_path(job_id).write_text("", encoding="utf-8")


def cancel_requested(job_id: str) -> bool:
    return _cancel_path(job_id).exists()


def clear_cancel(job_id: str) -> None:
    _cancel_path(job_id).unlink(missing_ok=True)


def public(job: dict) -> dict:
    """What the UI gets: never the prompt, never the raw athlete text."""
    return {k: v for k, v in job.items() if k not in ("prompt", "athlete_text")}


def count_active() -> int:
    return sum(1 for j in read_jobs(100) if j.get("status") in ACTIVE)


def next_queued() -> dict | None:
    queued = [j for j in read_jobs(100) if j.get("status") == "queued"]
    return min(queued, key=lambda j: j["id"]) if queued else None


def recover_stale() -> int:
    """A job left `running` by a previous process never finishes on its own."""
    n = 0
    for j in read_jobs(100):
        if j.get("status") == "running":
            j.update(status="failed", finished=_now().isoformat(timespec="seconds"),
                     result_summary="interrupted by a restart")
            write_job(j)
            n += 1
    return n


def cleanup() -> None:
    finals = [j for j in read_jobs(1000) if j.get("status") in FINAL]
    cutoff = (_now() - timedelta(days=RETENTION_DAYS)).isoformat()
    for idx, job in enumerate(finals):
        if idx >= RETENTION_MAX or str(job.get("finished") or job.get("created") or "") < cutoff:
            # `.cancel` too: a job cancelled a moment before it finished on its
            # own leaves a marker that nothing ever removed, so `jobs/` collected
            # one file per such race, forever.
            for p in (job_path(job["id"]), log_path(job["id"]), _cancel_path(job["id"])):
                p.unlink(missing_ok=True)


# ── Cards ────────────────────────────────────────────────────────────────────

def card_key(c: dict) -> tuple:
    """Kind AND reference: two "analyze-run" cards for different runs are
    different things."""
    ctx = c.get("ctx")
    ref = "|".join(f"{k}={ctx[k]}" for k in sorted(ctx)) if isinstance(ctx, dict) and ctx else ""
    return (str(c.get("kind") or ""), ref)


def write_card(card_id: str, card: dict) -> None:
    _write_json(card_path(card_id), card)


def delete_card(card_id: str) -> bool:
    """Remove one card for good. This is not convenience: `cleanup_cards` pins the
    newest card of each kind forever, and `previous_card_block` feeds it into every
    later card of that kind — so a card steered by an untrusted workout name would
    otherwise keep steering, with no way out."""
    if not ID_RE.fullmatch(card_id) or not card_path(card_id).is_file():
        return False
    card_path(card_id).unlink(missing_ok=True)
    return True


def read_card(card_id: str) -> dict | None:
    return _read_json(card_path(card_id)) if ID_RE.fullmatch(card_id) else None


def list_cards() -> list[tuple[str, dict]]:
    """Newest first by `generated_at` — NOT by mtime: giving feedback rewrites the
    file, which would make an old card jump to the top and become the "previous
    card" of the next run."""
    cards = [(p.stem, c) for p in paths.cards_dir().glob("j-*.json")
             if ID_RE.fullmatch(p.stem) and (c := _read_json(p)) is not None]
    cards.sort(key=lambda e: str(e[1].get("generated_at") or e[1].get("day") or ""), reverse=True)
    return cards


def latest_card(kind: str, ctx: dict | None = None, exclude: str | None = None) -> dict | None:
    for cid, c in list_cards():
        if cid != exclude and str(c.get("kind") or "") == kind and (c.get("ctx") or None) == (ctx or None):
            return {"id": cid, **c}
    return None


def cleanup_cards() -> None:
    """Keep the newest CARDS_MAX — plus always the newest card of each kind: it is
    the dedup anchor and the memory of the next run."""
    seen: set[tuple] = set()
    for idx, (cid, c) in enumerate(list_cards()):
        first_of_kind = card_key(c) not in seen
        seen.add(card_key(c))
        if idx >= CARDS_MAX and not first_of_kind:
            card_path(cid).unlink(missing_ok=True)


def _cap(value, length: int) -> str:
    """Truncate VISIBLY. A silently cut headline turns a condition ("... if HRV
    recovers") into a claim."""
    s = str(value or "")
    return s if len(s) <= length else s[:length - 1].rstrip() + "…"


def public_cards(limit: int = 10) -> list[dict]:
    """Card content is model output: only the STRUCTURE is enforced and capped
    here; the frontend renders it escaped. A broken card file is skipped — it
    must not empty the list."""
    out = []
    for cid, c in list_cards()[:limit]:
        bullets = c.get("bullets") if isinstance(c.get("bullets"), list) else []
        fb = c.get("feedback") if isinstance(c.get("feedback"), dict) else None
        out.append({
            "id": cid, "job_id": cid,
            "kind": _cap(c.get("kind"), 40), "ref": _cap(c.get("ref"), 40),
            "day": str(c.get("day") or "")[:10],
            "headline": _cap(c.get("headline"), 220),
            "verdict": _cap(c.get("verdict"), 160),
            "bullets": [_cap(b, 400) for b in bullets[:8]],
            "delta": _cap(c.get("delta"), 300),
            "text": "",
            "written": str(c.get("generated_at") or ""),
            "source": "job",
            "model": _cap(c.get("model"), 40),
            "generated_at": str(c.get("generated_at") or "")[:32],
            "data_through": str(c.get("data_through") or "")[:10],
            "snapshot_generated_at": str(c.get("snapshot_generated_at") or "")[:32],
            "cost_usd": c.get("cost_usd") if isinstance(c.get("cost_usd"), (int, float)) else None,
            "parent": c.get("parent") or None,
            "ctx": c.get("ctx") if isinstance(c.get("ctx"), dict) else None,
            # The id of a session the coach filed; `server.App._cards_with_proposals`
            # resolves it to preview + status (this module cannot import `plan`).
            "proposal": _cap(c.get("proposal"), 40) if isinstance(c.get("proposal"), str) else None,
            "feedback": ({"value": str(fb.get("value") or "")[:10], "text": _cap(fb.get("text"), 500),
                          "ts": str(fb.get("ts") or "")[:32]} if fb else None),
        })
    return out
