"""Module 7a: application log and the 2-per-company-per-30-days cap.

runs/applications.jsonl is append-only. A URL counts once it has any submitted entry: later
skipped or paused lines for the same URL (a re-run, a cap check) never un-count it. Only real
submissions count, so a paused or failed fill never uses up a company's slot.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .profile import ROOT

RUNS = ROOT / "runs"
CAP, WINDOW_DAYS = 2, 30
COUNTED = {"submitted", "submit_unconfirmed"}


def log(entry: dict, runs: Path = RUNS) -> dict:
    runs.mkdir(parents=True, exist_ok=True)
    entry = {**entry, "ts": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    with (runs / "applications.jsonl").open("a") as f:
        f.write(json.dumps(entry) + "\n")
    return entry


def entries(runs: Path = RUNS) -> list[dict]:
    f = runs / "applications.jsonl"
    return [json.loads(l) for l in f.read_text().splitlines() if l.strip()] if f.exists() else []


def company_count(company: str, runs: Path = RUNS, now: datetime | None = None) -> int:
    cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=WINDOW_DAYS)
    return len({e["url"] for e in entries(runs) if e.get("company", "").lower() == company.lower()
                and e.get("status") in COUNTED and datetime.fromisoformat(e["ts"]) >= cutoff})


def already_submitted(url: str, runs: Path = RUNS) -> bool:
    return any(e.get("url") == url and e.get("status") in COUNTED for e in entries(runs))
