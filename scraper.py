"""Module A: target discovery.

Pulls jobs from the companies in config/targets.json through each ATS's *public* job-board
JSON endpoint (Greenhouse, Lever, Ashby, Workable, Amazon). These are the feeds the companies
publish for their own careers pages, so they are stable, legal to read and need no browser.
Playwright is used only for sites with no feed (opt-in, see `playwright_google`).

LinkedIn, Glassdoor and Indeed are deliberately not scraped: their terms forbid it and they
ban accounts and IPs. Indeed and Workable are reachable through the Command Center's
connectors instead.

Usage:
  python scraper.py            # scrape, filter, store new jobs
  python scraper.py --check    # report which ats_tokens resolve
"""
from __future__ import annotations

import argparse
import html
import json
import re
import sys
import time
from pathlib import Path

import requests

from vanguard import db, filters, sponsors

ROOT = Path(__file__).resolve().parent
UA = {"User-Agent": "vanguard-jobsearch/1.0 (personal job search; contact via github.com/iamkhalid2)"}
TAG = re.compile(r"<[^>]+>")


def clean(s: str | None) -> str:
    s = html.unescape(s or "")
    s = re.sub(r"<(br|/p|/li|/h\d)[^>]*>", "\n", s, flags=re.I)
    return re.sub(r"\n{3,}", "\n\n", TAG.sub("", s)).strip()


def get(url: str, **kw):
    for attempt in range(3):
        r = requests.get(url, headers=UA, timeout=30, **kw)
        if r.status_code == 429 or r.status_code >= 500:
            time.sleep(2 ** attempt * 5)
            continue
        r.raise_for_status()
        return r.json()
    r.raise_for_status()


# --- adapters: each yields dicts with id, title, location, workplace, url, description, posted_at ---

def greenhouse(token: str):
    data = get(f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs", params={"content": "true"})
    for j in data.get("jobs", []):
        yield {"id": str(j["id"]), "title": j["title"], "location": (j.get("location") or {}).get("name", ""),
               "url": j["absolute_url"], "description": clean(j.get("content")), "posted_at": j.get("updated_at")}


def lever(token: str):
    for j in get(f"https://api.lever.co/v0/postings/{token}", params={"mode": "json"}):
        cats = j.get("categories") or {}
        lists = "\n".join(f"{l.get('text','')}\n{clean(l.get('content'))}" for l in j.get("lists", []))
        yield {"id": j["id"], "title": j["text"], "location": cats.get("location", ""),
               "workplace": j.get("workplaceType", "unknown"), "url": j["hostedUrl"],
               "description": clean(j.get("descriptionPlain") or j.get("description")) + "\n" + lists,
               "posted_at": str(j.get("createdAt", ""))}


def ashby(token: str):
    data = get(f"https://api.ashbyhq.com/posting-api/job-board/{token}")
    for j in data.get("jobs", []):
        yield {"id": j["id"], "title": j["title"], "location": j.get("location", ""),
               "workplace": "remote" if j.get("isRemote") else (j.get("workplaceType") or "unknown").lower(),
               "url": j["jobUrl"], "description": j.get("descriptionPlain") or clean(j.get("descriptionHtml")),
               "posted_at": j.get("publishedAt")}


def workable(token: str):
    data = get(f"https://apply.workable.com/api/v1/widget/accounts/{token}", params={"details": "true"})
    for j in data.get("jobs", []):
        yield {"id": j["shortcode"], "title": j["title"], "location": f"{j.get('city','')}, {j.get('country','')}",
               "workplace": "remote" if j.get("telecommuting") else "unknown", "url": j["url"],
               "description": clean(j.get("description")), "posted_at": j.get("published_on")}


def amazon(city: str):
    for q in ("software development engineer", "applied scientist graduate", "software engineer graduate"):
        data = get("https://www.amazon.jobs/en/search.json",
                   params={"base_query": q, "loc_query": city, "country": "GBR", "result_limit": 100, "sort": "recent"})
        for j in data.get("jobs", []):
            yield {"id": j["id_icims"], "title": j["title"], "location": j.get("normalized_location", ""),
                   "url": "https://www.amazon.jobs" + j["job_path"],
                   "description": "\n".join([j.get("description", ""), j.get("basic_qualifications", ""), j.get("preferred_qualifications", "")]),
                   "posted_at": j.get("posted_date")}


def playwright_google(_token: str):
    """Opt-in browser adapter for Google Careers (no feed). Polite: one listing page, then detail pages, 2 s apart."""
    from playwright.sync_api import sync_playwright
    url = "https://www.google.com/about/careers/applications/jobs/results/?location=London%2C%20UK&target_level=EARLY&q=software%20engineer"
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(user_agent=UA["User-Agent"])
        page.goto(url, wait_until="networkidle")
        links = sorted({a.get_attribute("href") for a in page.locator("a[href*='jobs/results/']").all() if a.get_attribute("href")})
        for href in links[:25]:
            full = href if href.startswith("http") else "https://www.google.com/about/careers/applications/" + href.lstrip("./")
            time.sleep(2)
            page.goto(full, wait_until="networkidle")
            title = page.locator("h2").first.inner_text(timeout=10_000)
            yield {"id": re.sub(r"\W+", "-", full.split("results/")[-1])[:80], "title": title, "location": "London, UK",
                   "url": full, "description": page.locator("main").inner_text()}
        browser.close()


ADAPTERS = {"greenhouse": greenhouse, "lever": lever, "ashby": ashby, "workable": workable,
            "amazon": amazon, "playwright": playwright_google}


def load_targets() -> dict:
    return json.loads((ROOT / "config" / "targets.json").read_text())


def scrape(check_only: bool = False) -> dict:
    cfg = load_targets()
    stats = {"new": 0, "kept": 0, "filtered": 0, "errors": []}
    register = {}
    try:
        sponsors.refresh()
    except Exception as e:
        stats["errors"].append(f"sponsor register refresh: {e.__class__.__name__}")
    if sponsors.CACHE.exists():
        register = sponsors.load()
    with db.connect() as conn:
        for c in cfg["companies"]:
            if not c.get("enabled", True):
                continue
            db.upsert_company(conn, {k: c.get(k) for k in ("id", "name", "tier", "size_bucket", "employees", "ats", "ats_token", "email_domain", "email_pattern")})
            if register:
                s = sponsors.is_skilled_worker_sponsor(c.get("legal_name", c["name"]), register)
                conn.execute("UPDATE companies SET licensed_sponsor=? WHERE id=?", (None if s is None else int(s), c["id"]))
            try:
                jobs = list(ADAPTERS[c["ats"]](c["ats_token"]))
            except Exception as e:  # one broken board must not stop the run
                stats["errors"].append(f"{c['id']}: {e.__class__.__name__}: {str(e)[:120]}")
                continue
            if check_only:
                print(f"ok   {c['id']:<14} {len(jobs)} postings")
                continue
            for j in jobs:
                j = {"workplace": "unknown", **j, "id": f"{c['id']}:{j['id']}", "company_id": c["id"]}
                if not db.insert_job_if_new(conn, j):
                    continue
                stats["new"] += 1
                keep, reason, parsed = filters.evaluate(j, cfg["filters"])
                if keep:
                    stats["kept"] += 1
                    conn.execute("UPDATE jobs SET min_years=?, needs_clearance=?, sponsorship=? WHERE id=?",
                                 (parsed["min_years"], parsed["needs_clearance"], parsed["sponsorship"], j["id"]))
                else:
                    stats["filtered"] += 1
                    db.set_status(conn, j["id"], "filtered_out", filter_reason=reason, **parsed)
            time.sleep(1)
        conn.execute("INSERT INTO runs (stage, started_at, finished_at, stats) VALUES ('scrape', ?, ?, ?)",
                     (db.now(), db.now(), json.dumps(stats)))
    for e in stats["errors"]:
        print("ERR ", e, file=sys.stderr)
    return stats


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    print(json.dumps(scrape(check_only=args.check), indent=2))
