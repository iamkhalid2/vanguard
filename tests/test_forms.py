"""Module 6 tests: read, fill and verify real-shaped ATS forms in headless Chromium.

Fixtures in tests/fixtures/forms copy the DOM shapes of Ashby, Greenhouse and Lever forms Vanguard
met, plus trap.html, which reproduces the 2026-10-19 bug where Khalid's name was typed into a
Location box.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pytest

pw_api = pytest.importorskip("playwright.sync_api")

from vanguard import answers, forms, profile

FIX = ROOT / "tests" / "fixtures" / "forms"
P = profile.load(private_path=Path("/nonexistent"))
JD = ("We are building the voice layer of the internet. You will build product features across our React and "
      "TypeScript frontend and Python backend, shipping text to speech and voice agents.")


def job(company):
    return {"id": f"fixture-{company.lower()}", "company": company, "role": "Full-Stack Engineer", "kind": "fullstack",
            "jd_text": JD, "resume_path": "assets/resumes/Mohammad-Khalid-Resume-Frontend-Kepler.pdf", "use_llm": False}


@pytest.fixture(scope="module")
def browser():
    with pw_api.sync_playwright() as pw:
        b = pw.chromium.launch()
        yield b
        b.close()


def run(browser, name, company):
    page = browser.new_page()
    page.goto((FIX / name).as_uri())
    scope = forms.scope_of(page)
    fields = forms.read(scope)
    j = job(company)
    items = answers.plan(fields, j, P)
    before = forms.readback(scope)
    errors = forms.fill(page, scope, items, ROOT)
    return page, scope, fields, items, errors, forms.verify(scope, items, before), j


@pytest.mark.parametrize("name,company", [("ashby.html", "ExampleVoice"), ("greenhouse.html", "Exampleco"), ("lever.html", "Exampleco")])
def test_fill_verify_submit(browser, name, company):
    page, scope, fields, items, errors, probs, j = run(browser, name, company)
    assert errors == [] and probs == [], (errors, probs)
    assert answers.check(items, P, j) == []
    after = forms.readback(scope)
    for it in items:   # every field that should have an answer has one
        if it.status == "ok":
            assert after.get(it.key) not in ("", [], None, False), it.label
    assert forms.submit(page, wait_s=5) == "submitted"


def test_ashby_details(browser):
    page, scope, fields, items, errors, probs, j = run(browser, "ashby.html", "ExampleVoice")
    after = forms.readback(scope)
    by = {i.intent: i for i in items}
    assert after[by["location"].key] == "London, England, United Kingdom"
    assert after[by["how_heard"].key] == ["Job board"]
    assert after[by["how_heard_other"].key] == ""
    assert after[by["impact_project"].key].startswith("Kepler")
    assert after[by["resume_file"].key] == ["Mohammad-Khalid-Resume-Frontend-Kepler.pdf"]


def test_greenhouse_choices(browser):
    page, scope, fields, items, errors, probs, j = run(browser, "greenhouse.html", "Exampleco")
    after = forms.readback(scope)
    by = {i.intent: i for i in items}
    assert after[by["sponsorship"].key] == "Yes"          # truthful: needs sponsorship after June 2028
    assert after[by["rtw"].key] == "Yes"
    assert after[by["messaging_opt_in"].key] == "No"
    assert after[by["eeo"].key] == "I don't wish to answer"
    assert after[by["privacy_consent"].key] is True


def test_trap_labels_are_never_borrowed(browser):
    page, scope, fields, items, errors, probs, j = run(browser, "trap.html", "Exampleco")
    labels = {f["key"]: f["label"] for f in fields}
    assert labels["vg0"] == "Name" and labels["vg1"] == "Location" and labels["vg2"] == ""
    after = forms.readback(scope)
    assert after["vg0"] == "Mohammad Khalid" and after["vg1"] == "London, UK"
    assert after["vg2"] == ""                                # unlabelled: never typed into
    assert after["vg3"] == ["Yes"]                           # visually hidden Workable radios
    assert set(after["vg4"]) == {"Full-stack", "Frontend", "AI/ML"}
    assert errors == [] and probs == []


def test_verify_catches_a_wrong_field(browser):
    """Re-create the old failure by hand and check verify() refuses it."""
    page, scope, fields, items, errors, probs, j = run(browser, "trap.html", "Exampleco")
    before = forms.readback(scope)
    scope.locator("[data-vg='vg1']").fill("Mohammad Khalid")   # name typed into Location
    scope.locator("[data-vg='vg2']").fill("oops")              # and into an unplanned field
    bad = forms.verify(scope, items, {**before, "vg2": ""})
    assert any("Location" in p for p in bad) and any("changed but not planned" in p for p in bad)


def test_test_run_regressions_2026_10_19(browser):
    """Errors the first plan-sheet run found on Stripe, Ashby and Palantir forms."""
    page = browser.new_page()
    page.goto((FIX / "stripe_like.html").as_uri())
    fields = forms.read(forms.scope_of(page))
    labels = [f["label"] for f in fields]
    assert "" not in labels                                   # the hidden react-select input is not a field
    heard = next(f for f in fields if f["label"].startswith("How did you hear"))
    assert heard["kind"] == "radio" and len(heard["options"]) == 3   # unnamed radios read as one group
    items = {i.label: i for i in answers.plan(fields, job("ExampleVoice"), P)}
    assert items["Do you have the right to work in the UK?*"].value == "Yes"   # an option, not a sentence
    assert items["How did you hear about ExampleVoice?"].value == "Job board"
    assert items["Which university did you last attend?"].value == "Other (School Not Listed)"
    assert items["High School Name*"].status == "needs_khalid"          # never guessed
