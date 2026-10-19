"""Module 1: candidate profile. One typed view over data/knowledge_graph.json, config/base_resume.txt,
config/answer_defaults.json and the never-committed ~/.vanguard/private.json.

Every other module reads candidate facts from here, so a fact is defined once. `validate()` is the
module's own check: it lists every missing or malformed fact instead of failing later inside a form.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
KG_PATH = ROOT / "data" / "knowledge_graph.json"
RESUME_TXT = ROOT / "config" / "base_resume.txt"
DEFAULTS_PATH = ROOT / "config" / "answer_defaults.json"
PRIVATE_PATH = Path.home() / ".vanguard" / "private.json"

PROJECT_REQUIRED = ("id", "name", "skills", "features", "showcase_rank")
STORY_KEYS = ("hook", "contribution", "hard_problem", "solution", "verification")


@dataclass
class Profile:
    first: str
    last: str
    full: str
    email: str
    phone: str
    city: str
    country: str
    location: str
    linkedin: str
    github: str
    website: str
    degree: str
    school: str
    grad_year: int
    employer: str
    title: str
    commercial_swe_years: int
    rtw_until: str                 # "30 June 2028"
    address: str = ""
    postcode: str = ""
    defaults: dict = field(default_factory=dict)
    projects: list = field(default_factory=list)
    experience: list = field(default_factory=list)

    @property
    def showcase(self) -> list[dict]:
        """Projects in the order he would show them, best first."""
        return sorted(self.projects, key=lambda p: p.get("showcase_rank", 99))

    def project(self, pid: str) -> dict:
        return next(p for p in self.projects if p["id"] == pid)


def _human_date(iso: str) -> str:
    y, m, d = iso.split("-")
    months = ["January", "February", "March", "April", "May", "June", "July", "August", "September",
              "October", "November", "December"]
    return f"{int(d)} {months[int(m) - 1]} {y}"


def load(kg_path: Path = KG_PATH, private_path: Path = PRIVATE_PATH, defaults_path: Path = DEFAULTS_PATH) -> Profile:
    kg = json.loads(kg_path.read_text())
    c = kg["candidate"]
    resume = RESUME_TXT.read_text() if RESUME_TXT.exists() else ""
    phone = re.search(r"\+44[\d ]{9,14}\d", resume)
    private = json.loads(private_path.read_text()) if private_path.exists() else {}
    defaults = json.loads(defaults_path.read_text()) if defaults_path.exists() else {}
    first, *rest = c["name"].split()
    edu, job = c["education"][0], c["experience"][0]
    return Profile(
        first=first, last=" ".join(rest), full=c["name"], email=c["email"],
        phone=phone.group(0) if phone else private.get("phone", ""),
        city="London", country="United Kingdom", location=c.get("location", "London, UK"),
        linkedin=c.get("linkedin", ""), github=f"https://github.com/{c['github']}", website=c.get("website", ""),
        degree=edu["degree"], school=edu["school"], grad_year=int(edu["years"].split("-")[-1]),
        employer=job["company"], title=job["title"], commercial_swe_years=int(c.get("commercial_swe_years", 0)),
        rtw_until=_human_date(c.get("needs_sponsorship_after", "2028-06-30")),
        address=private.get("address", ""), postcode=private.get("postcode", ""),
        defaults=defaults, projects=kg["projects"], experience=c["experience"],
    )


def validate(p: Profile) -> list[str]:
    """Problems that would make a form answer wrong or empty. Empty list means the profile is usable."""
    out = []
    for name in ("first", "last", "email", "phone", "linkedin", "github", "degree", "school"):
        if not getattr(p, name):
            out.append(f"missing {name}")
    if p.email and not re.fullmatch(r"[^@\s]+@[^@\s]+\.\w+", p.email):
        out.append(f"bad email: {p.email}")
    if not (2000 < p.grad_year < 2100):
        out.append(f"bad grad_year: {p.grad_year}")
    ranks = [pr.get("showcase_rank") for pr in p.projects]
    for pr in p.projects:
        for k in PROJECT_REQUIRED:
            if k not in pr:
                out.append(f"project {pr.get('id', '?')}: missing {k}")
    if len([r for r in ranks if r is not None]) != len({r for r in ranks if r is not None}):
        out.append("showcase_rank values must be unique")
    for pr in p.showcase[:3]:
        missing = [k for k in STORY_KEYS if not (pr.get("story") or {}).get(k)]
        if missing:
            out.append(f"top project {pr['id']}: story missing {missing}")
    for k in ("notice_period", "salary", "start_date", "how_heard"):
        if not p.defaults.get(k):
            out.append(f"answer_defaults.json: missing {k}")
    return out


if __name__ == "__main__":
    problems = validate(load())
    print("\n".join(problems) or "profile OK")
