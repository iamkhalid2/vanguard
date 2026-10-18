"""Bridge between the backend (SQLite) and the Command Center artifact.

The artifact runs in a sandbox that cannot reach your machine or GitHub, so state moves as files:
  python sync.py export            -> out/queue.json  (import it in Command Center > Pipeline > Import queue)
  python sync.py import FILE.json  <- approvals exported from Command Center > Pipeline > Export approvals

An approval file is the only thing that can set approved_at; nothing else in the backend does.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from vanguard import db

ROOT = Path(__file__).resolve().parent


def export(path: Path = ROOT / "out" / "queue.json") -> int:
    with db.connect() as conn:
        rows = conn.execute("""SELECT j.*, c.name AS company, c.tier, c.size_bucket, c.licensed_sponsor, t.projects, t.bullets, t.cover_letter, t.latex,
                                      o.subject, o.body FROM jobs j JOIN companies c ON c.id=j.company_id
                               LEFT JOIN tailorings t ON t.job_id=j.id LEFT JOIN outreach o ON o.job_id=j.id
                               WHERE j.status NOT IN ('filtered_out')""").fetchall()
        out = []
        for r in rows:
            contacts = [dict(c) for c in conn.execute("SELECT persona, name, linkedin_search_url, linkedin_url, email FROM contacts WHERE job_id=?", (r["id"],))]
            out.append({
                "id": r["id"], "company": r["company"], "tier": r["tier"], "size": r["size_bucket"], "title": r["title"],
                "location": r["location"], "url": r["url"], "description": r["description"][:12000], "status": r["status"],
                "sponsor": None if r["licensed_sponsor"] is None else bool(r["licensed_sponsor"]), "scoreBefore": r["score_before"], "scoreAfter": r["score_after"],
                "missing": json.loads(r["missing_keywords"] or "[]"), "minYears": r["min_years"], "sponsorship": r["sponsorship"],
                "projects": json.loads(r["projects"] or "[]"), "bullets": json.loads(r["bullets"] or "[]"),
                "coverLetter": r["cover_letter"], "latex": r["latex"],
                "email": {"subject": r["subject"], "body": r["body"]} if r["subject"] else None, "contacts": contacts,
            })
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"exportedAt": db.now(), "jobs": out}, indent=1))
    return len(out)


def import_approvals(path: Path) -> int:
    data = json.loads(path.read_text())
    n = 0
    with db.connect() as conn:
        for a in data.get("approvals", []):
            job = conn.execute("SELECT id FROM jobs WHERE id=?", (a["jobId"],)).fetchone()
            if not job:
                continue
            o = conn.execute("SELECT id, contact_id FROM outreach WHERE job_id=?", (a["jobId"],)).fetchone()
            if a.get("contact") and o:
                c = a["contact"]
                conn.execute("UPDATE contacts SET name=?, linkedin_url=?, email=? WHERE id=?",
                             (c.get("name"), c.get("linkedinUrl"), c.get("email"), o["contact_id"]))
            if a.get("email") and o:
                body = a["email"]["body"]
                conn.execute("UPDATE outreach SET subject=?, body=?, word_count=?, approved_at=? WHERE id=?",
                             (a["email"]["subject"], body, len(body.split()), a["approvedAt"], o["id"]))
            if a.get("alreadyDrafted") and o:  # the Command Center already made the Gmail draft
                conn.execute("UPDATE outreach SET gmail_draft_id='command-center', drafted_at=? WHERE id=?", (db.now(), o["id"]))
            db.set_status(conn, a["jobId"], a.get("status", "approved"))
            n += 1
    return n


if __name__ == "__main__":
    if sys.argv[1:2] == ["export"]:
        print(f"exported {export()} jobs to out/queue.json")
    elif sys.argv[1:2] == ["import"] and len(sys.argv) == 3:
        print(f"imported {import_approvals(Path(sys.argv[2]))} approvals")
    else:
        sys.exit(__doc__)
