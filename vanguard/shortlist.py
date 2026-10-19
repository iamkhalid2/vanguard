"""Module 2b: shortlist. Turns jobs that passed the eligibility filters into an apply batch.

Ordering: licensed visa sponsors first (his right to work ends 30 June 2028), then keyword
coverage, then newest. The 2-per-company cap is applied here as well as in the runner, so a
batch never carries more than the company has slots for.
"""
from __future__ import annotations

import re

from . import tracker

KIND = [("android", re.compile(r"\b(android|mobile|kotlin)\b", re.I)),
        ("ai", re.compile(r"\b(ai|ml|machine learning|llm|applied ai|data scien\w*)\b", re.I))]


def kind_of(title: str) -> str:
    return next((k for k, rx in KIND if rx.search(title)), "fullstack")


def apply_url(url: str) -> str:
    if "jobs.ashbyhq.com" in url and not url.rstrip("/").endswith("/application"):
        return url.rstrip("/") + "/application"
    return url


def from_db(conn, limit: int = 10, runs=tracker.RUNS) -> list[dict]:
    rows = conn.execute("""
        SELECT j.*, c.name AS company, c.licensed_sponsor FROM jobs j JOIN companies c ON c.id = j.company_id
        WHERE j.status NOT IN ('filtered_out', 'applied', 'rejected')
        ORDER BY COALESCE(c.licensed_sponsor, 0) DESC, COALESCE(j.score_before, 0) DESC, j.scraped_at DESC""").fetchall()
    batch, per_company = [], {}
    for r in rows:
        used = tracker.company_count(r["company"], runs) + per_company.get(r["company"], 0)
        if used >= tracker.CAP or tracker.already_submitted(apply_url(r["url"]), runs):
            continue
        per_company[r["company"]] = per_company.get(r["company"], 0) + 1
        batch.append({"id": re.sub(r"\W+", "-", f"{r['company']}-{r['title']}").strip("-").lower(),
                      "company": r["company"], "role": r["title"], "kind": kind_of(r["title"]),
                      "url": apply_url(r["url"]), "jd_text": r["description"]})
        if len(batch) >= limit:
            break
    return batch
