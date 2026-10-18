"""Module D: decision-maker routing and cold email drafting.

What this does NOT do, on purpose: log into LinkedIn or scrape profiles. Automated LinkedIn
access breaks its User Agreement and gets accounts restricted, which would cost you the one
channel recruiters actually check. Instead, for each pending job it:
  1. Picks the target persona from company size (enterprise -> EM / tech lead / early-careers
     recruiter; startup -> CTO / VP Eng / technical co-founder).
  2. Builds a LinkedIn people-search URL for that persona at that company in London, which you
     open and pick a name from (about 30 seconds per job).
  3. Optionally asks Hunter.io (HUNTER_API_KEY, free tier 25 searches/month) for the company's
     email pattern; otherwise uses the pattern in targets.json, marked as unverified.
  4. Drafts the <90-word "show, don't tell" email, grounded in one flagship project.

Usage: python enricher.py
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from urllib.parse import quote_plus

import requests

from vanguard import db, grounding, keywords, llm

ROOT = Path(__file__).resolve().parent
KG = json.loads((ROOT / "data" / "knowledge_graph.json").read_text())
PROJECTS = {p["id"]: p for p in KG["projects"]}

PERSONAS = {
    "enterprise": [("engineering_manager", "engineering manager"), ("tech_lead", "tech lead"),
                   ("early_careers_recruiter", "early careers recruiter")],
    "mid": [("engineering_manager", "engineering manager"), ("vp_eng", "head of engineering")],
    "startup": [("cto", "CTO"), ("vp_eng", "VP engineering"), ("cofounder", "technical co-founder")],
}

TEAM_HINT = re.compile(r"\b(?:join|on) (?:our|the) ([A-Z][\w&-]*(?: [A-Z][\w&-]*){0,3}) (?:team|group|org)", re.M)


def linkedin_search(company: str, title: str, team: str | None) -> str:
    q = f'"{company}" {title} {team or ""} London'.strip()
    return f"https://www.linkedin.com/search/results/people/?keywords={quote_plus(q)}&origin=GLOBAL_SEARCH_HEADER"


def hunter_pattern(domain: str) -> str | None:
    key = os.environ.get("HUNTER_API_KEY")
    if not key or not domain:
        return None
    r = requests.get("https://api.hunter.io/v2/domain-search", params={"domain": domain, "api_key": key, "limit": 1}, timeout=20)
    return (r.json().get("data") or {}).get("pattern") if r.ok else None


def email_from_pattern(pattern: str, full_name: str, domain: str) -> str | None:
    parts = re.sub(r"[^a-z\s-]", "", full_name.lower()).split()
    if len(parts) < 2 or not pattern or not domain:
        return None
    first, last = parts[0], parts[-1]
    local = pattern.format(first=first, last=last, f=first[0], l=last[0])
    return f"{local}@{domain}"


def pick_flagship(jd: dict[str, int], selected_ids: list[str]) -> dict:
    pool = [PROJECTS[i] for i in selected_ids if i in PROJECTS and (PROJECTS[i]["repo"] or PROJECTS[i]["demo"]) and PROJECTS[i].get("on_resume", True)]
    pool = pool or [p for p in KG["projects"] if p["flagship"]]
    return max(pool, key=lambda p: keywords.coverage(jd, keywords.extract(grounding.project_evidence(p))))


def draft_email(company: str, role: str, team: str | None, jd: dict[str, int], project: dict) -> tuple[str, str]:
    link = project["repo"] or project["demo"]
    overlap = [t for t in keywords.extract(grounding.project_evidence(project)) if t in jd][:2]
    feature = max(project["features"], key=lambda f: keywords.coverage(jd, keywords.extract(f)))
    metric = next((m for m in project["metrics"] if "star" in m or "chapter" in m), None)
    area = team or (overlap[0] if overlap else "engineering")
    subject = f"question re: {company} london {area} role + {metric or project['name']}".lower()
    if llm.available():
        try:
            res = llm.complete_json(
                "Write a cold email under 90 words, 4-5 sentences: (1) saw the London ROLE working on AREA; (2) one flagship project "
                "with one specific architectural feature, from EVIDENCE only, with LINK; (3) ask if they are the right person to route the "
                "profile to, or open to a brief look at the repo. No flattery, no 'I hope this finds you well'. Return JSON {\"body\": \"...\"}.",
                f"COMPANY: {company}\nROLE: {role}\nAREA: {area}\nLINK: {link}\nEVIDENCE:\n{grounding.project_evidence(project)}")
            body = res["body"].strip()
            if len(body.split()) < 90 and not grounding.check(body, project):
                return subject, body
        except Exception:
            pass
    body = (f"Hi {{first_name}},\n\nI saw the London {role} opening on {area}. I built {project['name']} ({link}), "
            f"and the part closest to your work is this: {feature[0].lower() + feature[1:].rstrip('.')}. "
            f"Are you the right person to route my profile to, or open to a quick look at the repo?\n\nThanks,\nKhalid")
    return subject, body


def run() -> int:
    n = 0
    with db.connect() as conn:
        rows = conn.execute("""SELECT j.*, c.name AS company_name, c.size_bucket, c.email_domain, c.email_pattern, t.projects
                               FROM jobs j JOIN companies c ON c.id=j.company_id JOIN tailorings t ON t.job_id=j.id
                               WHERE j.status='pending_approval' AND j.id NOT IN (SELECT job_id FROM outreach)""").fetchall()
        for j in rows:
            jd = keywords.extract(j["title"] + "\n" + j["description"])
            team_m = TEAM_HINT.search(j["description"])
            team = team_m.group(1) if team_m else None
            pattern = j["email_pattern"] or hunter_pattern(j["email_domain"])
            if pattern and not j["email_pattern"]:
                conn.execute("UPDATE companies SET email_pattern=? WHERE id=?", (pattern, j["company_id"]))
            contact_ids = []
            for persona, title in PERSONAS[j["size_bucket"]]:
                cur = conn.execute("INSERT INTO contacts (job_id, persona, linkedin_search_url, email_confidence, created_at) VALUES (?,?,?,?,?)",
                                   (j["id"], persona, linkedin_search(j["company_name"], title, team), "pattern" if pattern else "guess", db.now()))
                contact_ids.append(cur.lastrowid)
            project = pick_flagship(jd, [p["id"] for p in json.loads(j["projects"])])
            subject, body = draft_email(j["company_name"], j["title"], team, jd, project)
            conn.execute("INSERT INTO outreach (job_id, contact_id, subject, body, word_count) VALUES (?,?,?,?,?)",
                         (j["id"], contact_ids[0], subject, body, len(body.split())))
            n += 1
    return n


if __name__ == "__main__":
    print(f"drafted outreach for {run()} jobs")
