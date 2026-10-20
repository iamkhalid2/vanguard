"""Hard filters applied to every scraped job before any tailoring is spent on it."""
from __future__ import annotations

import re

TITLE_INCLUDE = re.compile(
    r"\b(software|full[- ]?stack|applied ai|ai engineer|ml engineer|machine learning engineer|forward[- ]deployed|"
    r"backend|back[- ]end|frontend|front[- ]end|product engineer|developer|swe)\b", re.I)
TITLE_ENTRY = re.compile(r"\b(graduate|grad|new grad|entry[- ]level|junior|jr\.?|associate|early careers?|l3|e3|swe i\b|engineer i\b|level 1)\b", re.I)
TITLE_EXCLUDE = re.compile(
    r"\b(senior|sr\.?|staff|principal|lead|head of|director|manager|vp|architect|intern(ship)?|placement|"
    r"apprentice|ii|iii|iv|l4|l5|l6|e4|e5|e6|phd)\b", re.I)

LONDON = re.compile(r"\b(london|united kingdom|\buk\b|england|remote[^a-z]*(uk|emea|europe))", re.I)

# "3+ years", "minimum of 4 years", "at least 5 years", "3-5 years"
YEARS = re.compile(r"(?:(?:minimum|at least|min\.?)\s+(?:of\s+)?)?(\d{1,2})\s*(?:\+|-\s*\d{1,2}|to\s+\d{1,2})?\s*\+?\s*years?", re.I)
YEARS_CONTEXT = re.compile(r"experience|commercial|industry|professional|production", re.I)

CLEARANCE = re.compile(r"\b(sc clearance|dv clearance|developed vetting|security clearance|eligible for (sc|dv)|"
                       r"active (sc|dv)|bpss and sc|sole british nationals?|british citizens? only)\b", re.I)
# he has the right to work only until 2028-06-30, so roles that demand it permanently exclude him
PERMANENT_RTW = re.compile(r"\b(permanent|indefinite|unrestricted and permanent) right to work\b|\b(settled status|ilr|indefinite leave to remain) (is )?required\b", re.I)
NO_SPONSOR = re.compile(r"(not able to|unable to|cannot|can't|do not|don't|will not|won't)\s+(offer|provide)?\s*(visa\s+)?sponsor", re.I)
SPONSOR = re.compile(r"(visa sponsorship (is )?(available|offered|provided)|we (can|will) sponsor|sponsorship available)", re.I)


# "graduated in 2025, 2026, or 2027", "graduating between 2025 and 2027", "class of 2026/2027"
GRAD_WINDOW = re.compile(r"(?:graduat\w*|class of)[^.]{0,40}?((?:20\d\d\W{1,6}(?:and\W+|or\W+|to\W+)?)+20\d\d|20\d\d)", re.I)


def grad_years(text: str) -> set[int]:
    """Graduation years a JD accepts. Empty set if it names none."""
    years: set[int] = set()
    for m in GRAD_WINDOW.finditer(text):
        found = [int(y) for y in re.findall(r"20\d\d", m.group(1))]
        if len(found) == 2 and re.search(r"between|to|-|–", m.group(0)):
            found = list(range(min(found), max(found) + 1))
        years.update(found)
    return years


def min_years(text: str) -> float | None:
    """Largest 'N years' figure that appears near an experience phrase. None if the JD states none."""
    best = None
    for m in YEARS.finditer(text):
        window = text[max(0, m.start() - 80): m.end() + 80]
        if YEARS_CONTEXT.search(window):
            n = float(m.group(1))
            if n <= 15:
                best = n if best is None else max(best, n)
    return best


def sponsorship(text: str) -> str:
    if NO_SPONSOR.search(text):
        return "not_offered"
    if SPONSOR.search(text):
        return "offered"
    return "unknown"


def evaluate(job: dict, cfg: dict) -> tuple[bool, str | None, dict]:
    """Return (keep, reason_if_dropped, parsed_fields)."""
    title, desc, loc = job["title"], job["description"], job.get("location") or ""
    parsed = {
        "min_years": min_years(desc),
        "needs_clearance": int(bool(CLEARANCE.search(desc))),
        "sponsorship": sponsorship(desc),
    }
    window = sorted(grad_years(desc))   # not a db column, so kept out of parsed
    if not TITLE_INCLUDE.search(title):
        return False, "role family not targeted", parsed
    if TITLE_EXCLUDE.search(title) and not TITLE_ENTRY.search(title):
        return False, "senior or non-target level in title", parsed
    if not LONDON.search(loc + " " + desc[:400]):
        return False, "not London / UK", parsed
    if parsed["min_years"] is not None and parsed["min_years"] > cfg.get("max_years_required", 3):
        return False, f"requires {parsed['min_years']:g}+ years", parsed
    if parsed["needs_clearance"] and not cfg.get("has_clearance", False):
        return False, "security clearance required", parsed
    if cfg.get("graduation_year") and window and cfg["graduation_year"] not in window:
        return False, f"graduation window {window} excludes {cfg['graduation_year']}", parsed
    if PERMANENT_RTW.search(desc):
        return False, "requires permanent right to work in the UK", parsed
    if cfg.get("drop_if_no_sponsorship") and parsed["sponsorship"] == "not_offered":
        return False, "states no visa sponsorship", parsed
    return True, None, parsed
