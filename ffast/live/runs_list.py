"""The "Live runs" list (ADR 0087): every run the wrapper started on this account.

The wrapper writes one pointer per run, ``~/.ffast/live-runs/<run id>.json``
(``$FFAST_LIVE_RUNS`` overrides the folder), holding the run folder's path.
Each row is read from that folder's ``run.json``: producer progress, scored-
through progress and the scorer's heartbeat are shown apart, so a stalled scorer
is not mistaken for a stalled simulation.
"""

from __future__ import annotations

import datetime as _dt
from pathlib import Path

from ffast.live import run_folder as rf

LIMIT = 50
STALLED_S = 30.0          # 3 × the scorer's 10 s heartbeat


def _age_s(utc: str | None, now: _dt.datetime) -> float | None:
    if not utc:
        return None
    try:
        t = _dt.datetime.fromisoformat(utc.replace("Z", "+00:00"))
    except ValueError:
        return None
    if t.tzinfo is None:
        t = t.replace(tzinfo=_dt.timezone.utc)
    return max(0.0, (now - t).total_seconds())


def _row(pointer: dict, now: _dt.datetime) -> dict:
    from ffast.live.live_run import tab_name

    run_id = str(pointer.get("run_id", ""))
    run_dir = Path(str(pointer.get("run_dir", "")))
    row = {"run_id": run_id, "run_dir": str(run_dir), "name": tab_name(run_id),
           "started_utc": pointer.get("started_utc"), "exists": False}
    run = rf.read_json(run_dir / rf.RUN_JSON)
    if not isinstance(run, dict):
        return row
    analyses = run.get("analyses") or {}
    progress = next(iter(analyses.values()), {}) if analyses else {}
    end = run.get("end")
    age = _age_s(progress.get("heartbeat_utc"), now)
    settings = rf.read_json(run_dir / rf.ANALYSIS_DIR / progress.get("analysis_id", "-") / rf.SETTINGS) or {}
    row.update({
        "exists": True,
        "bundle": Path(settings["bundle"]).stem if settings.get("bundle") else None,
        "last_step": (run.get("producer") or {}).get("last_step"),
        "planned_end_step": progress.get("planned_end_step"),
        "first_step": progress.get("first_step"),
        "scored_through_step": progress.get("scored_through_step"),
        "scorer_state": progress.get("state"),
        "heartbeat_age_s": age,
        "stalled": end is None and progress.get("state") not in (None, "done", "refused", "failed")
                   and age is not None and age > STALLED_S,
        "alarms": [{"kind": a.get("kind"), "step": (a.get("confirmation") or {}).get("step")}
                   for a in progress.get("alarms") or []],
        "end": end,
        "state": (end or {}).get("state") or "running",
        "stop": run.get("stop"),
    })
    return row


def list_runs(pointer_dir: Path, limit: int = LIMIT, now: _dt.datetime | None = None) -> list[dict]:
    """Rows for the newest ``limit`` pointers, newest first."""
    now = now or _dt.datetime.now(_dt.timezone.utc)
    pointer_dir = Path(pointer_dir)
    if not pointer_dir.is_dir():
        return []
    pointers = [p for p in (rf.read_json(f) for f in pointer_dir.glob("*.json")) if isinstance(p, dict)]
    pointers.sort(key=lambda p: (str(p.get("started_utc") or ""), str(p.get("run_id"))), reverse=True)
    return [_row(p, now) for p in pointers[:limit]]


def forget_run(pointer_dir: Path, run_id: str) -> bool:
    """Delete the pointer of ``run_id`` (never the run folder); True if it existed."""
    if not run_id or "/" in run_id or run_id.startswith("."):
        raise ValueError(f"not a run id: {run_id!r}")
    path = Path(pointer_dir) / f"{run_id}.json"
    try:
        path.unlink()
        return True
    except FileNotFoundError:
        return False
