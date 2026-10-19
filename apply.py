"""Module 7b: apply runner. Thin glue over the tested modules; it makes no decisions of its own.

  profile  -> evidence -> questions -> answers (plan + check) -> forms (read, fill, verify) -> tracker

Per job: cap check, open the form, read every field, plan an answer for each, refuse to touch the
form if the plan has blocking problems, fill, read every value back, screenshot, and list the job
in runs/ready-to-submit.json. Only `--submit ids` clicks Submit, and only for ids a human named.
A CAPTCHA parks the job; nothing here solves one.

Usage
  python apply.py --batch config/apply_batch.json            # fill + verify + screenshot, no submit
  python apply.py --submit id1,id2                            # submit exactly these ready ids
  python apply.py --plan --batch config/apply_batch.json      # read forms headless, write answer sheets only
  python apply.py --brief ID                                  # writer briefs for ID's write-up fields
  python apply.py URL --company X --role Y --kind fullstack   # one job
Answer sheets land in runs/plans/<id>.md (label -> exact text, in form order); the visual filler on
the Mac types from them verbatim. Authored answers go in config/answers/<id>.json (label or intent -> text).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

from vanguard import answers, filters, forms, profile, tracker, writer

ROOT = Path(__file__).resolve().parent
PROFILE_DIR = Path.home() / ".vanguard" / "profile"
SEARCH_DIRS = [ROOT / "assets" / "resumes", Path.home() / "Documents" / "Codes", Path.home() / "Downloads"]
RESUMES = {"android": "Mohammad-Khalid-Resume-Frontend-Kepler.pdf", "ai": "Mohammad-Khalid-Resume-FullStack.pdf",
           "fullstack": "Mohammad-Khalid-Resume-FullStack.pdf"}


def job_id(job: dict) -> str:
    return job.get("id") or re.sub(r"\W+", "-", f"{job['company']}-{job['role']}").strip("-").lower()


def resume_for(kind: str) -> Path | None:
    name = RESUMES.get(kind, RESUMES["fullstack"])
    for d in SEARCH_DIRS:
        hit = next(iter(sorted(d.rglob(name))), None) if d.exists() else None
        if hit:
            return hit
    return None


def normalise(job: dict) -> dict:
    """Fills in id, resume, JD text and legacy cover_text so every module sees the same job shape."""
    j = dict(job)
    j["id"] = job_id(j)
    j.setdefault("kind", "fullstack")
    if not j.get("jd_text") and j.get("jd_path") and (ROOT / j["jd_path"]).exists():
        j["jd_text"] = (ROOT / j["jd_path"]).read_text()
    if not j.get("resume_path"):
        r = resume_for(j["kind"])
        if r:
            j["resume_path"] = str(r)
    if j.get("cover_text") and (ROOT / j["cover_text"]).exists():
        j.setdefault("answers", {})
        j["answers"] = {**j["answers"], "cover_letter": (ROOT / j["cover_text"]).read_text().strip()}
    return j


def write_plan(j: dict, fields: list, items: list, problems: list, runs: Path) -> Path:
    d = runs / "plans"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{j['id']}.json").write_text(json.dumps({"job": j,
                                                    "fields": fields, "plan": answers.to_json(items),
                                                    "problems": problems}, indent=2))
    md = d / f"{j['id']}.md"
    head = f"# {j['company']}: {j['role']}\n\n{j.get('url', '')}\n\n"
    block = ("## Blocking\n\n" + "\n".join(f"- {p}" for p in problems) + "\n\n") if problems else "No blocking problems.\n\n"
    md.write_text(head + block + "## Answers, in form order\n\n" + answers.sheet(items))
    return md


def open_form(ctx, j: dict):
    page = ctx.new_page()
    page.goto(j["url"], wait_until="domcontentloaded")
    if not j["url"].startswith("file:"):
        page.evaluate("() => { try { localStorage.clear(); sessionStorage.clear(); } catch (e) {} }")
        page.reload(wait_until="domcontentloaded")
        page.wait_for_timeout(2500)
    n = len(ctx.pages)
    forms.click_apply(page)
    if len(ctx.pages) > n:
        page = ctx.pages[-1]
        page.wait_for_load_state("domcontentloaded")
        page.wait_for_timeout(2000)
    forms.dismiss_cookies(page)
    return page


def process(ctx, job: dict, prof: profile.Profile, mode: str, runs: Path) -> dict:
    """mode: plan (read + plan only) | fill (fill + verify) | submit (fill + verify + submit)."""
    j = normalise(job)
    rec = {"id": j["id"], "company": j["company"], "role": j["role"], "url": j["url"], "problems": [], "screenshots": []}
    if "linkedin.com" in urlparse(j["url"]).netloc:
        return {**rec, "status": "skipped", "reason": "LinkedIn is not automated"}
    if j.get("skip") or j.get("hold"):
        return {**rec, "status": "skipped" if j.get("skip") else "paused", "reason": j.get("skip") or j.get("hold")}
    if mode != "plan" and tracker.already_submitted(j["url"], runs):
        return {**rec, "status": "skipped", "reason": "already submitted"}
    if mode != "plan" and tracker.company_count(j["company"], runs) >= tracker.CAP:
        return {**rec, "status": "skipped", "reason": f"cap: {tracker.CAP} applications to {j['company']} in {tracker.WINDOW_DAYS} days"}
    if mode != "plan" and (not j.get("resume_path") or not (ROOT / j["resume_path"]).exists()):   # absolute paths survive the join
        return {**rec, "status": "paused", "reason": f"resume not found for kind {j['kind']}"}

    page = open_form(ctx, j)
    rec["final_url"] = page.url
    window = sorted(filters.grad_years(j.get("jd_text") or page.inner_text("body")))
    if window and prof.grad_year not in window:
        return {**rec, "status": "skipped", "reason": f"ineligible: graduation window {window} excludes {prof.grad_year}"}
    if forms.has_captcha(page):
        return {**rec, "status": "parked-captcha", "reason": "CAPTCHA on the form; left for later"}
    scope = forms.scope_of(page)
    fields = forms.read(scope)
    if not [f for f in fields if f["kind"] != "file"]:
        return {**rec, "status": "failed", "reason": f"no application form at {page.url} (login wall or unsupported page)"}
    items = answers.plan(fields, j, prof)
    problems = answers.check(items, prof, j)
    rec["plan"] = str(write_plan(j, fields, items, problems, runs))
    if problems:
        return {**rec, "status": "paused", "reason": "plan has blocking problems", "problems": problems}
    if mode == "plan":
        return {**rec, "status": "planned"}

    shots = runs / "screenshots"
    shots.mkdir(parents=True, exist_ok=True)
    before = forms.readback(scope)
    errors = forms.fill(page, scope, items, ROOT)
    bad = forms.verify(scope, items, before)
    shot = shots / f"{j['id']}-filled.png"
    page.screenshot(path=str(shot), full_page=True)
    rec["screenshots"].append(str(shot))
    if errors or bad:
        return {**rec, "status": "failed-verify", "reason": "a field did not hold its planned value", "problems": errors + bad}
    if mode == "fill":
        return {**rec, "status": "filled", "job": job}
    outcome = forms.submit(page)
    post = shots / f"{j['id']}-confirmation.png"
    page.screenshot(path=str(post), full_page=True)
    rec["screenshots"].append(str(post))
    status = {"submitted": "submitted", "captcha": "parked-captcha", "no_button": "failed",
              "unconfirmed": "submit_unconfirmed"}[outcome]
    return {**rec, "status": status, "reason": "" if status == "submitted" else outcome}


def run(jobs: list[dict], mode: str = "fill", headless: bool = False, runs: Path = tracker.RUNS,
        prof: profile.Profile | None = None, launch=None) -> list[dict]:
    from playwright.sync_api import sync_playwright
    prof = prof or profile.load()
    bad = profile.validate(prof)
    if bad:
        sys.exit("profile problems:\n" + "\n".join(bad))
    results, ready = [], []
    with sync_playwright() as pw:
        if launch:
            ctx = launch(pw)
        else:
            PROFILE_DIR.mkdir(parents=True, exist_ok=True)
            try:
                ctx = pw.chromium.launch_persistent_context(str(PROFILE_DIR), channel="msedge", headless=headless)
            except Exception:
                ctx = pw.chromium.launch_persistent_context(str(PROFILE_DIR), headless=headless)
        for job in jobs:
            try:
                r = process(ctx, job, prof, mode, runs)
            except Exception as e:
                r = {"id": job_id(job), "company": job["company"], "role": job["role"], "url": job["url"],
                     "status": "failed", "reason": f"{type(e).__name__}: {e}"[:300], "problems": [], "screenshots": []}
            if r["status"] == "filled":
                ready.append({"id": r["id"], "company": r["company"], "role": r["role"], "url": r["url"],
                              "screenshot": r["screenshots"][0], "plan": r.get("plan"), "job": r.pop("job")})
            if mode != "plan":
                tracker.log(r, runs)
            results.append(r)
            print(f"{r['status']:>18}  {r['id']}  {r.get('reason', '')}", flush=True)
            for p in r.get("problems", [])[:12]:
                print(f"{'':>20}- {p}", flush=True)
        ctx.close()
    if mode == "fill":
        runs.mkdir(parents=True, exist_ok=True)
        (runs / "ready-to-submit.json").write_text(json.dumps(ready, indent=2))
    return results


def briefs(jid: str, runs: Path = tracker.RUNS) -> list[dict]:
    """Writer briefs for every write-up field in a planned job (run --plan first)."""
    data = json.loads((runs / "plans" / f"{jid}.json").read_text())
    j = normalise(data["job"])
    prof = profile.load()
    out = []
    for it in data["plan"]:
        if it["intent"] in writer.WORDS:
            b = writer.brief(it["intent"], it["label"], prof, j["company"], j["role"], j.get("jd_text", ""))
            b["current_answer"], b["current_problems"] = it["value"], it["problems"]
            out.append(b)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("url", nargs="?")
    ap.add_argument("--company")
    ap.add_argument("--role")
    ap.add_argument("--kind", choices=RESUMES, default="fullstack")
    ap.add_argument("--batch", type=Path)
    ap.add_argument("--submit", help="comma-separated ids from runs/ready-to-submit.json that a human approved")
    ap.add_argument("--plan", action="store_true", help="read forms and write answer sheets; fill nothing")
    ap.add_argument("--brief", metavar="ID", help="print writer briefs for a planned job")
    ap.add_argument("--headless", action="store_true")
    a = ap.parse_args()
    if a.brief:
        print(json.dumps(briefs(a.brief), indent=2))
        return
    if a.submit:
        ready = {r["id"]: r for r in json.loads((tracker.RUNS / "ready-to-submit.json").read_text())}
        ids = [i.strip() for i in a.submit.split(",") if i.strip()]
        unknown = [i for i in ids if i not in ready]
        if unknown:
            sys.exit(f"not in ready-to-submit.json: {unknown}")
        jobs, mode = [ready[i]["job"] for i in ids], "submit"
    else:
        jobs = json.loads(a.batch.read_text()) if a.batch else [{"url": a.url, "company": a.company, "role": a.role, "kind": a.kind}]
        mode = "plan" if a.plan else "fill"
    if not all(j.get("url") and j.get("company") and j.get("role") for j in jobs):
        sys.exit("each job needs url, company and role")
    res = run(jobs, mode, headless=a.headless or a.plan)
    print(json.dumps({s: sum(r["status"] == s for r in res) for s in sorted({r["status"] for r in res})}))


if __name__ == "__main__":
    main()
