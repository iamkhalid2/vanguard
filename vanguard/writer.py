"""Module 5a: write-up writer. Produces the paragraph answers (why us, most impactful project, how you
knew it worked, ...) from real evidence only.

Three sources, best first:
1. An authored answer for this job (config/answers/<job_id>.json), written by Claude in the daily run
   from the brief `brief()` returns, or by Khalid.
2. Claude through the API, if ANTHROPIC_API_KEY is set, given the same brief.
3. The composer below: deterministic paragraphs assembled from the knowledge graph's project stories.
   Grounded and on-topic for every intent it knows; `free_text` questions it cannot answer well, so
   those come back as needs_writer and block the job until source 1 or 2 fills them.

Whatever the source, answers.check_writeup() is the gate.
"""
from __future__ import annotations

import json
import re

from . import evidence, llm
from .profile import Profile

WORDS = {   # intent -> (min, max) words
    "why_company": (70, 200), "impact_project": (90, 220), "success_measure": (45, 170),
    "product_usage": (30, 150), "hardest_challenge": (80, 220), "llm_production": (70, 220),
    "pipeline_scale": (70, 220), "why_you": (55, 190), "alt_company": (45, 170), "cover_letter": (110, 250),
    "additional_info": (20, 150), "project_generic": (80, 220), "android_project": (60, 220),
    "frontend_project": (60, 220), "free_text": (25, 220),
}
COMPOSABLE = set(WORDS) - {"free_text"}

YOU_WILL = re.compile(r"\byou('ll| will) ([^.!?\n]{12,220})[.!?]", re.I)
WE_BUILD = re.compile(r"\b(we('re| are) (building|on a mission to)[^.!?\n]{8,200})[.!?]", re.I)
HINTS = {   # intent -> lexicon terms that make a highlight relevant even when the JD doesn't say them
    "llm_production": "llm guardrails prompt engineering openrouter agents structured outputs gemini",
    "pipeline_scale": "python machine learning transformers data pipelines testing",
    "success_measure": "testing unit testing",
    "hardest_challenge": "architecture testing",
    "android_project": "android kotlin jetpack compose",
    "frontend_project": "react next.js typescript frontend",
}


def jd_focus(jd_text: str, max_words: int = 26) -> str:
    """What the JD says the hire will do, as a quotable clause ("build ..."). Empty if it says nothing usable."""
    m = YOU_WILL.search(jd_text or "")
    if not m:
        return ""
    words = m.group(2).strip().split()
    s = " ".join(words[:max_words]).rstrip(",;:")
    s = re.sub(r"\bour\b", "its", s, flags=re.I)
    s = re.sub(r"\b(with|for|join) us\b", r"\1 the team", s, flags=re.I)
    return s


def jd_mission(jd_text: str) -> str:
    m = WE_BUILD.search(jd_text or "")
    return " ".join(m.group(1).split()[:24]) if m else ""


def _cap(s: str) -> str:
    return s[:1].upper() + s[1:]


def _story(p: dict, key: str) -> str:
    return (p.get("story") or {}).get(key, "")


def _name(p: dict) -> str:
    return _story(p, "display") or p["name"]


def _intro(p: dict) -> str:
    """'Kepler, my open-source Android app ...' : the project's name and one-line description."""
    hook = _story(p, "hook") or (p.get("features") or [""])[0].rstrip(".")
    return f"{_name(p)}, {hook}"


def _highlights(p: dict, jd_text: str, intent: str, n: int = 2, exclude: tuple = ()) -> list[str]:
    """The project's most relevant highlight sentences for this JD and question, best first."""
    from . import keywords
    want = set(keywords.extract(jd_text)) | set(keywords.extract(HINTS.get(intent, "")))
    told = set(re.findall(r"[a-z]{4,}", " ".join(exclude).lower()))
    fresh = lambda h: len(set(re.findall(r"[a-z]{4,}", h.lower())) & told) <= 0.5 * len(set(re.findall(r"[a-z]{4,}", h.lower())))
    pool = [h for h in (_story(p, "highlights") or []) if fresh(h)]
    if not pool:
        pool = [f.rstrip(".") + "." for f in p.get("features", [])]
    scored = sorted(enumerate(pool), key=lambda t: (-len(want & set(keywords.extract(t[1]))), t[0]))
    return [h for _, h in scored[:n]]


def _lead_and_support(prof: Profile, jd_text: str, intent: str) -> tuple[dict, dict]:
    ranked = evidence.rank(prof.projects, jd_text, intent)
    lead = ranked[0]
    support = next((p for p in ranked[1:] if p.get("story")), ranked[1] if len(ranked) > 1 else lead)
    return lead, support


def _problem(p: dict) -> str:
    if not _story(p, "hard_problem"):
        return ""
    return f"The hard problem was {_story(p, 'hard_problem')}. I solved it with {_story(p, 'solution')}."


def compose(intent: str, prof: Profile, company: str, role: str, jd_text: str = "") -> str:
    """Deterministic, grounded answer for a known intent. Raises KeyError for intents it cannot write."""
    if intent not in COMPOSABLE:
        raise KeyError(intent)
    lead, support = _lead_and_support(prof, jd_text, intent)
    focus, mission = jd_focus(jd_text), jd_mission(jd_text)
    me = (f"I graduated in {prof.grad_year} with a {prof.degree} and work as an {prof.title} at {prof.employer} "
          f"in London, so my engineering so far is self-directed and public on GitHub.")
    hl = lambda p, n=2: " ".join(_highlights(p, jd_text, intent, n))

    if intent == "why_company":
        opener = (f"{company} says the {role} will {focus}, and that is the kind of work I already choose to do."
                  if focus else f"{_cap(mission)}, and that is the kind of product I already choose to build."
                  if mission else f"{company}'s {role} role is the work I already choose to do in my own time.")
        return " ".join([
            opener,
            f"My strongest example is {_intro(lead)}.", hl(lead, 1),
            f"I also built {_intro(support)}.",
            f"Why now: {me} I want to do this work full time on a team that ships to real users, and {company} is where I want that to be.",
        ])
    if intent in ("impact_project", "project_generic", "android_project", "frontend_project"):
        told = (_problem(lead),)
        return " ".join([f"{_cap(_intro(lead))}.", _story(lead, "contribution") + ".", _problem(lead),
                         *_highlights(lead, jd_text, intent, 2, told)]).replace(" . ", " ")
    if intent == "success_measure":
        return _story(lead, "verification") or hl(lead, 2)
    if intent == "hardest_challenge":
        return " ".join([
            f"In {_intro(lead)}, the hardest problem was {_story(lead, 'hard_problem')}.",
            f"I solved it with {_story(lead, 'solution')}.", _story(lead, "verification"),
        ])
    if intent == "product_usage":
        hit = next((p for p in prof.projects if any(company.lower() in s for s in p.get("skills", []))), None)
        if hit:
            return f"Yes, in {_intro(hit)}. {hl(hit, 1)}"
        return (f"Not in a shipped project yet. The closest work I have done is in {_intro(lead)}. "
                f"{hl(lead, 1)} That is where I would try {company} first.")
    if intent == "llm_production":
        return " ".join([
            "Not in a commercial production setting; my LLM work is in personal projects, all public on GitHub.",
            f"The main one is {_intro(lead)}.", hl(lead, 2),
            f"Before that I built {_intro(support)}.",
        ])
    if intent == "pipeline_scale":
        return " ".join([
            f"My most demanding Python pipeline is {_intro(lead)}.", hl(lead, 2),
            "It is a personal project, so the scale is a single-user workload rather than production traffic.",
        ])
    if intent == "why_you":
        return " ".join([
            f"{company} would get someone who has designed and shipped a complete product alone: {_intro(lead)}.",
            f"In it I built {_story(lead, 'solution')}.",
            f"I also built {_intro(support)}.",
            "I learn from code and tests rather than tutorials, and I want to do that full time.",
        ])
    if intent == "alt_company":
        return " ".join([
            "A small product team building software people use every day, where AI meets a real interface such as voice or reading.",
            f"That is what I build in my own time: {_intro(lead)}, and {_intro(support)}.",
        ])
    if intent == "cover_letter":
        opener = (f"The {role} role at {company} is to {focus}." if focus
                  else f"I am applying for the {role} role at {company}.")
        return "\n\n".join([
            opener + " That is the kind of work I have been doing on my own.",
            f"My strongest example is {_intro(lead)}. {_problem(lead)} {hl(lead, 1)}",
            f"I also built {_intro(support)}. {hl(support, 1)}",
            me + f" I would welcome a conversation about the {role} role.",
        ])
    if intent == "additional_info":
        links = [p["repo"] for p in prof.showcase[:2] if p.get("repo")]
        return (f"I have the right to work in the UK until {prof.rtw_until}. "
                f"My strongest work is public: {' and '.join(links)}, and everything else is on {prof.github}.")
    raise KeyError(intent)


# --------------------------------------------------------------------------- briefs for Claude

RULES = [
    "First person, plain and specific. No clichés (passionate, team player, I am writing to express).",
    "Use only facts in `evidence`. Name the JD's technologies only as theirs, never as things you used, unless evidence has them.",
    "No commercial software engineering experience: never imply production, users, teams or employers that the evidence does not state.",
    "Open with the lead project given (Kepler is the flagship). Never lead with a small-business website.",
    "Answer the question actually asked, tie it to this company and role, and stay within the word range.",
]


def brief(intent: str, label: str, prof: Profile, company: str, role: str, jd_text: str) -> dict:
    lead, support = _lead_and_support(prof, jd_text, intent)

    def facts(p):
        return {k: p.get(k) for k in ("name", "repo", "demo", "skills", "features", "metrics", "problem_solution", "story")}
    lo, hi = WORDS.get(intent, WORDS["free_text"])
    return {"company": company, "role": role, "question": label, "intent": intent, "words": [lo, hi],
            "jd_focus": jd_focus(jd_text), "jd_mission": jd_mission(jd_text), "jd_excerpt": (jd_text or "")[:2500],
            "candidate": {"grad_year": prof.grad_year, "degree": prof.degree, "job": f"{prof.title}, {prof.employer}",
                          "commercial_swe_years": prof.commercial_swe_years, "right_to_work_until": prof.rtw_until},
            "lead": facts(lead), "support": facts(support), "rules": RULES}


def llm_write(b: dict) -> str | None:
    if not llm.available():
        return None
    out = llm.complete_json(
        "You write one job-application answer. Return JSON {\"answer\": str}. Follow every rule in the brief.",
        json.dumps(b), max_tokens=900)
    return (out.get("answer") or "").strip() or None
