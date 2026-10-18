"""SQLite state store. One file (data/vanguard.db), committed back to the repo by the
GitHub Actions job so state survives between cron runs.

Pipeline states (jobs.status):
  scraped -> filtered_out | tailored -> pending_approval -> approved -> applied
  -> cold_email_drafted -> cold_email_sent -> interviewing | rejected | offer
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "vanguard.db"

STATUSES = [
    "scraped", "filtered_out", "tailored", "pending_approval", "approved", "applied",
    "cold_email_drafted", "cold_email_sent", "interviewing", "rejected", "offer",
]
# A company slot is "used" once an application is approved or later.
ACTIVE_STATUSES = ("approved", "applied", "cold_email_drafted", "cold_email_sent", "interviewing")

SCHEMA = """
CREATE TABLE IF NOT EXISTS companies (
  id            TEXT PRIMARY KEY,          -- slug, e.g. 'stripe'
  name          TEXT NOT NULL,
  tier          TEXT NOT NULL CHECK (tier IN ('A','B')),
  size_bucket   TEXT NOT NULL CHECK (size_bucket IN ('enterprise','mid','startup')),
  employees     INTEGER,
  ats           TEXT,                      -- greenhouse | lever | ashby | workable | amazon | playwright
  ats_token     TEXT,                      -- board token / tenant
  email_domain  TEXT,
  email_pattern TEXT,                      -- e.g. '{first}.{last}' once confirmed
  licensed_sponsor INTEGER                 -- 1/0 from the Home Office register, NULL if the name didn't match
);

CREATE TABLE IF NOT EXISTS jobs (
  id              TEXT PRIMARY KEY,        -- '<company>:<ats job id>'
  company_id      TEXT NOT NULL REFERENCES companies(id),
  title           TEXT NOT NULL,
  location        TEXT,
  workplace       TEXT,                    -- onsite | hybrid | remote | unknown
  url             TEXT NOT NULL,
  description     TEXT NOT NULL,
  posted_at       TEXT,
  scraped_at      TEXT NOT NULL,
  status          TEXT NOT NULL DEFAULT 'scraped',
  filter_reason   TEXT,
  min_years       REAL,                    -- parsed experience requirement
  needs_clearance INTEGER DEFAULT 0,
  sponsorship     TEXT,                    -- offered | not_offered | unknown
  score_before    REAL,                    -- keyword coverage of base resume, 0..1
  score_after     REAL,                    -- coverage of tailored resume, 0..1
  jd_keywords     TEXT,                    -- JSON list
  missing_keywords TEXT,                   -- JSON list (honest gaps; never faked)
  updated_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS jobs_status ON jobs(status);
CREATE INDEX IF NOT EXISTS jobs_company ON jobs(company_id, status);

CREATE TABLE IF NOT EXISTS tailorings (
  job_id          TEXT PRIMARY KEY REFERENCES jobs(id),
  projects        TEXT NOT NULL,           -- JSON list of {id, overlap}
  bullets         TEXT NOT NULL,           -- JSON list of {project_id, original, tailored, grounded, violations}
  cover_letter    TEXT,
  latex           TEXT NOT NULL,
  pdf_path        TEXT,
  model           TEXT,                    -- 'deterministic' or the Claude model id used
  created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS contacts (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  job_id          TEXT NOT NULL REFERENCES jobs(id),
  persona         TEXT NOT NULL,           -- engineering_manager | tech_lead | early_careers_recruiter | cto | vp_eng | cofounder
  name            TEXT,                    -- filled by a human from the LinkedIn search link
  linkedin_search_url TEXT NOT NULL,
  linkedin_url    TEXT,
  email           TEXT,
  email_confidence TEXT,                   -- verified | pattern | guess
  created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS outreach (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  job_id          TEXT NOT NULL REFERENCES jobs(id),
  contact_id      INTEGER REFERENCES contacts(id),
  subject         TEXT NOT NULL,
  body            TEXT NOT NULL,
  word_count      INTEGER NOT NULL,
  approved_at     TEXT,                    -- set only by a human (Command Center export)
  gmail_draft_id  TEXT,
  drafted_at      TEXT,
  sent_at         TEXT                     -- set by a human after pressing Send in Gmail
);

CREATE TABLE IF NOT EXISTS runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT, stage TEXT, started_at TEXT, finished_at TEXT, stats TEXT
);
"""


def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@contextmanager
def connect(path: Path = DB_PATH):
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def upsert_company(conn, c: dict) -> None:
    conn.execute(
        """INSERT INTO companies (id,name,tier,size_bucket,employees,ats,ats_token,email_domain,email_pattern)
           VALUES (:id,:name,:tier,:size_bucket,:employees,:ats,:ats_token,:email_domain,:email_pattern)
           ON CONFLICT(id) DO UPDATE SET name=excluded.name, tier=excluded.tier, size_bucket=excluded.size_bucket,
             employees=excluded.employees, ats=excluded.ats, ats_token=excluded.ats_token,
             email_domain=excluded.email_domain,
             email_pattern=COALESCE(companies.email_pattern, excluded.email_pattern)""",
        {"employees": None, "email_domain": None, "email_pattern": None, "ats_token": None, **c},
    )


def insert_job_if_new(conn, job: dict) -> bool:
    cur = conn.execute(
        """INSERT OR IGNORE INTO jobs (id,company_id,title,location,workplace,url,description,posted_at,scraped_at,updated_at)
           VALUES (:id,:company_id,:title,:location,:workplace,:url,:description,:posted_at,:scraped_at,:scraped_at)""",
        {"posted_at": None, "workplace": "unknown", **job, "scraped_at": now()},
    )
    return cur.rowcount == 1


def set_status(conn, job_id: str, status: str, **fields) -> None:
    assert status in STATUSES, status
    sets = ", ".join(f"{k} = :{k}" for k in fields)
    conn.execute(
        f"UPDATE jobs SET status = :status, updated_at = :ts{', ' + sets if sets else ''} WHERE id = :id",
        {"status": status, "ts": now(), "id": job_id, **{k: (json.dumps(v) if isinstance(v, (list, dict)) else v) for k, v in fields.items()}},
    )


def active_applications(conn, company_id: str, days: int = 30) -> int:
    """Applications approved or later at this company in the last `days` days (anti-spam cap)."""
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    q = f"SELECT COUNT(*) FROM jobs WHERE company_id = ? AND status IN ({','.join('?' * len(ACTIVE_STATUSES))}) AND updated_at >= ?"
    return conn.execute(q, (company_id, *ACTIVE_STATUSES, since)).fetchone()[0]


def drafts_today(conn) -> int:
    today = datetime.now(timezone.utc).date().isoformat()
    return conn.execute("SELECT COUNT(*) FROM outreach WHERE drafted_at >= ?", (today,)).fetchone()[0]
