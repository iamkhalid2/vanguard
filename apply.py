"""Apply step: fills ATS application forms from the candidate profile, then hands over for a human Submit.

It submits only ids a human approved by name (--submit) and never touches a CAPTCHA. For each job it:
  1. refuses LinkedIn URLs and anything over the 2-per-company-per-30-days cap (checked against the log),
  2. opens the page headed in a dedicated persistent profile (~/.vanguard/profile; log into each ATS once there),
  3. clicks through to the form, fills labelled fields from data/knowledge_graph.json, config/base_resume.txt,
     config/apply_answers.json and the right resume PDF,
  4. pauses (and logs the question) when a required field has no truthful answer in the profile,
  5. pauses when a recaptcha/hcaptcha/turnstile frame is present, leaving the window open,
  6. screenshots the filled form and lists it in runs/ready-to-submit.json; nothing is submitted.
  7. `--submit id,id` re-fills exactly those approved ids, clicks Submit, screenshots the confirmation.
Every attempt is logged to runs/applications.jsonl.

Usage:
  python apply.py --batch config/apply_batch.json        # fill + screenshot, writes runs/ready-to-submit.json
  python apply.py --submit revolut-graduate-2027-android-engineer,...   # submit only the approved ids
  python apply.py URL --company Revolut --role "Graduate Android Engineer" --kind android
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent
RUNS = ROOT / "runs"
LOG = RUNS / "applications.jsonl"
PROFILE_DIR = Path.home() / ".vanguard" / "profile"
SEARCH_DIRS = [Path.home() / "Documents" / "Codes", Path.home() / "Downloads"]
RESUMES = {"android": "Mohammad-Khalid-Resume-Frontend-Kepler.pdf", "ai": "Mohammad-Khalid-Resume-FullStack.pdf",
           "fullstack": "Mohammad-Khalid-Resume-FullStack.pdf"}
CAP, WINDOW_DAYS = 2, 30
CAPTCHA_SELECTORS = ["iframe[src*='recaptcha']", "iframe[src*='hcaptcha']", "iframe[src*='challenges.cloudflare.com']",
                     ".g-recaptcha", ".h-captcha", ".cf-turnstile"]
APPLY_BUTTONS = [r"^apply( now| for this job)?$", r"^i'?m interested$", r"^apply on company site$"]
# right-to-work wording the profile can't answer with a plain yes/no (time-limited visa, sponsorship after June 2028)
AMBIGUOUS = r"without (any )?restrictions?|without requiring sponsorship|indefinite|permanent(ly)?|in the future|at any (point|time)"
# "will you now or in the future require sponsorship": truthful answer is yes (after 30 June 2028)
FUTURE_SPONSOR = r"(require|need)\w*\b.{0,40}sponsorship.{0,40}(future|ever|at any)|(now|currently),? or in the future.{0,40}sponsor"
FUTURE_SPONSOR_TEXT = "Yes, after 30 June 2028. I have the right to work in the UK until then and do not need sponsorship before that date."
COUNTED = {"submitted", "submit_unconfirmed"}


# ---------------------------------------------------------------- profile

def load_profile() -> dict:
    kg = json.loads((ROOT / "data" / "knowledge_graph.json").read_text())
    c = kg["candidate"]
    resume_txt = (ROOT / "config" / "base_resume.txt").read_text()
    phone = re.search(r"\+44[\d ]{9,14}\d", resume_txt)
    answers_path = ROOT / "config" / "apply_answers.json"
    answers = json.loads(answers_path.read_text()) if answers_path.exists() else {}
    private_path = Path.home() / ".vanguard" / "private.json"   # address etc.; never committed
    private = json.loads(private_path.read_text()) if private_path.exists() else {}
    first, *rest = c["name"].split()
    return {"first": first, "last": " ".join(rest), "full": c["name"], "email": c["email"],
            "phone": phone.group(0) if phone else "", "location": c.get("location", "London, UK"),
            "city": "London", "country": "United Kingdom", "linkedin": c.get("linkedin", ""),
            "github": f"https://github.com/{c['github']}", "website": c.get("website", ""),
            "rtw": "Right to work in the UK until 30 June 2028; will need sponsorship after that.",
            "swe_years": str(c.get("commercial_swe_years", 0)),
            "degree": c["education"][0]["degree"], "school": c["education"][0]["school"],
            "grad_year": c["education"][0]["years"].split("-")[-1],
            "employer": c["experience"][0]["company"], "title": c["experience"][0]["title"],
            "address": private.get("address", ""), "postcode": private.get("postcode", ""),
            "rtw_unrestricted": private.get("rtw_without_restrictions", ""),
            "answers": answers}


PROFILE: dict = {}


def rules(p: dict, kind: str) -> list[tuple[str, str]]:
    """(label regex, answer). First match wins; order matters. Only facts present in the profile."""
    pack = p["answers"].get(kind, {})
    r = [
        (r"first name|given name|forename", p["first"]),
        (r"last name|surname|family name", p["last"]),
        (r"^(full )?name\b|your name", p["full"]),
        (r"e-?mail", p["email"]),
        (r"phone|mobile|telephone", p["phone"]),
        (r"linkedin", p["linkedin"]),
        (r"github", p["github"]),
        (r"website|portfolio|personal site|other url", p["website"]),
        (r"\bcity\b|town", p["city"]),
        (r"country of residence|\bcountry\b", p["country"]),
        (r"post ?code|zip", p["postcode"]),
        (r"^\W*(home |current |street )?address|address line 1", p["address"]),
        (r"location|where are you (based|located)|country you.re (currently )?residing", p["location"]),
        (r"right to work|eligib\w* to work|work authori[sz]ation|legally (allowed|entitled)", p["rtw"]),
        (r"require (visa )?sponsorship|need sponsorship|visa sponsorship", "Not currently. " + p["rtw"]),
        (r"in(to)? production|production system|scale|users|revenue|team size|managed", ""),   # never invent
        (r"years of (commercial|professional|software|engineering)|how many years.*experience", p["swe_years"]),
        (r"current (company|employer)", p["employer"]),
        (r"current (job )?title|current role", p["title"]),
        (r"degree|qualification", p["degree"]),
        (r"university|school|institution", p["school"]),
        (r"graduat\w* (year|date)|year of graduation", p["grad_year"]),
        (r"security clearance|\bsc\b|\bdv\b clear", "No"),
    ]
    guard = r"in(to)? production|production system|at scale|\bscale\b"
    for pat, ans in pack.items():   # project-specific paste-in answers from the apply pack
        r.insert(0, (rf"^(?!.*({guard})).*({pat})", ans))
    return r


def choice_rules(p: dict) -> list[tuple[str, str]]:
    """For selects/radios: label regex -> option-text regex."""
    return [
        (r"right to work|eligib\w* to work|legally", r"^yes\b"),
        # plain yes/no sponsorship questions have no single truthful answer (not now, yes after June 2028): pause
        (r"require (visa )?sponsorship|need sponsorship", r"not (now|currently)|until 2028"),
        (r"country", r"united kingdom|^uk$"),
        (r"security clearance", r"^no\b"),
        (r"how did you hear", r"careers? (page|site)|company website|job board"),
        # voluntary equal-opportunity questions: always the most private option
        (r"gender|sex\b|race|ethnic|veteran|disabilit|sexual orientation|pronoun|transgender|hispanic",
         r"decline|prefer not|don.t wish|do not wish|not to (say|disclose|answer)|rather not"),
        (r"years of (commercial|professional)|how many years", r"^0\b|less than 1|none|no experience"),
    ]


def resume_for(kind: str) -> Path | None:
    name = RESUMES[kind]
    for d in SEARCH_DIRS:
        hit = next(iter(sorted(d.rglob(name))), None) if d.exists() else None
        if hit:
            return hit
    return None


# ---------------------------------------------------------------- log + cap

def log(entry: dict) -> None:
    RUNS.mkdir(exist_ok=True)
    entry["ts"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with LOG.open("a") as f:
        f.write(json.dumps(entry) + "\n")


def company_count(company: str) -> int:
    if not LOG.exists():
        return 0
    cutoff = datetime.now(timezone.utc) - timedelta(days=WINDOW_DAYS)
    seen = {}
    for line in LOG.read_text().splitlines():
        e = json.loads(line)
        if e["company"].lower() == company.lower() and datetime.fromisoformat(e["ts"]) >= cutoff:
            seen[e["url"]] = e["status"]   # last status per URL
    return sum(1 for s in seen.values() if s in COUNTED)


# ---------------------------------------------------------------- browser helpers

def has_captcha(page) -> bool:
    """True only for a challenge a human must solve. Invisible reCAPTCHA/hCaptcha (Greenhouse, Lever, Ashby)
    sit hidden or as a 256x60 badge and need nothing unless they escalate to a visible challenge."""
    for s in CAPTCHA_SELECTORS:
        for el in page.locator(s).all():
            try:
                if not el.is_visible():
                    continue
                box = el.bounding_box() or {}
                src = el.get_attribute("src") or ""
                if "recaptcha" in src and "anchor" in src and round(box.get("width", 0)) <= 260:
                    continue   # invisible-reCAPTCHA badge, not a checkbox
                if box.get("width", 0) < 40 or box.get("height", 0) < 40:
                    continue
                return True
            except Exception:
                continue
    # Turnstile renders in a closed shadow root, so its iframe is invisible to selectors; look for its text instead
    if any("challenges.cloudflare.com" in f.url for f in page.frames):
        return True
    return page.get_by_text(re.compile(r"verify you are human", re.I)).count() > 0


def dismiss_cookies(page) -> None:
    """Picks the most privacy-preserving option on consent banners."""
    for pat in (r"^(decline|reject) all$", r"^(decline|reject)( optional| non-essential)?( cookies)?$",
                r"^(only )?(strictly )?necessary( cookies)?( only)?$"):
        btn = page.get_by_role("button", name=re.compile(pat, re.I))
        if btn.count() and btn.first.is_visible():
            btn.first.click(); page.wait_for_timeout(800); return


def click_apply(page) -> None:
    dismiss_cookies(page)
    for pat in APPLY_BUTTONS:
        for role in ("button", "link"):
            loc = page.get_by_role(role, name=re.compile(pat, re.I))
            if loc.count() and loc.first.is_visible():
                loc.first.click()
                page.wait_for_load_state("domcontentloaded")
                page.wait_for_timeout(1500)
                return


QUESTION_JS = """e => { let n = e;
            for (let i = 0; i < 7 && n; i++) { n = n.parentElement; if (!n) break;
                const l = [...n.querySelectorAll('label, legend, [class*=question-title i], [class*=label i]')]
                    .find(x => !x.contains(e) && !x.querySelector('input') && x.innerText.trim().length > 1);
                if (l) return l.innerText.replace(/\\s+/g, ' ').trim(); }
            return ''; }"""


def question_of(el) -> str:
    """Text of the nearest label/legend above a control (for widgets whose label isn't wired to the input)."""
    try:
        return el.evaluate(QUESTION_JS, timeout=5000)
    except Exception:
        return ""


def label_of(page, el) -> str:
    try:
        t = _label_of(el)
    except Exception:   # detached or slow element: treat as unlabelled
        t = ""
    if not t or t == (el.get_attribute("placeholder") or "").strip():
        t = question_of(el) or t
    return t


def _label_of(el) -> str:
    return el.evaluate("""e => {
        const id = e.id; let t = '';
        if (id) { const l = document.querySelector(`label[for="${CSS.escape(id)}"]`); if (l) t = l.innerText; }
        if (!t) { const l = e.closest('label'); if (l) t = l.innerText; }
        if (!t) t = e.getAttribute('aria-label') || '';
        if (!t) { const lb = e.getAttribute('aria-labelledby');
                  if (lb) t = lb.split(' ').map(i => document.getElementById(i)?.innerText || '').join(' '); }
        if (!t) { const g = e.closest('fieldset, [role=group], .field, .application-question, [data-ui=field]');
                  if (g) { const h = g.querySelector('legend, label, .label, [class*=label]'); if (h) t = h.innerText; } }
        if (!t) t = e.getAttribute('placeholder') || e.name || '';
        return t.replace(/\\s+/g, ' ').trim();
    }""", timeout=5000)


def is_required(el, label: str) -> bool:
    return bool(el.get_attribute("required") is not None or el.get_attribute("aria-required") == "true"
                or label.endswith("*") or "required" in label.lower())


def unfilled_required(page, scope) -> list[str]:
    """Re-reads the form after filling: any required field still empty is reported, whatever we think we filled."""
    return scope.evaluate("""root => {
        const out = [];
        const qtext = e => { let n = e; for (let i = 0; i < 5 && n; i++) { n = n.parentElement;
            if (n && n.innerText && n.innerText.trim().length > 2) break; } return n ? n.innerText.split('\\n')[0].trim() : (e.name || ''); };
        const lbl = e => { let n = e; for (let i = 0; i < 6 && n; i++) { n = n.parentElement; const l = n && n.querySelector('label, legend');
            if (l) return l; } return null; };
        const req = e => e.required || e.getAttribute('aria-required') === 'true' || /[*✱]/.test(qtext(e).slice(-3)) ||
            (lbl(e) && (/required/i.test(lbl(e).className) || /[*✱]\\s*$/.test(lbl(e).innerText)));
        for (const e of root.querySelectorAll('input:not([type=hidden]):not([type=radio]):not([type=checkbox]):not([type=file]):not([type=submit]), textarea, select')) {
            if (e.offsetParent === null) continue;
            if (req(e) && !String(e.value || '').trim()) out.push(qtext(e));
        }
        const names = new Set([...root.querySelectorAll('input[type=radio]')].map(e => e.name).filter(Boolean));
        for (const n of names) {
            const rs = [...root.querySelectorAll(`input[type=radio][name="${CSS.escape(n)}"]`)];
            let c = rs[0].parentElement; while (c && !rs.every(r => c.contains(r))) c = c.parentElement;
            const q = c ? (c.parentElement || c).innerText.split('\\n')[0].trim() : n;
            const ql = c && c.parentElement && c.parentElement.querySelector('label, legend');
            const rq = rs.some(r => r.required) || /[*✱]/.test(q.slice(-3)) || (ql && /required/i.test(ql.className));
            if (rq && !rs.some(r => r.checked)) out.push(q);
        }
        return out;
    }""")


def pick_radio(r) -> None:
    """Workable-style radios are visually hidden; click the visible wrapper when check() can't."""
    try:
        r.check(timeout=3000)
    except Exception:
        r.locator("xpath=..").first.click()
    if not r.is_checked():
        r.evaluate("e => { e.click(); }")


def answer(label: str, table: list[tuple[str, str]]) -> str | None:
    if re.search(FUTURE_SPONSOR, label, re.I):
        return FUTURE_SPONSOR_TEXT
    if re.search(r"indefinite|permanent", label, re.I):
        return None
    if re.search(AMBIGUOUS, label, re.I):
        return PROFILE.get("rtw_unrestricted") or None
    for pat, ans in table:
        if re.search(pat, label, re.I):
            return ans or None   # empty answer = deliberately unanswerable
    return None


def fill_form(page, p: dict, kind: str, resume: Path, job: dict | None = None) -> tuple[list[str], list[str]]:
    """Returns (filled labels, missing required labels). job["answers"]/job["choices"] are per-job overrides."""
    job = job or {}
    extra = dict(job.get("answers", {}))
    if job.get("cover_text"):
        extra.setdefault(r"^\W*cover letter|why (do you want|are you interested)|additional information",
                         (ROOT / job["cover_text"]).read_text().strip())
    text_rules = list(extra.items()) + rules(p, kind)
    opt_rules = list(job.get("choices", {}).items()) + choice_rules(p)
    filled, missing = [], []
    scope = page.frame_locator("iframe#grnhse_iframe").locator("body") if page.locator("iframe#grnhse_iframe").count() else page.locator("body")

    for f in scope.locator("input[type=file]").all():
        lab = label_of(page, f).lower()
        if re.search(r"photo|avatar|image|picture|autofill|upload file$", lab) or \
                f.evaluate("e => !!e.closest('[class*=autofill i]')"):
            continue   # Ashby's "autofill from resume" re-parses and clears fields we filled
        if "cover" in lab:
            if job.get("cover_pdf"):
                try:
                    f.set_input_files(str(ROOT / job["cover_pdf"])); filled.append(f"file:{lab}")
                    page.wait_for_timeout(1500)
                except Exception:
                    pass
            continue
        try:
            f.set_input_files(str(resume)); filled.append(f"file:{lab or 'resume'}")
            page.wait_for_timeout(4000)   # let upload-triggered parsing finish before typing
        except Exception:
            pass

    for el in scope.locator("input:not([type=hidden]):not([type=file]):not([type=checkbox]):not([type=radio]):not([type=submit]):not([role=combobox]), textarea").all():
        if not el.is_visible():
            continue
        lab = label_of(page, el)
        ans = answer(lab, text_rules)
        if ans:
            try:
                el.fill(ans, timeout=5000); filled.append(lab)
            except Exception:   # read-only / custom widget: count as unanswered if required
                if is_required(el, lab):
                    missing.append(f"{lab} (could not type into field)")
        elif is_required(el, lab):
            if el.input_value():   # stale draft restored by the ATS: never keep an answer we can't stand behind
                el.fill("")
            missing.append(lab)

    for el in scope.locator("select").all():
        if not el.is_visible():
            continue
        lab = label_of(page, el)
        opts = el.locator("option").all_inner_texts()
        target = next((rx for pat, rx in opt_rules if re.search(pat, lab, re.I)), None)
        pick = next((o for o in opts if target and re.search(target, o.strip(), re.I)), None)
        if pick:
            el.select_option(label=pick); filled.append(lab)
        elif is_required(el, lab) and el.input_value() in ("", None):
            missing.append(lab)

    for cb in scope.locator("input[type=checkbox]").all():
        lab = label_of(page, cb)
        if re.search(r"privacy (notice|policy)", lab, re.I) and re.search(r"this application|processing of my data|recruit", lab, re.I) \
                and not re.search(r"marketing|newsletter|future (roles|opportunities)|talent (pool|community)", lab, re.I):
            if not cb.is_checked():
                pick_radio(cb)
            filled.append(lab)

    # radio groups outside fieldsets (Ashby, Lever): group by name, find the question text above the options
    for name in set(scope.locator("input[type=radio]").evaluate_all("els => els.map(e => e.name).filter(Boolean)")):
        radios = scope.locator(f"input[type=radio][name='{name}']")
        if radios.first.evaluate("e => !!e.closest('fieldset, [role=radiogroup]')"):
            continue   # handled below
        q = question_of(radios.first) or radios.first.evaluate("""e => { const all = [...document.querySelectorAll(`input[type=radio][name="${CSS.escape(e.name)}"]`)];
            let n = e.parentElement; while (n && !all.every(r => n.contains(r))) n = n.parentElement;
            while (n && n.parentElement && n.innerText.trim() === [...n.querySelectorAll('label')].map(l => l.innerText).join('\\n').trim()) n = n.parentElement;
            return (n ? n.innerText : '').split('\\n')[0].trim(); }""")
        target = next((rx for pat, rx in opt_rules if re.search(pat, q, re.I)), None)
        hit = next((r for r in radios.all() if target and re.search(target, label_of(page, r), re.I)), None)
        if hit:
            pick_radio(hit); filled.append(q)
        elif not radios.evaluate_all("els => els.some(e => e.checked)") and ("*" in q or "✱" in q):
            missing.append(q)

    # type-ahead comboboxes (location pickers): type the answer, take the first suggestion
    for cb in scope.locator("input[role=combobox], input[placeholder*='Start typing' i]").all():
        if not cb.is_visible():
            continue
        lab = label_of(page, cb)
        q = question_of(cb)
        ans = answer(lab or q, text_rules) or answer(q, text_rules)
        if not ans:
            if "*" in (q + lab):
                missing.append(q or lab)
            continue
        try:
            cb.click(); cb.fill("")
            tries = [p["country"], p["city"]] if re.search(r"country|residing|location|city", q + lab, re.I) else [ans]
            opt = None
            for want in tries:   # some pickers take countries ("United Kingdom"), others cities ("London")
                cb.fill(""); cb.press_sequentially(want, delay=80)
                page.wait_for_timeout(3500)
                opts = page.locator("[role=option], [role=listbox] li").filter(has_text=re.compile(re.escape(want), re.I))
                if opts.count():
                    opt = opts.first; break
            if opt is None:
                missing.append(q or lab); continue
            if opt.is_visible():
                opt.click(); filled.append(q or lab)
            elif "*" in (q + lab):
                missing.append(q or lab)
        except Exception:
            missing.append(f"{q or lab} (could not pick an option)")

    for grp in scope.locator("fieldset, [role=radiogroup]").all():
        radios = grp.locator("input[type=radio]")
        if not radios.count():
            continue
        lab = label_of(page, grp)
        target = next((rx for pat, rx in job.get("choices", {}).items() if re.search(pat, lab, re.I)), None) or \
            (r"^yes\b" if re.search(FUTURE_SPONSOR, lab, re.I) else
             None if re.search(r"indefinite|permanent", lab, re.I) else
             (r"^yes\b" if p["rtw_unrestricted"] else None) if re.search(AMBIGUOUS, lab, re.I) else next((rx for pat, rx in opt_rules if re.search(pat, lab, re.I)), None))
        done = False
        for r in radios.all():
            if target and re.search(target, label_of(page, r), re.I):
                pick_radio(r); filled.append(lab); done = True; break
        if not done and (is_required(radios.first, lab) or "*" in lab):
            missing.append(lab)
    page.wait_for_timeout(1500)
    for q in unfilled_required(page, scope):
        if q not in missing:
            missing.append(q)
    return filled, missing


# ---------------------------------------------------------------- run

READY = RUNS / "ready-to-submit.json"
SUBMIT_BUTTON = r"^(submit( application)?|send application|apply)$"
CONFIRM_TEXT = r"thank you|application (has been )?(received|submitted)|we('ve| have) received"
CAPTCHA_WAIT_S = 15 * 60
PARK_CAPTCHA = True   # owner's call: park CAPTCHA forms for later instead of waiting (--wait-captcha to wait)


def job_id(job: dict) -> str:
    return re.sub(r"\W+", "-", f"{job['company']}-{job['role']}").strip("-").lower()


def wait_out_captcha(page, label: str) -> bool:
    """Leaves the window open for a human to solve it. Returns True once the challenge is gone."""
    print(f"[CAPTCHA] {label}: waiting up to {CAPTCHA_WAIT_S // 60} min for you to solve it in the open window.",
          flush=True)
    log({"company": label, "role": "", "url": page.url, "status": "captcha_wait", "missing_fields": [], "screenshots": []})
    for _ in range(CAPTCHA_WAIT_S // 5):
        page.wait_for_timeout(5000)
        if not has_captcha(page):
            return True
    return False


def open_and_fill(ctx, job: dict, p: dict) -> tuple[object, dict]:
    url, company, role, kind = job["url"], job["company"], job["role"], job["kind"]
    base = {"id": job_id(job), "company": company, "role": role, "url": url, "missing_fields": [], "screenshots": []}
    if "linkedin.com" in urlparse(url).netloc:
        return None, {**base, "status": "skipped", "reason": "LinkedIn is not automated"}
    if job.get("skip"):
        return None, {**base, "status": "skipped", "reason": job["skip"]}
    if job.get("hold"):
        return None, {**base, "status": "paused", "reason": job["hold"]}
    resume = (ROOT / job["resume_path"]) if job.get("resume_path") else resume_for(kind)
    if not resume or not resume.exists():
        return None, {**base, "status": "paused", "reason": f"resume not found: {RESUMES[kind]}"}

    shots = RUNS / "screenshots"; shots.mkdir(parents=True, exist_ok=True)
    page = ctx.new_page()
    page.goto(url, wait_until="domcontentloaded")
    page.evaluate("() => { try { localStorage.clear(); sessionStorage.clear(); } catch (e) {} }")   # drop stale ATS drafts
    page.reload(wait_until="domcontentloaded"); page.wait_for_timeout(2500)
    if "linkedin.com" in urlparse(page.url).netloc:
        return page, {**base, "status": "skipped", "reason": "redirected to LinkedIn"}
    n = len(ctx.pages)
    click_apply(page)
    if len(ctx.pages) > n:   # apply opened a new tab
        page = ctx.pages[-1]; page.wait_for_load_state("domcontentloaded"); page.wait_for_timeout(2000)
    dismiss_cookies(page)
    base["final_url"] = page.url
    if has_captcha(page):
        if PARK_CAPTCHA:
            return page, {**base, "status": "parked-captcha", "reason": "CAPTCHA on the form; left for later"}
        if not wait_out_captcha(page, f"{company} - {role}"):
            return page, {**base, "status": "paused", "reason": "captcha not solved; window left open"}

    filled, missing = fill_form(page, p, kind, resume, job)
    base["filled"], base["resume"] = filled, resume.name
    pre = shots / f"{base['id']}-filled.png"; page.screenshot(path=str(pre), full_page=True)
    base["screenshots"].append(str(pre.relative_to(ROOT)))
    if not any(not f.startswith("file:") for f in filled):
        return page, {**base, "status": "failed", "reason": f"no application form found at {page.url} (login wall or unsupported page)"}
    if missing:
        return page, {**base, "status": "paused", "reason": "required questions with no truthful profile answer",
                      "missing_fields": missing}
    return page, {**base, "status": "filled"}


def confirmed(page) -> bool:
    return bool(re.search(CONFIRM_TEXT, page.inner_text("body"), re.I))


def submit(page, rec: dict) -> dict:
    """Clicks Submit, then waits for a real confirmation page. A CAPTCHA keeps it waiting for the human
    (up to CAPTCHA_WAIT_S); a widget that flickers while reloading no longer counts as solved."""
    btn = page.get_by_role("button", name=re.compile(SUBMIT_BUTTON, re.I))
    if not btn.count():
        return {**rec, "status": "failed", "reason": "submit button not found"}
    btn.last.click()
    label, announced, saw_captcha, reclicked = f"{rec['company']} - {rec['role']}", False, False, False
    for tick in range(CAPTCHA_WAIT_S // 5):
        page.wait_for_timeout(5000)
        if confirmed(page):
            break
        if has_captcha(page):
            saw_captcha = True
            if PARK_CAPTCHA:
                break
            if not announced:
                print(f"[CAPTCHA] {label}: waiting up to {CAPTCHA_WAIT_S // 60} min for you to solve it in the open window.",
                      flush=True)
                log({**rec, "status": "captcha_wait"}); announced = True
            continue
        btn = page.get_by_role("button", name=re.compile(SUBMIT_BUTTON, re.I))
        if saw_captcha and not reclicked and btn.count() and btn.last.is_enabled():
            btn.last.click(); reclicked = True   # some ATSs need Submit again once the challenge clears
        if not saw_captcha and tick >= 6:
            break   # no challenge and nothing happening after 30 s
    post = RUNS / "screenshots" / f"{rec['id']}-confirmation.png"; page.screenshot(path=str(post), full_page=True)
    rec["screenshots"].append(str(post.relative_to(ROOT)))
    if confirmed(page):
        return {**rec, "status": "submitted", "reason": ""}
    if saw_captcha:
        return {**rec, "status": "parked-captcha", "reason": "CAPTCHA behind Submit; not submitted, left for later"}
    return {**rec, "status": "submit_unconfirmed", "reason": "clicked Submit but saw no confirmation; check the screenshot"}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("url", nargs="?")
    ap.add_argument("--company"); ap.add_argument("--role"); ap.add_argument("--kind", choices=RESUMES, default="fullstack")
    ap.add_argument("--batch", type=Path)
    ap.add_argument("--wait-captcha", action="store_true", help="wait for a human to solve CAPTCHAs instead of parking")
    ap.add_argument("--submit", help="comma-separated ids from runs/ready-to-submit.json that the human approved")
    a = ap.parse_args()
    global PARK_CAPTCHA
    PARK_CAPTCHA = not a.wait_captcha
    if a.submit:
        ready = {r["id"]: r for r in json.loads(READY.read_text())}
        ids = [i.strip() for i in a.submit.split(",") if i.strip()]
        unknown = [i for i in ids if i not in ready]
        if unknown:
            sys.exit(f"not in ready-to-submit.json: {unknown}")
        jobs = [ready[i]["job"] for i in ids]
    else:
        jobs = json.loads(a.batch.read_text()) if a.batch else [{"url": a.url, "company": a.company, "role": a.role, "kind": a.kind}]
    if not all(j.get("url") and j.get("company") for j in jobs):
        sys.exit("each job needs url and company")

    from playwright.sync_api import sync_playwright
    p = load_profile()
    PROFILE.update(p)
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    results, ready_out, keep_open = [], [], False
    with sync_playwright() as pw:
        try:
            ctx = pw.chromium.launch_persistent_context(str(PROFILE_DIR), channel="msedge", headless=False)
        except Exception:
            ctx = pw.chromium.launch_persistent_context(str(PROFILE_DIR), headless=False)
        for job in jobs:
            if company_count(job["company"]) >= CAP:
                r = {"id": job_id(job), "company": job["company"], "role": job["role"], "url": job["url"],
                     "status": "skipped", "reason": f"cap: {CAP} applications to {job['company']} in {WINDOW_DAYS} days",
                     "missing_fields": [], "screenshots": []}
            else:
                try:
                    page, r = open_and_fill(ctx, job, p)
                    if r["status"] == "filled":
                        if a.submit:
                            r = submit(page, r)
                        else:
                            ready_out.append({"id": r["id"], "company": r["company"], "role": r["role"], "url": r["url"],
                                              "screenshot": r["screenshots"][0], "job": job})
                    keep_open |= r["status"] == "paused" and "captcha" in r.get("reason", "") and not PARK_CAPTCHA
                except Exception as e:
                    r = {"id": job_id(job), "company": job["company"], "role": job["role"], "url": job["url"],
                         "status": "failed", "reason": f"{type(e).__name__}: {e}"[:300], "missing_fields": [], "screenshots": []}
            log(r); results.append(r)
            print(f"{r['status']:>18}  {r['id']}  {r.get('reason', '')}  {r.get('missing_fields') or ''}", flush=True)
        if not a.submit:
            READY.write_text(json.dumps(ready_out, indent=2))
        if keep_open:
            print("A CAPTCHA window is still open; close the browser when you are done.", flush=True)
            ctx.wait_for_event("close", timeout=0)
        ctx.close()
    print(json.dumps({s: sum(r["status"] == s for r in results) for s in {r["status"] for r in results}}))


if __name__ == "__main__":
    main()
