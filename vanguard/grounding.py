"""Zero-hallucination guard. A tailored bullet passes only if every skill term and every number
in it is backed by the knowledge graph entry of the project it describes (or the candidate's
global evidence). Anything else is rejected and the original bullet is kept."""
from __future__ import annotations

import re

from . import keywords

NUM = re.compile(r"\d[\d,.]*")
URL = re.compile(r"https?://\S+|\b[\w.-]+\.(?:com|io|xyz|ai|dev|app)/\S*")


def project_evidence(project: dict) -> str:
    return " ".join([project["name"], " ".join(project["skills"]), " ".join(project["features"]),
                     " ".join(project.get("metrics", [])),
                     " ".join(f"{p['problem']} {p['solution']}" for p in project.get("problem_solution", []))])


def check(bullet: str, project: dict) -> list[str]:
    evidence = URL.sub(" ", project_evidence(project))
    bullet = URL.sub(" ", bullet)
    allowed_terms = set(keywords.extract(evidence))
    violations = [f"unbacked skill: {t}" for t in keywords.extract(bullet) if t not in allowed_terms]
    ev_nums = {n.replace(",", "").rstrip(".") for n in NUM.findall(evidence)}
    for n in NUM.findall(bullet):
        if n.replace(",", "").rstrip(".") not in ev_nums:
            violations.append(f"unbacked number: {n}")
    return violations
