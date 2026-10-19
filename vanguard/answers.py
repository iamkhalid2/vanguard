"""Module 5b: answer planner and checker.

plan(fields, job, profile) turns a form's field list (from forms.extract or a saved schema) into one
decision per field: what to type, which option to pick, which file to attach, or why it stays empty.
check(plan) is the gate before anything touches a browser. A job is ready only when check() returns
no blocking problems.

Policy (Khalid, 2026-10-19): every field gets an answer, optional ones too, tailored to the company
and role, with Kepler as the flagship evidence. Fields stay empty only when filling them would be
wrong: voluntary diversity questions (most private option instead), marketing opt-ins (No), a
conditional "if other" box when Other wasn't picked, a photo upload, and legal declarations, which
only Khalid can make.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import evidence, grounding, keywords, questions, writer
from .profile import ROOT, Profile

ANSWERS_DIR = ROOT / "config" / "answers"

# facts that only the profile may answer; a job's authored answers can override anything else
IDENTITY = {"first_name", "last_name", "full_name", "preferred_name", "email", "phone", "linkedin", "github",
            "website", "links_combined", "school", "degree", "grad_year", "current_employer", "current_title"}
# facts Vanguard has no source for; never guessed, so they go to Khalid when required and stay empty otherwise
NEVER_GUESS = {"pronunciation", "high_school", "high_school_year"}
# short values typed into searchable dropdowns (comboboxes), whose options are only known once opened
COMBO_VALUES = {"rtw": "Yes", "sponsorship": "Yes", "office": "Yes", "relocation": "No", "enrolled_recently": "No",
                "previously_employed_here": "No", "clearance": "No", "messaging_opt_in": "No", "deadline": "No",
                "recording_consent": "Yes", "privacy_consent": "Yes", "school": "Other", "degree": "Bachelor",
                "how_heard": "Job board", "years_experience": "0"}
PREFERENCE_TERMS = {"android": ["Mobile", "Android", "AI", "Machine Learning", "Full", "Product", "Front"],
                    "ai": ["AI", "Machine Learning", "Full", "Product", "Back", "Mobile"],
                    "fullstack": ["Full", "Product", "Front", "AI", "Back"]}
# fields that are correct to leave empty when optional
INTENTIONAL_SKIP = {"eeo", "pronouns", "photo", "how_heard_other", "referral", "unknown_choice"}

CHOICES = {   # intent -> option regexes in preference order
    "rtw": [r"^yes\b"],
    "sponsorship": [r"future|later|after|not (now|currently|at the moment)", r"^yes\b"],
    "office": [r"^yes\b"],
    "relocation": [r"^no\b|not (required|needed)|already"],
    "enrolled_recently": [r"^no\b"],
    "previously_employed_here": [r"^no\b"],
    "clearance": [r"^no\b|none"],
    "messaging_opt_in": [r"^no\b|do not|don.t|opt.?out"],
    "recording_consent": [r"^yes\b|consent|agree|happy|ok"],
    "eeo": [r"decline|prefer not|don.t wish|do not wish|not to (say|disclose|answer)|rather not|not specified"],
    "how_heard": [r"careers? (page|site)|company (website|site)|\bwebsite\b|job board|linkedin|^other\b"],
    "country": [r"^united kingdom$|^uk$|great britain|united kingdom"],
    "location": [r"london"],
    "city": [r"london"],
    "years_experience": [r"^0\b|^<\s*1|less than (1|one)|none|no experience|^0\s*-\s*1"],
    "degree": [r"bachelor|\bbsc\b|undergraduate|\bba\b|b\.sc"],
    "school": [r"^other\b|not listed|isn.t listed|not on (the|this) list"],
    "deadline": [r"^no\b"],
    "privacy_consent": [r"^yes\b|agree|accept|consent|i have read"],
    "grad_year": [],          # filled from profile below
    "high_school_year": [],
}
BANNED = re.compile(r"\b(passion(ate)?|team player|hard-?working|i am writing to|express my (interest|passion)|synergy|"
                    r"fast-paced|dream job|rock ?star|ninja|leverage my|perfect fit|go-getter)\b", re.I)
FALSE_CLAIM = re.compile(r"\b(in production at|at work i (built|shipped)|my (engineering )?team (at|built)|led a team|"
                         r"managed (a|the) team|\d+\+? years of (commercial|professional|industry) (software )?experience|"
                         r"(millions|thousands|hundreds) of (users|customers))\b", re.I)
NEGATION = re.compile(r"\b(not|haven.t|have not|never|yet)\b", re.I)
COMPANY_INTENTS = {"why_company", "why_you", "cover_letter", "product_usage"}


@dataclass
class Item:
    key: str
    label: str
    kind: str
    intent: str
    required: bool
    action: str = "skip"          # type | select | check | upload | sight (visual filler only) | skip
    value: str | list = ""
    source: str = ""              # fact | default | choice | authored | llm | composer | file
    status: str = "ok"            # ok | skip | needs_writer | needs_khalid
    problems: list = field(default_factory=list)


# --------------------------------------------------------------------------- facts

def facts(p: Profile) -> dict[str, str]:
    d = p.defaults
    links = ", ".join(x for x in (p.linkedin, p.github, p.website) if x)
    return {
        "first_name": p.first, "last_name": p.last, "full_name": p.full, "preferred_name": p.first,
        "pronunciation": "", "email": p.email, "phone": p.phone,
        "linkedin": p.linkedin, "github": p.github, "website": p.website or p.github, "links_combined": links,
        "city": p.city, "country": p.country, "location": p.location, "address": p.address, "postcode": p.postcode,
        "current_employer": p.employer, "current_title": p.title, "school": p.school, "degree": p.degree,
        "grad_year": str(p.grad_year), "high_school": "", "high_school_year": "", "test_scores": d.get("test_scores", ""),
        "years_experience": str(p.commercial_swe_years),
        "rtw": (f"Yes. I have the right to work in the UK until {p.rtw_until}, with no restrictions before then. "
                f"I would need sponsorship after that date."),
        "sponsorship": (f"Not before {p.rtw_until}. I have the right to work in the UK until then and would need "
                        f"sponsorship after that date."),
        "rtw_document": d.get("rtw_document", ""), "clearance": "No",
        "office": d.get("office", ""), "relocation": d.get("relocation", ""),
        "notice_period": d.get("notice_period", ""), "salary": d.get("salary", ""),
        "start_date": d.get("start_date", ""), "deadline": d.get("deadline", ""), "how_heard": d.get("how_heard", ""),
        "how_heard_other": d.get("how_heard", ""), "previously_employed_here": d.get("previously_employed_here", "No"),
        "enrolled_recently": "No", "recording_consent": d.get("recording_consent", "Yes"),
        "messaging_opt_in": d.get("messaging_opt_in", "No"), "referral": "",
    }


def norm_opt(s: str) -> str:
    return re.sub(r"\W+", " ", s or "").strip().lower()


def pick_option(intent: str, options: list[str], want: str = "") -> str | None:
    opts = [o.strip() for o in options if o and o.strip() and not re.fullmatch(r"select\.*|choose\.*|--+|please select\.*", o.strip(), re.I)]
    if want:
        exact = next((o for o in opts if o.lower() == want.lower()), None)
        if exact:
            return exact
    for rx in CHOICES.get(intent, []):
        hit = next((o for o in opts if re.search(rx, o, re.I)), None)
        if hit:
            return hit
    if want:
        return next((o for o in opts if want.lower() in o.lower()), None)
    return None


def pick_preferences(options: list[str], kind: str, n: int = 3) -> list[str]:
    order = {"android": [r"android|mobile", r"applied ai|\bai\b|machine learning|ml", r"full.?stack|product", r"front.?end"],
             "ai": [r"applied ai|\bai\b|machine learning|ml|llm", r"full.?stack|product", r"back.?end|infra|data", r"android|mobile"],
             "fullstack": [r"full.?stack|product", r"front.?end|web", r"applied ai|\bai\b|ml", r"back.?end"]}.get(kind, [])
    out = []
    for rx in order:
        for o in options:
            if re.search(rx, o, re.I) and o not in out:
                out.append(o)
                break
    return out[:n] or options[:1]


# --------------------------------------------------------------------------- authored answers

def authored(job: dict) -> dict[str, str]:
    """Answers written for this job: inline job['answers'] plus config/answers/<id>.json (label or intent -> text)."""
    out = dict(job.get("answers") or {})
    f = ANSWERS_DIR / f"{job.get('id', '')}.json"
    if f.exists():
        out.update(json.loads(f.read_text()))
    return out


def _authored_for(table: dict[str, str], label: str, intent: str) -> str | None:
    norm = lambda s: re.sub(r"\W+", " ", s).strip().lower()
    for k, v in table.items():
        if k == intent or norm(k) == norm(label):
            return v
    for k, v in table.items():   # legacy packs keyed by a label regex
        try:
            if re.search(k, label, re.I):
                return v
        except re.error:
            continue
    return None


# --------------------------------------------------------------------------- plan

def plan(fields: list[dict], job: dict, prof: Profile) -> list[Item]:
    fx = facts(prof)
    table = authored(job)
    jd = job.get("jd_text", "")
    company, role = job["company"], job["role"]
    items: list[Item] = []
    picked_other = False

    for f in fields:
        kind, label = f["kind"], f.get("label", "")
        intent = questions.classify(label, kind)
        it = Item(key=f["key"], label=label, kind=kind, intent=intent, required=bool(f.get("required")))
        opts = f.get("options") or []

        if kind == "orphan":
            # a question the reader found on the page but no control for; the visual filler answers it
            it.intent = questions.classify(label, "radio")
            val = COMBO_VALUES.get(it.intent) or fx.get(it.intent, "")
            if it.intent in questions.PERSONAL or it.intent in NEVER_GUESS or not val or len(val) > 40:
                it.status = "needs_khalid" if it.required else "skip"
                if it.required:
                    it.problems.append(f"question '{label}' has no readable control and no short answer")
            else:
                it.action, it.value, it.source = "sight", val, "default"
            items.append(it)
            continue
        if intent in questions.PERSONAL:
            ans = prof.defaults.get("legal_declarations", {}).get(label) if isinstance(prof.defaults.get("legal_declarations"), dict) else None
            if ans and kind in ("select", "radio"):
                it.action, it.value, it.source = "select", pick_option(intent, opts, ans) or "", "default"
            else:
                it.status = "needs_khalid"
                it.problems.append("legal declaration: only Khalid can answer")
        elif kind == "file":
            if intent == "resume_file" and job.get("resume_path"):
                it.action, it.value, it.source = "upload", job["resume_path"], "file"
            elif intent == "cover_letter_file" and job.get("cover_pdf"):
                it.action, it.value, it.source = "upload", job["cover_pdf"], "file"
            elif intent in ("resume_file", "cover_letter_file") and it.required:
                it.status = "needs_khalid"; it.problems.append(f"no file for {intent}")
            else:
                it.status = "skip"
        elif kind in ("select", "radio", "combobox") or (kind == "checkbox" and opts):
            if intent == "preferences" and kind == "combobox" and not opts:
                rank = len([i for i in items if i.intent == "preferences"])
                terms = PREFERENCE_TERMS.get(job.get("kind", "fullstack"), PREFERENCE_TERMS["fullstack"])
                it.action, it.value, it.source = "select", terms[min(rank, len(terms) - 1)], "choice"
            elif intent == "preferences":
                vals = pick_preferences(opts, job.get("kind", "fullstack"), 3 if kind == "checkbox" else 1)
                rank = len([i for i in items if i.intent == "preferences"])   # 2nd/3rd preference selects
                if kind != "checkbox":
                    ordered = pick_preferences(opts, job.get("kind", "fullstack"), 3)
                    vals = [ordered[min(rank, len(ordered) - 1)]] if ordered else []
                it.action, it.value, it.source = "select", vals if kind == "checkbox" else (vals[0] if vals else ""), "choice"
            else:
                want = fx.get(intent, "")
                if intent in NEVER_GUESS:
                    choice = None
                elif intent in ("grad_year", "high_school_year"):
                    choice = pick_option(intent, opts, want)
                elif kind == "combobox":
                    # typed, then matched against live suggestions; a dropdown takes a short option, never a sentence
                    choice = COMBO_VALUES.get(intent) or (want if want and len(want) <= 40 else None)
                elif intent == "school":
                    choice = next((o for o in opts if norm_opt(o) == norm_opt(want)), None) or pick_option(intent, opts)
                else:
                    choice = pick_option(intent, opts, want if intent not in ("rtw", "sponsorship", "office") else "")
                if choice:
                    it.action, it.value, it.source = "select", choice, "choice"
                    picked_other |= intent == "how_heard" and bool(re.match(r"other", choice, re.I))
                else:
                    it.intent = intent if intent != "unknown" else "unknown_choice"
                    it.status = "needs_khalid" if it.required else "skip"
                    if it.required:
                        it.problems.append(f"no truthful option for '{label}' among {opts[:8]}")
        elif kind == "checkbox":
            if intent == "privacy_consent":
                it.action, it.value, it.source = "check", True, "default"
            else:
                it.status = "skip"
        else:   # text / textarea
            if intent == "how_heard_other" and not picked_other:
                it.status = "skip"
            elif intent in questions.WRITEUPS:
                text = _authored_for(table, label, intent)
                src = "authored"
                if not text:
                    b = writer.brief(intent, label, prof, company, role, jd)
                    text = writer.llm_write(b) if job.get("use_llm", True) else None
                    src = "llm"
                if not text and intent in writer.COMPOSABLE:
                    text, src = writer.compose(intent, prof, company, role, jd), "composer"
                if text:
                    it.action, it.value, it.source = "type", text.strip(), src
                else:
                    it.status = "needs_writer"
                    it.problems.append("free-text question with no authored answer; write one with `apply.py brief`")
            else:
                text = _authored_for(table, label, intent) if intent not in IDENTITY else None
                val = text or fx.get(intent, "")
                if val:
                    it.action, it.value, it.source = "type", val, "authored" if text else "fact"
                elif intent in NEVER_GUESS and it.required:
                    it.status = "needs_khalid"
                    it.problems.append(f"no source for '{label}'; never guessed")
                elif intent in INTENTIONAL_SKIP or intent in NEVER_GUESS or intent == "unknown" and not it.required:
                    it.status = "skip"
                else:
                    it.status = "needs_khalid" if it.required else "skip"
                    if it.required:
                        it.problems.append(f"no fact for '{label}' ({intent})")
        if f.get("maxlength") and isinstance(it.value, str) and len(it.value) > int(f["maxlength"]):
            it.problems.append(f"answer is {len(it.value)} chars, field allows {f['maxlength']}")
        items.append(it)
    return items


# --------------------------------------------------------------------------- checks

def _evidence_text(prof: Profile) -> str:
    parts = [grounding.project_evidence(p) + " " + json.dumps(p.get("story", {})) for p in prof.projects]
    for e in prof.experience:
        parts += [e["title"], e["company"], " ".join(e.get("bullets", [])), " ".join(e.get("skills", []))]
    resume = ROOT / "config" / "base_resume.txt"
    if resume.exists():
        parts.append(resume.read_text())
    parts += [prof.degree, prof.school, str(prof.grad_year), prof.rtw_until, str(prof.grad_year - 3)]
    return " ".join(parts)


def mentioned_projects(text: str, prof: Profile) -> list[str]:
    """Project ids in the order the text first mentions them."""
    hits = []
    for p in prof.projects:
        pos = [m.start() for a in [p["name"], *p.get("aliases", [])] for m in re.finditer(re.escape(a), text, re.I)]
        if pos:
            hits.append((min(pos), p["id"]))
    return [pid for _, pid in sorted(hits)]


def check_writeup(text: str, intent: str, prof: Profile, company: str, jd_text: str = "") -> list[str]:
    probs = []
    words = len(text.split())
    lo, hi = writer.WORDS.get(intent, writer.WORDS["free_text"])
    if words < lo or words > hi:
        probs.append(f"{words} words, want {lo}-{hi}")
    ev = _evidence_text(prof)
    allowed_terms = set(keywords.extract(ev)) | set(keywords.extract(jd_text)) | set(keywords.extract(company))
    for t in keywords.extract(text):
        if t not in allowed_terms:
            probs.append(f"unbacked skill: {t}")
    ev_nums = {n.replace(",", "").rstrip(".") for n in grounding.NUM.findall(grounding.URL.sub(" ", ev + " " + jd_text))}
    for n in grounding.NUM.findall(grounding.URL.sub(" ", text)):
        if n.replace(",", "").rstrip(".") not in ev_nums:
            probs.append(f"unbacked number: {n}")
    if BANNED.search(text):
        probs.append(f"cliché: {BANNED.search(text).group(0)}")
    if FALSE_CLAIM.search(text):
        probs.append(f"claims experience he doesn't have: {FALSE_CLAIM.search(text).group(0)}")
    if intent in COMPANY_INTENTS and company.lower() not in text.lower():
        probs.append(f"doesn't mention {company}")
    if intent in evidence.IMPACT_INTENTS or intent in evidence.REQUIRES:
        named = mentioned_projects(text, prof)
        ok = evidence.allowed_leads(prof.projects, jd_text, intent)
        if intent not in ("additional_info", "alt_company") and named and named[0] not in ok:
            probs.append(f"leads with {named[0]}; should lead with one of {sorted(ok)}")
        if intent in ("impact_project", "hardest_challenge", "project_generic", "success_measure", "llm_production",
                      "pipeline_scale") and not named and intent != "success_measure":
            probs.append("names no project")
    if intent == "product_usage":
        used = any(company.lower() in s for p in prof.projects for s in p.get("skills", []))
        if not used and not NEGATION.search(text):
            probs.append(f"implies he has used {company}; the knowledge graph has no such project")
    return probs


def check(items: list[Item], prof: Profile, job: dict) -> list[str]:
    """Blocking problems for the whole form. Also writes each item's own problems onto it."""
    out = []
    for it in items:
        if it.action == "type" and it.intent in questions.WRITEUPS:
            it.problems += check_writeup(str(it.value), it.intent, prof, job["company"], job.get("jd_text", ""))
        if it.status in ("needs_writer", "needs_khalid"):
            out.append(f"{it.label or it.key}: {it.status}")
        elif it.status == "skip" and it.kind in ("text", "textarea") and it.intent not in INTENTIONAL_SKIP \
                and it.intent != "unknown":
            out.append(f"{it.label or it.key}: left blank")
        elif it.status == "skip" and it.required:
            out.append(f"{it.label or it.key}: required but skipped")
        out += [f"{it.label or it.key}: {p}" for p in it.problems if it.status == "ok"]
    return out


def to_json(items: list[Item]) -> list[dict]:
    return [asdict(i) for i in items]


def sheet(items: list[Item]) -> str:
    """Human/visual-filler view: label -> exact answer, in form order."""
    lines = []
    for i in items:
        if i.status == "skip":
            continue
        v = ", ".join(i.value) if isinstance(i.value, list) else ("[tick]" if i.value is True else i.value)
        how = "pick on screen by sight; no control was readable" if i.action == "sight" else i.kind
        lines.append(f"### {i.label or i.key}  ({how}, {i.intent}, {i.source or i.status})\n{v if i.status == 'ok' else i.status.upper()}\n")
    return "\n".join(lines)
