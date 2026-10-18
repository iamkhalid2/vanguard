"""UK Register of Licensed Sponsors (Workers), published daily by the Home Office as a CSV.

A company on this register (Skilled Worker route) can sponsor a visa when the current right to
work ends. Vanguard marks every company with it and sorts sponsors first; it does not drop
non-sponsors, because a role that starts before mid-2028 is still worth having.
"""
from __future__ import annotations

import csv
import io
import re
from pathlib import Path

import requests

PAGE = "https://www.gov.uk/government/publications/register-of-licensed-sponsors-workers"
CACHE = Path(__file__).resolve().parent.parent / "data" / "sponsors.csv"
SUFFIX = re.compile(r"\b(ltd|limited|plc|llp|inc|incorporated|uk|u\.k\.|group|holdings|technologies|technology|services|international|europe|london)\b\.?", re.I)


def norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", SUFFIX.sub(" ", name.lower())).strip()


def refresh() -> Path:
    html = requests.get(PAGE, timeout=30).text
    url = re.search(r"https://assets\.publishing\.service\.gov\.uk/[^\"']+\.csv", html).group(0)
    CACHE.write_bytes(requests.get(url, timeout=120).content)
    return CACHE


def load(path: Path = CACHE) -> dict[str, list[str]]:
    """normalised organisation name -> routes (e.g. 'Skilled Worker')."""
    out: dict[str, list[str]] = {}
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    for row in csv.DictReader(io.StringIO(text)):
        out.setdefault(norm(row.get("Organisation Name", "")), []).append(row.get("Route", ""))
    return out


def is_skilled_worker_sponsor(company: str, register: dict[str, list[str]]) -> bool | None:
    """True/False when the name matches unambiguously; None when it can't be told by name."""
    key = norm(company)
    if not key:
        return None
    if key in register:
        return any("Skilled Worker" in r for r in register[key])
    hits = [k for k in register if k.startswith(key + " ") or k == key]
    if len(hits) == 1:
        return any("Skilled Worker" in r for r in register[hits[0]])
    return None
