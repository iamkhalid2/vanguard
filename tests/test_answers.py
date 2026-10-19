"""Modules 1, 3, 5: profile, evidence ranking, writer and answer planner/checker.

The regressions pinned here are the ones Khalid hit on 2026-10-19: the ElevenLabs "most impactful
thing you've built" answer was the bakery website, and optional write-ups were left blank.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from vanguard import answers, evidence, profile, writer

P = profile.load(private_path=Path("/nonexistent"))
JD_EL = ("ElevenLabs is building the voice layer of the internet. As a Full-Stack Engineer you will build product features "
         "across our React and TypeScript frontend and Python backend, shipping text to speech and voice agents. "
         "We deploy on Vercel and care about performance budgets, SEO and CI.")
JOB_EL = {"id": "test-elevenlabs", "company": "ElevenLabs", "role": "Full-Stack Engineer", "kind": "fullstack",
          "jd_text": JD_EL, "resume_path": "assets/resumes/Mohammad-Khalid-Resume-Frontend-Kepler.pdf", "use_llm": False}
OLD_BAKERY_ANSWER = ("Bustan Bakers website (bustanbakers.com), a statically generated Next.js site (menu, products, locations, "
                     "product enquiry flow) deployed on Vercel. I built and deployed it, including a GitHub Actions pipeline "
                     "(lint, typecheck, build, tests) that fails the build if any route exceeds 178 KB of gzipped JavaScript "
                     "or 900 KB of transfer, automated per-route SEO checks and an AVIF image pipeline with five responsive widths.")

# The ElevenLabs Full-Stack form as it was on 2026-10-19 (labels from the screenshots).
EL_FORM = [
    {"key": "f1", "kind": "text", "label": "Name", "required": True},
    {"key": "f2", "kind": "text", "label": "Email", "required": True},
    {"key": "f3", "kind": "file", "label": "Resume", "required": True},
    {"key": "f4", "kind": "combobox", "label": "Where are you located?", "required": True},
    {"key": "f5", "kind": "radio", "label": "How did you hear about ElevenLabs?", "required": False,
     "options": ["Job board", "Social media (LinkedIn, Instagram, X etc)", "In person event", "Referral",
                 "I was reached out to", "Other (please specify)"]},
    {"key": "f6", "kind": "text", "label": "If other, please specify below", "required": False},
    {"key": "f7", "kind": "text", "label": "Link to your Github profile", "required": True},
    {"key": "f8", "kind": "text", "label": "Link to your LinkedIn profile", "required": True},
    {"key": "f9", "kind": "textarea", "label": "Why ElevenLabs, and why now?", "required": False},
    {"key": "f10", "kind": "textarea", "label": "What's the most impactful thing you've built? What was your specific contribution?", "required": False},
    {"key": "f11", "kind": "textarea", "label": "How did you know it worked? What did success actually look like?", "required": False},
    {"key": "f12", "kind": "textarea", "label": "Have you used ElevenLabs - even in a personal or side project? What did you build or explore?", "required": False},
]


def test_profile_valid():
    assert profile.validate(P) == []
    assert P.showcase[0]["id"] == "kepler"


@pytest.mark.parametrize("intent", ["impact_project", "hardest_challenge", "project_generic", "why_you", "why_company"])
def test_impact_questions_never_lead_with_small_sites(intent):
    # a JD full of web-performance words used to pull the bakery site to the top
    assert evidence.lead(P.projects, JD_EL, intent)["id"] == "kepler"
    assert "bustan-bakers" not in evidence.allowed_leads(P.projects, JD_EL, intent)


def test_constrained_questions_respect_the_stack():
    assert "python" in evidence.lead(P.projects, JD_EL, "pipeline_scale")["skills"]
    assert "llm" in evidence.lead(P.projects, JD_EL, "llm_production")["skills"]


def test_checker_rejects_the_bakery_answer():
    probs = answers.check_writeup(OLD_BAKERY_ANSWER, "impact_project", P, "ElevenLabs", JD_EL)
    assert any("leads with bustan-bakers" in p for p in probs)


@pytest.mark.parametrize("intent", sorted(writer.COMPOSABLE))
def test_composer_output_passes_its_own_checker(intent):
    text = writer.compose(intent, P, "ElevenLabs", "Full-Stack Engineer", JD_EL)
    assert answers.check_writeup(text, intent, P, "ElevenLabs", JD_EL) == []


def test_checker_catches_invention():
    probs = answers.check_writeup("At ElevenLabs-scale I served 50,000 users with Kubernetes. I am passionate and a team player. "
                                  "Kepler is great. " * 6, "why_company", P, "ElevenLabs", JD_EL)
    assert any("kubernetes" in p for p in probs) and any("50,000" in p for p in probs) and any("cliché" in p for p in probs)
    assert answers.check_writeup("Yes, I built a dubbing tool with ElevenLabs in Kepler and loved it. " * 3,
                                 "product_usage", P, "ElevenLabs", JD_EL)


def test_plan_fills_every_elevenlabs_field():
    items = answers.plan(EL_FORM, JOB_EL, P)
    by = {i.key: i for i in items}
    assert answers.check(items, P, JOB_EL) == []
    assert by["f1"].value == "Mohammad Khalid" and by["f4"].value == "London, UK"
    assert by["f5"].value == "Job board" and by["f6"].status == "skip"     # Other not picked -> stays empty
    for k in ("f9", "f10", "f11", "f12"):                                  # optional write-ups are filled
        assert by[k].status == "ok" and len(str(by[k].value).split()) > 30, k
    assert by["f10"].value.startswith("Kepler")
    assert "Not in a shipped project yet" in by["f12"].value


def test_authored_answers_win_and_are_still_checked(tmp_path, monkeypatch):
    monkeypatch.setattr(answers, "ANSWERS_DIR", tmp_path)
    (tmp_path / "test-elevenlabs.json").write_text('{"impact_project": "' + OLD_BAKERY_ANSWER + '"}')
    items = answers.plan(EL_FORM, JOB_EL, P)
    item = next(i for i in items if i.key == "f10")
    assert item.source == "authored"
    assert any("bustan" in p for p in answers.check(items, P, JOB_EL))


def test_legal_declarations_wait_for_khalid():
    form = [{"key": "a", "kind": "radio", "label": "Have you been convicted of any criminal offences that are not yet spent?",
             "required": True, "options": ["Yes", "No"]}]
    items = answers.plan(form, JOB_EL, P)
    assert items[0].status == "needs_khalid"
    assert answers.check(items, P, JOB_EL)


def test_unknown_free_text_blocks_until_written(tmp_path, monkeypatch):
    monkeypatch.setattr(answers, "ANSWERS_DIR", tmp_path)
    form = [{"key": "q", "kind": "textarea", "label": "Describe your ideal code review culture.", "required": False}]
    items = answers.plan(form, JOB_EL, P)
    assert items[0].status == "needs_writer"
    (tmp_path / "test-elevenlabs.json").write_text(
        '{"Describe your ideal code review culture.": "Small pull requests with tests, reviewed the same day. In Kepler every module has unit tests run through Gradle, so I review against failing tests rather than opinions."}')
    items = answers.plan(form, JOB_EL, P)
    assert items[0].status == "ok" and answers.check(items, P, JOB_EL) == []


def test_choices():
    assert answers.pick_option("sponsorship", ["Yes", "No"]) == "Yes"
    assert answers.pick_option("rtw", ["No", "Yes"]) == "Yes"
    assert answers.pick_option("eeo", ["Male", "Female", "I don't wish to answer"]) == "I don't wish to answer"
    assert answers.pick_option("country", ["Select...", "United States", "United Kingdom"]) == "United Kingdom"
    assert answers.pick_option("messaging_opt_in", ["Yes", "No"]) == "No"
    assert answers.pick_preferences(["Backend", "Frontend", "Full-stack", "Mobile", "AI/ML"], "android") == ["Mobile", "AI/ML", "Full-stack"]
