"""Module 3: evidence ranking. Decides which real project answers a given question for a given job.

The bug this module exists to fix: the old answer step picked projects by raw keyword overlap, so a
question like "most impactful thing you've built" got the bakery website because the JD said
"deploy". Ranking now combines three things, in this order of authority:

1. Hard constraints from the question (a "Python pipeline" question can only be answered with a
   Python project; an LLM question only with an LLM project).
2. Showcase rank, set by hand in the knowledge graph (Kepler first). Impact-style questions only
   draw from the top of it.
3. Relevance to the JD (weighted keyword coverage), which breaks ties and orders supporting projects.
"""
from __future__ import annotations

from . import keywords

# question intent -> skills a project must have (any of) to be used as the lead answer
REQUIRES = {
    "pipeline_scale": {"python"},
    "llm_production": {"llm"},
    "android_project": {"android", "kotlin"},
    "frontend_project": {"react", "next.js", "svelte", "typescript"},
}
# intents where the lead project must be one of the top-N showcase projects
IMPACT_INTENTS = {"impact_project", "hardest_challenge", "project_generic", "why_you", "success_measure",
                  "cover_letter", "why_company", "additional_info", "alt_company", "free_text"}
SHOWCASE_TOP = 3


def relevance(project: dict, jd_terms: dict[str, int]) -> float:
    if not jd_terms:
        return 0.0
    cv = {s: 1 for s in project.get("skills", [])}
    cv.update(keywords.extract(" ".join(project.get("features", []))))
    return keywords.coverage(jd_terms, cv)


def rank(projects: list[dict], jd_text: str = "", intent: str = "free_text") -> list[dict]:
    """Projects best-first for this intent and JD. Every project returned is real; order is the decision."""
    jd_terms = keywords.extract(jd_text)
    need = REQUIRES.get(intent)
    pool = [p for p in projects if not need or need & set(p.get("skills", []))]
    if not pool:
        pool = list(projects)

    def score(p: dict) -> float:
        r = p.get("showcase_rank", 99)
        showcase = 1.0 / r                     # 1.0, 0.5, 0.33, ...
        return 0.65 * showcase + 0.35 * relevance(p, jd_terms)

    ordered = sorted(pool, key=score, reverse=True)
    if intent in IMPACT_INTENTS:
        top = [p for p in ordered if p.get("showcase_rank", 99) <= SHOWCASE_TOP]
        ordered = top + [p for p in ordered if p not in top]
    return ordered


def lead(projects: list[dict], jd_text: str = "", intent: str = "free_text") -> dict:
    return rank(projects, jd_text, intent)[0]


def allowed_leads(projects: list[dict], jd_text: str = "", intent: str = "free_text") -> set[str]:
    """Project ids an answer may open with. The checker rejects an answer led by anything else."""
    ordered = rank(projects, jd_text, intent)
    if intent in IMPACT_INTENTS and not REQUIRES.get(intent):
        return {p["id"] for p in ordered if p.get("showcase_rank", 99) <= SHOWCASE_TOP}
    return {p["id"] for p in ordered[:2]}
