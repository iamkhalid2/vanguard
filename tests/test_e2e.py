"""Whole-machine test: board feed -> eligibility filters -> shortlist -> answer plan -> fill + verify on
real-shaped forms in headless Chromium -> human picks ids -> submit -> log -> 2-per-company cap.

Nothing here touches the network: boards are faked and the forms are local fixture files.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pytest

pytest.importorskip("playwright.sync_api")

import apply
from vanguard import db, profile, shortlist, tracker

FIX = ROOT / "tests" / "fixtures" / "forms"
GOOD = ("ExampleVoice is building the voice layer of the internet. You will build product features across our React and "
        "TypeScript frontend and Python backend. 0-2 years of experience. Hybrid in London, UK.")


def board(_token):
    yield {"id": "1", "title": "Full-Stack Engineer (Graduate)", "location": "London, UK", "url": (FIX / "ashby.html").as_uri(), "description": GOOD}
    yield {"id": "2", "title": "Junior Software Engineer", "location": "London", "url": (FIX / "lever.html").as_uri(), "description": GOOD}
    yield {"id": "3", "title": "Software Engineer, New Grad", "location": "London", "url": (FIX / "greenhouse.html").as_uri(), "description": GOOD}
    yield {"id": "4", "title": "Senior Software Engineer", "location": "London", "url": "https://x/4", "description": "6+ years of commercial experience."}
    yield {"id": "5", "title": "Graduate Programme 2027: Software Engineer", "location": "London", "url": "https://x/5",
           "description": "You must have graduated in 2025, 2026 or 2027. Python."}


@pytest.fixture()
def world(tmp_path, monkeypatch):
    import scraper
    path = tmp_path / "v.db"
    real = db.connect
    monkeypatch.setattr(db, "connect", lambda p=path: real(p))
    monkeypatch.setitem(scraper.ADAPTERS, "greenhouse", board)
    monkeypatch.setattr(scraper, "load_targets", lambda: {"filters": {"max_years_required": 3, "graduation_year": 2024}, "companies": [
        {"id": "examplevoice", "name": "ExampleVoice", "tier": "B", "size_bucket": "startup", "ats": "greenhouse", "ats_token": "x"}]})
    monkeypatch.setattr(scraper.time, "sleep", lambda s: None)
    monkeypatch.setattr(scraper.sponsors, "refresh", lambda: None)
    monkeypatch.setattr(scraper.sponsors, "CACHE", Path("/nonexistent/sponsors.csv"))
    monkeypatch.setattr(apply.answers, "ANSWERS_DIR", tmp_path / "answers")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    return scraper, tmp_path / "runs"


def headless(pw):
    return pw.chromium.launch().new_context()


def test_whole_machine(world):
    scraper, runs = world
    stats = scraper.scrape()
    assert stats["kept"] == 3 and stats["filtered"] == 2          # senior and the 2025-27 grad scheme are dropped

    with db.connect() as conn:
        batch = shortlist.from_db(conn, limit=10, runs=runs)
    assert len(batch) == 2                                          # cap: 2 per company even in one batch
    for b in batch:
        b["resume_path"] = "assets/resumes/Mohammad-Khalid-Resume-Frontend-Kepler.pdf"
        b["use_llm"] = False

    prof = profile.load(private_path=Path("/nonexistent"))
    res = apply.run(batch, "fill", runs=runs, prof=prof, launch=headless)
    assert [r["status"] for r in res] == ["filled", "filled"], [(r["status"], r.get("problems")) for r in res]
    ready = json.loads((runs / "ready-to-submit.json").read_text())
    assert len(ready) == 2
    sheet = Path(ready[0]["plan"]).with_suffix(".md").read_text()
    assert "No blocking problems." in sheet and "Kepler" in sheet
    assert all(Path(r["screenshot"]).exists() for r in ready)
    assert tracker.company_count("ExampleVoice", runs) == 0         # filling never uses a slot

    # the human approves one id; only that one is submitted
    res = apply.run([ready[0]["job"]], "submit", runs=runs, prof=prof, launch=headless)
    assert res[0]["status"] == "submitted"
    assert tracker.company_count("ExampleVoice", runs) == 1
    # re-running the same job is refused, and a second submit fills the company's last slot
    assert apply.run([ready[0]["job"]], "fill", runs=runs, prof=prof, launch=headless)[0]["reason"] == "already submitted"
    assert apply.run([ready[1]["job"]], "submit", runs=runs, prof=prof, launch=headless)[0]["status"] == "submitted"
    third = {**batch[0], "url": (FIX / "greenhouse.html").as_uri(), "role": "Another role"}
    r3 = apply.run([third], "fill", runs=runs, prof=prof, launch=headless)[0]
    assert r3.get("reason", "").startswith("cap:"), r3


def test_blocking_plan_never_touches_the_form(world, tmp_path):
    _, runs = world
    form = tmp_path / "declaration.html"
    form.write_text("""<form><label for=n>Full name*</label><input id=n required>
      <fieldset><legend>Have you been convicted of any criminal offences that are not yet spent?*</legend>
      <label><input type=radio name=c required>Yes</label><label><input type=radio name=c>No</label></fieldset>
      <button type=submit>Submit</button></form>""")
    job = {"company": "Bank", "role": "Graduate Engineer", "url": form.as_uri(), "kind": "fullstack",
           "resume_path": "assets/resumes/Mohammad-Khalid-Resume-Frontend-Kepler.pdf", "use_llm": False}
    res = apply.run([job], "fill", runs=runs, prof=profile.load(private_path=Path("/nonexistent")), launch=headless)
    assert res[0]["status"] == "paused"
    assert any("legal declaration" in p or "needs_khalid" in p for p in res[0]["problems"])
    assert not (runs / "screenshots" / f"{res[0]['id']}-filled.png").exists()
