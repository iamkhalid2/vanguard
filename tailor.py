"""Module C: ATS scoring, project selection, grounded bullet rewriting, LaTeX resume and cover letter.

For every kept job in status 'scraped':
  1. Extract JD keywords (deterministic lexicon) and score the base resume (coverage + cosine).
  2. Enforce the per-company cap (max 2 approved-or-later applications per 30 days).
  3. Rank knowledge-graph projects by weighted keyword overlap with the JD; keep the top 3-4.
  4. If ANTHROPIC_API_KEY is set, rewrite bullets in XYZ form and draft a <=200-word cover letter.
     Every rewritten bullet goes through grounding.check(); any unbacked skill or number
     reverts that bullet to the original evidence text.
  5. Render LaTeX, compile a PDF if tectonic/pdflatex exists, rescore, move to 'pending_approval'.

Usage: python tailor.py [--limit 20] [--job JOB_ID]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from vanguard import db, grounding, keywords, latex, llm

ROOT = Path(__file__).resolve().parent
KG = json.loads((ROOT / "data" / "knowledge_graph.json").read_text())
TARGETS = json.loads((ROOT / "config" / "targets.json").read_text())

SKILL_GROUPS = [("Languages", "languages"), ("Frameworks & Platforms", "frameworks"),
                ("AI & LLM Systems", "ai"), ("Infrastructure & Delivery", "infra"), ("Engineering", "concepts")]


def project_terms(p: dict) -> dict[str, int]:
    return keywords.extract(grounding.project_evidence(p))


def base_resume_text() -> str:
    """The untailored resume the 'before' score is measured on (text of the current Applied AI PDF)."""
    return (ROOT / "config" / "base_resume.txt").read_text()


def rank_projects(jd: dict[str, int], k: int = 4) -> list[dict]:
    scored = []
    for p in KG["projects"]:
        pt = project_terms(p)
        overlap = keywords.coverage(jd, pt)
        scored.append({"project": p, "overlap": overlap + (0.02 if p["flagship"] else 0), "terms": pt})
    scored.sort(key=lambda s: s["overlap"], reverse=True)
    top = [s for s in scored if s["overlap"] > 0.02][:k]
    return top if len(top) >= 3 else scored[:3]


def deterministic_bullets(p: dict, jd: dict[str, int], n: int = 2) -> list[str]:
    """Pick the n features with most JD overlap. No rewording, so nothing can be invented."""
    feats = sorted(p["features"], key=lambda f: keywords.coverage(jd, keywords.extract(f)), reverse=True)
    return feats[:n]


REWRITE_SYSTEM = """You rewrite resume bullets for one software project. Rules, all mandatory:
- Use ONLY facts in the EVIDENCE block. Never add a technology, number, user count, percentage or outcome that is not written there.
- XYZ form where the evidence allows: accomplished X, measured by Y, by doing Z. If the evidence has no metric, omit Y rather than invent one.
- Prefer the JD's exact wording for a skill only when the evidence shows that skill.
- Each bullet <= 30 words, past tense, no first person.
Return JSON: {"bullets": ["...", "..."]}"""

COVER_SYSTEM = """Write a cover letter of at most 200 words for an entry-level engineering role.
Structure: (1) hook naming a specific technical initiative or product from the JD, (2) proof: two engineering problems the candidate solved, taken only from EVIDENCE, each tied to the JD's stack, (3) one-sentence call to action.
Banned: "I am writing to", "passion", "excited to apply", "fast-paced", any claim not in EVIDENCE. Plain text, no greeting line, no sign-off.
Return JSON: {"cover_letter": "..."}"""


def tailor_job(conn, job) -> dict | None:
    cap = TARGETS["filters"]["max_active_per_company_30d"]
    if db.active_applications(conn, job["company_id"]) >= cap:
        conn.execute("UPDATE jobs SET filter_reason=? WHERE id=?", (f"company cap: {cap} active in 30 days", job["id"]))
        return None

    jd = keywords.extract(job["title"] + "\n" + job["description"])
    base = keywords.extract(base_resume_text())
    ranked = rank_projects(jd)
    use_llm = llm.available()

    bullets_out, bullets_by_project, projects = [], {}, []
    for r in ranked:
        p = r["project"]
        originals = deterministic_bullets(p, jd)
        tailored = originals
        if use_llm:
            evidence = grounding.project_evidence(p)
            try:
                res = llm.complete_json(REWRITE_SYSTEM, f"JD KEYWORDS: {', '.join(jd)}\n\nEVIDENCE:\n{evidence}\n\nORIGINAL BULLETS:\n" + "\n".join(originals))
                tailored = res["bullets"][: len(originals)] or originals
            except Exception:
                tailored = originals
        final = []
        for orig, new in zip(originals, tailored + originals[len(tailored):]):
            v = grounding.check(new, p) if new != orig else []
            final.append(orig if v else new)
            bullets_out.append({"project_id": p["id"], "original": orig, "tailored": new, "grounded": not v, "violations": v})
        bullets_by_project[p["id"]] = final
        shown = [t for t in r["terms"] if t in jd] + [t for t in r["terms"] if t not in jd]
        projects.append({**p, "stack_shown": shown[:5]})

    # Skills section: only terms the candidate has evidence for, JD terms first.
    have = {t for p in KG["projects"] for t in project_terms(p)}
    skills = []
    for label, cat in SKILL_GROUPS:
        items = [t for t in have if keywords.category(t) == cat]
        items.sort(key=lambda t: (t not in jd, t))
        if items:
            skills.append((label, items[:10]))

    headline = f"Software Engineer | {', '.join([t for t in jd if t in have][:6])}"
    summary = ("Software engineer (BSc Information Technology, 2024) who ships LLM systems, Android and web "
               f"applications end to end. Selected work below is chosen for {job['title']} and links to source.")
    cover = None
    if use_llm:
        ev = "\n\n".join(grounding.project_evidence(r["project"]) for r in ranked[:2])
        try:
            cover = llm.complete_json(COVER_SYSTEM, f"JD:\n{job['description'][:6000]}\n\nEVIDENCE:\n{ev}")["cover_letter"]
            if len(cover.split()) > 200:
                cover = " ".join(cover.split()[:200])
        except Exception:
            cover = None

    tex = latex.render(KG["candidate"], headline, summary, skills, projects, bullets_by_project)
    tailored_text = "\n".join([headline, summary, *[" ".join(i) for _, i in skills],
                               *[p["name"] + " " + " ".join(p["stack_shown"]) for p in projects],
                               *[b for bs in bullets_by_project.values() for b in bs],
                               *[b for e in KG["candidate"]["experience"] for b in e["bullets"]]])
    after = keywords.extract(tailored_text)
    pdf = latex.compile_pdf(tex, ROOT / "out" / job["id"].replace(":", "_") / "resume.pdf")

    conn.execute("INSERT OR REPLACE INTO tailorings (job_id, projects, bullets, cover_letter, latex, pdf_path, model, created_at) VALUES (?,?,?,?,?,?,?,?)",
                 (job["id"], json.dumps([{"id": r["project"]["id"], "overlap": r["overlap"]} for r in ranked]),
                  json.dumps(bullets_out), cover, tex, str(pdf) if pdf else None,
                  llm.MODEL if use_llm else "deterministic", db.now()))
    db.set_status(conn, job["id"], "pending_approval",
                  score_before=keywords.coverage(jd, base), score_after=keywords.coverage(jd, after),
                  jd_keywords=list(jd), missing_keywords=keywords.missing(jd, base))
    return {"job": job["id"], "before": keywords.coverage(jd, base), "after": keywords.coverage(jd, after),
            "projects": [r["project"]["id"] for r in ranked]}


def run(limit: int = 20, job_id: str | None = None) -> list[dict]:
    results = []
    with db.connect() as conn:
        # Licensed visa sponsors first, then Kepler-relevant stacks, then newest.
        q = ("SELECT j.* FROM jobs j JOIN companies c ON c.id=j.company_id WHERE j.status='scraped'"
             + (" AND j.id=?" if job_id else " ORDER BY COALESCE(c.licensed_sponsor,0) DESC, j.scraped_at DESC LIMIT ?"))
        for job in conn.execute(q, (job_id,) if job_id else (limit,)).fetchall():
            r = tailor_job(conn, dict(job))
            if r:
                results.append(r)
    return results


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=20)
    ap.add_argument("--job")
    a = ap.parse_args()
    print(json.dumps(run(a.limit, a.job), indent=2))
