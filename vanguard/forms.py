"""Module 6: form reader, filler and verifier (Playwright).

read(scope)          -> every visible field on the form, each tagged in the DOM with data-vg="<key>"
fill(scope, plan)    -> types/picks/uploads exactly the planned value into exactly the tagged element
verify(scope, plan)  -> reads every field back and compares it with the plan

Why this replaces the old label matcher: the old code searched the page for the first label that
matched a regex and typed into whatever input sat near it, which is how Khalid's name ended up in a
Location box. Here a label belongs to one control or to none:
  1. <label for=id>, aria-labelledby, a <label> wrapping only this control, aria-label;
  2. otherwise the nearest text block *above* the control, searched only inside containers that
     hold no other field, so a label can never be borrowed from a neighbouring field;
  3. otherwise the field is unlabelled and nothing is typed into it.
After filling, verify() must see each planned value in its own field and no change in any field
the plan left alone. Any mismatch stops the job before Submit.
"""
from __future__ import annotations

import re
from pathlib import Path

READ_JS = r"""
(root) => {
  const doc = root.ownerDocument || document;
  const CONTROL = 'input:not([type=hidden]):not([type=submit]):not([type=button]):not([type=image]):not([type=reset]), textarea, select';
  const GENERIC = /^(type here|select|choose|start typing|search|enter)\b|\.\.\.$/i;
  const clean = s => (s || '').replace(/\s+/g, ' ').trim();
  const visible = e => {
    if (!e) return false;
    const r = e.getBoundingClientRect(); const st = getComputedStyle(e);
    return r.width > 0 && r.height > 0 && st.visibility !== 'hidden' && st.display !== 'none';
  };
  const shown = e => visible(e) || (['radio', 'checkbox', 'file'].includes(e.type) &&
      (visible(e.closest('label')) || visible(e.parentElement) || (e.id && visible(doc.querySelector(`label[for="${CSS.escape(e.id)}"]`)))));
  const ownText = el => { // element text without the text of any controls or option labels inside it
    const c = el.cloneNode(true);
    c.querySelectorAll('input, textarea, select, option, button, [role=listbox], [role=option], script, style').forEach(x => x.remove());
    return clean(c.innerText || c.textContent);
  };
  const controlsIn = el => [...el.querySelectorAll(CONTROL)].filter(shown);
  // text immediately above `node`, looking only in containers that contain nothing but `members`
  const above = (node, members) => {
    let n = node;
    for (let depth = 0; depth < 6 && n && n !== root; depth++) {
      for (let s = n.previousElementSibling; s; s = s.previousElementSibling) {
        if (controlsIn(s).length) return '';            // the previous thing is another field: no label here
        if (s.matches('button, [role=button], script, style')) continue;
        const t = ownText(s);
        if (t && t.length > 1) return t;
      }
      const parent = n.parentElement;
      if (!parent || controlsIn(parent).some(c => !members.includes(c))) return '';
      n = parent;
    }
    return '';
  };
  const wired = e => {
    if (e.id) { const l = doc.querySelector(`label[for="${CSS.escape(e.id)}"]`); if (l && ownText(l)) return ownText(l); }
    const lb = e.getAttribute('aria-labelledby');
    if (lb) { const t = clean(lb.split(/\s+/).map(i => doc.getElementById(i)?.innerText || '').join(' ')); if (t) return t; }
    const wrap = e.closest('label');
    if (wrap && controlsIn(wrap).length === 1 && ownText(wrap)) return ownText(wrap);
    return clean(e.getAttribute('aria-label'));
  };
  const optionLabel = e => {
    if (e.id) { const l = doc.querySelector(`label[for="${CSS.escape(e.id)}"]`); if (l) return clean(l.innerText); }
    const wrap = e.closest('label'); if (wrap) return ownText(wrap) || clean(wrap.innerText);
    const sib = e.nextElementSibling; if (sib && !sib.matches(CONTROL)) return clean(sib.innerText);
    return clean(e.getAttribute('aria-label') || e.value);
  };
  const requiredMark = t => /[*✱]\s*$/.test(t) || /\(required\)/i.test(t);
  const fields = []; const seen = new Set(); let n = 0;
  // a text control the user can't see or reach: react-select's required-validation input (opacity 0,
  // tabindex -1) and anything inside aria-hidden. Stripe's form has one beside every dropdown.
  const ghost = e => e.closest('[aria-hidden="true"]') ||
      (getComputedStyle(e).opacity === '0' && (e.tabIndex < 0 || e.readOnly));
  // radios/checkboxes without a name: the group is the nearest ancestor holding more than one of them
  const unnamedGroup = (e, type) => {
    for (let box = e.parentElement; box && box !== root.parentElement; box = box.parentElement) {
      const g = [...box.querySelectorAll(`input[type=${type}]`)].filter(x => !x.name && shown(x));
      if (g.length > 1) return g;   // other controls (an "if other" box) may share the container
    }
    return [e];
  };
  for (const e of root.querySelectorAll(CONTROL)) {
    if (seen.has(e) || !shown(e)) continue;
    const type = (e.getAttribute('type') || e.tagName).toLowerCase();
    if (!['radio', 'checkbox', 'file'].includes(type) && e.tagName !== 'SELECT' && ghost(e)) { seen.add(e); continue; }
    if (type === 'radio' || type === 'checkbox') {
      const group = e.name ? [...root.querySelectorAll(`input[name="${CSS.escape(e.name)}"]`)].filter(shown) : unnamedGroup(e, type);
      group.forEach(g => seen.add(g));
      if (type === 'radio' || group.length > 1) {
        const key = `vg${n++}`;
        let box = e.parentElement; while (box && box !== root && !group.every(g => box.contains(g))) box = box.parentElement;
        const fs = e.closest('fieldset'); const legend = fs && fs.contains(group[group.length - 1]) && fs.querySelector('legend');
        const rg = e.closest('[role=radiogroup], [role=group]');
        let label = legend ? clean(legend.innerText) :
            (rg && rg.getAttribute('aria-labelledby')) ? wired(rg) : clean(rg && rg.getAttribute('aria-label'));
        if (!label && box) label = above(box, group);
        if (!label && box) { const c = box.cloneNode(true); c.querySelectorAll('label, input').forEach(x => x.remove()); label = clean(c.innerText || c.textContent); }
        group.forEach((g, i) => g.setAttribute('data-vg', `${key}.${i}`));
        fields.push({key, kind: type, label, options: group.map(optionLabel), name: e.name,
                     required: group.some(g => g.required || g.getAttribute('aria-required') === 'true') || requiredMark(label)});
        continue;
      }
    }
    seen.add(e);
    const key = `vg${n++}`; e.setAttribute('data-vg', key);
    const combo = e.getAttribute('role') === 'combobox' || e.getAttribute('aria-autocomplete') === 'list';
    const kind = e.tagName === 'TEXTAREA' ? 'textarea' : e.tagName === 'SELECT' ? 'select' :
                 type === 'file' ? 'file' : type === 'checkbox' ? 'checkbox' : type === 'radio' ? 'radio' :
                 combo ? 'combobox' : 'text';
    let label = wired(e) || above(e, [e]);
    const ph = clean(e.getAttribute('placeholder'));
    if (!label && ph && !GENERIC.test(ph)) label = ph;
    fields.push({key, kind, label, name: e.name || '', id: e.id || '', placeholder: ph,
                 options: e.tagName === 'SELECT' ? [...e.options].map(o => clean(o.text)) : [],
                 maxlength: e.maxLength > 0 ? e.maxLength : null,
                 required: e.required || e.getAttribute('aria-required') === 'true' || requiredMark(label)});
  }
  // Questions printed on the page that no field above claimed (custom widgets the reader can't see
  // as controls, e.g. Ashby's "How did you hear" buttons). The plan lists them for the visual filler.
  const strip = t => clean(t).replace(/[*✱]\s*$/, '').trim().toLowerCase();
  const claimed = new Set(fields.map(f => strip(f.label)));
  const orphans = new Set();
  for (const el of root.querySelectorAll('label, legend, p, span, div, h3, h4, h5, h6')) {
    if (!visible(el) || controlsIn(el).length) continue;
    const t = ownText(el);
    if (!t || t.length > 200 || !/(\?|[*✱])\s*$/.test(t)) continue;
    if ([...el.children].some(c => ownText(c) === t)) continue;      // the innermost element carries it
    const k = strip(t);
    if (claimed.has(k) || orphans.has(k) || [...claimed].some(c => c && (c.includes(k) || k.includes(c)))) continue;
    orphans.add(k);
    fields.push({key: `vg${n++}`, kind: 'orphan', label: t, options: [], required: requiredMark(t)});
  }
  return fields;
}
"""

READBACK_JS = r"""
(root) => {
  const out = {}; const clean = s => (s || '').replace(/\s+/g, ' ').trim();
  for (const e of root.querySelectorAll('[data-vg]')) {
    const k = e.getAttribute('data-vg');
    if (k.includes('.')) {                       // radio / checkbox group member
      const g = k.split('.')[0]; out[g] = out[g] || [];
      if (e.checked) {
        let t = '';
        if (e.id) { const l = (e.ownerDocument).querySelector(`label[for="${CSS.escape(e.id)}"]`); if (l) t = l.innerText; }
        if (!t) { const w = e.closest('label'); t = w ? w.innerText : (e.nextElementSibling ? e.nextElementSibling.innerText : e.value); }
        out[g].push(clean(t));
      }
    } else if (e.type === 'checkbox') out[k] = e.checked;
    else if (e.type === 'file') out[k] = [...(e.files || [])].map(f => f.name);
    else if (e.tagName === 'SELECT') out[k] = e.selectedIndex >= 0 ? clean(e.options[e.selectedIndex].text) : '';
    else out[k] = e.value;
  }
  return out;
}
"""

CAPTCHA_SELECTORS = ["iframe[src*='recaptcha']", "iframe[src*='hcaptcha']", "iframe[src*='challenges.cloudflare.com']",
                     ".g-recaptcha", ".h-captcha", ".cf-turnstile"]
APPLY_BUTTONS = [r"^apply( now| for this job)?$", r"^i'?m interested$", r"^apply on company site$"]
SUBMIT_BUTTON = r"^(submit( application)?|send application|apply)$"
CONFIRM_TEXT = r"thank you|application (has been )?(received|submitted)|we('ve| have) received"


def norm(s) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip().lower()


# --------------------------------------------------------------------------- page helpers

def scope_of(page):
    """The element that holds the form: Greenhouse embeds it in an iframe."""
    if page.locator("iframe#grnhse_iframe").count():
        return page.frame_locator("iframe#grnhse_iframe").locator("body")
    return page.locator("body")


def dismiss_cookies(page) -> None:
    """Picks the most privacy-preserving option on consent banners."""
    for pat in (r"^(decline|reject) all$", r"^(decline|reject)( optional| non-essential)?( cookies)?$",
                r"^(only )?(strictly )?necessary( cookies)?( only)?$"):
        btn = page.get_by_role("button", name=re.compile(pat, re.I))
        if btn.count() and btn.first.is_visible():
            btn.first.click()
            page.wait_for_timeout(800)
            return


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


def has_captcha(page) -> bool:
    """True only for a challenge a human must solve; invisible reCAPTCHA badges don't count."""
    for s in CAPTCHA_SELECTORS:
        for el in page.locator(s).all():
            try:
                if not el.is_visible():
                    continue
                box = el.bounding_box() or {}
                src = el.get_attribute("src") or ""
                if "recaptcha" in src and "anchor" in src and round(box.get("width", 0)) <= 260:
                    continue
                if box.get("width", 0) < 40 or box.get("height", 0) < 40:
                    continue
                return True
            except Exception:
                continue
    if any("challenges.cloudflare.com" in f.url for f in page.frames):
        return True
    return page.get_by_text(re.compile(r"verify you are human", re.I)).count() > 0


# --------------------------------------------------------------------------- read / fill / verify

def read(scope) -> list[dict]:
    return scope.evaluate(READ_JS)


def readback(scope) -> dict:
    return scope.evaluate(READBACK_JS)


def _el(scope, key: str):
    return scope.locator(f"[data-vg='{key}']")


def _options(scope, key: str):
    return scope.locator(f"[data-vg^='{key}.']")


def _check_option(scope, page, key: str, want: str) -> bool:
    """Ticks the group member whose own label equals `want`."""
    opts = _options(scope, key)
    for i in range(opts.count()):
        o = opts.nth(i)
        lab = o.evaluate("""e => { let t = '';
            if (e.id) { const l = e.ownerDocument.querySelector(`label[for="${CSS.escape(e.id)}"]`); if (l) t = l.innerText; }
            if (!t) { const w = e.closest('label'); t = w ? w.innerText : (e.nextElementSibling ? e.nextElementSibling.innerText : e.value); }
            return t; }""")
        if norm(lab) == norm(want):
            try:
                o.check(timeout=3000, force=True)
            except Exception:
                o.evaluate("e => e.click()")
            if not o.is_checked():
                o.evaluate("e => { const l = e.closest('label') || e.parentElement; l.click(); }")
            return o.is_checked()
    return False


def fill(page, scope, items, root: Path = Path(".")) -> list[str]:
    """Applies every planned action. Files first: Ashby re-parses the resume and overwrites fields."""
    errors = []
    order = sorted(items, key=lambda i: 0 if i.action == "upload" else 1)
    for it in order:
        if it.action == "skip" or it.status != "ok":
            continue
        try:
            if it.action == "upload":
                _el(scope, it.key).set_input_files(str((root / it.value).resolve()))
                page.wait_for_timeout(2500)
            elif it.action == "type":
                _el(scope, it.key).fill(str(it.value), timeout=5000)
            elif it.action == "check":
                el = _el(scope, it.key)
                if not el.is_checked():
                    try:
                        el.check(timeout=3000, force=True)
                    except Exception:
                        el.evaluate("e => e.click()")
            elif it.action == "select" and it.kind == "select":
                _el(scope, it.key).select_option(label=str(it.value))
            elif it.action == "select" and it.kind in ("radio", "checkbox"):
                for v in (it.value if isinstance(it.value, list) else [it.value]):
                    if not _check_option(scope, page, it.key, v):
                        errors.append(f"{it.label}: no option '{v}'")
            elif it.action == "select" and it.kind == "combobox":
                el = _el(scope, it.key)
                el.click()
                el.fill("")
                want = str(it.value).split(",")[0].strip()      # "London, UK" -> type "London"
                el.press_sequentially(want, delay=60)
                opts = page.locator("[role=option]")
                opts.first.wait_for(state="visible", timeout=8000)
                exact = opts.filter(has_text=re.compile(rf"^\s*{re.escape(want)}\s*$", re.I))
                opt = exact if exact.count() else opts.filter(has_text=re.compile(rf"\b{re.escape(want)}", re.I))
                opt.first.click(timeout=5000)
        except Exception as e:
            errors.append(f"{it.label or it.key}: {type(e).__name__}: {str(e)[:120]}")
    page.wait_for_timeout(500)
    return errors


def verify(scope, items, before: dict) -> list[str]:
    """Every planned value is in its own field; every field the plan left alone is unchanged."""
    after = readback(scope)
    probs = []
    for it in items:
        got = after.get(it.key)
        if it.status != "ok" or it.action == "skip":
            if it.kind in ("text", "textarea") and norm(got) != norm(before.get(it.key)):
                probs.append(f"{it.label or it.key}: changed but not planned (now '{str(got)[:60]}')")
            continue
        if it.action == "type" and norm(got) != norm(it.value):
            probs.append(f"{it.label or it.key}: expected '{str(it.value)[:50]}', field has '{str(got)[:50]}'")
        elif it.action == "upload" and not got:
            probs.append(f"{it.label or it.key}: no file attached")
        elif it.action == "check" and got is not True:
            probs.append(f"{it.label or it.key}: not ticked")
        elif it.action == "select" and it.kind == "select" and norm(got) != norm(it.value):
            probs.append(f"{it.label or it.key}: expected option '{it.value}', got '{got}'")
        elif it.action == "select" and it.kind in ("radio", "checkbox"):
            want = {norm(v) for v in (it.value if isinstance(it.value, list) else [it.value])}
            if {norm(v) for v in (got or [])} != want:
                probs.append(f"{it.label or it.key}: expected {sorted(want)}, got {got}")
        elif it.action == "select" and it.kind == "combobox":
            if norm(str(it.value).split(",")[0]) not in norm(got):
                probs.append(f"{it.label or it.key}: expected '{it.value}', got '{got}'")
    return probs


def submit(page, wait_s: int = 30) -> str:
    """Clicks Submit and waits for a confirmation page. Returns submitted | captcha | unconfirmed | no_button."""
    scope = scope_of(page)
    btn = scope.get_by_role("button", name=re.compile(SUBMIT_BUTTON, re.I))
    if not btn.count():
        return "no_button"
    btn.last.click()
    for _ in range(max(1, wait_s)):
        page.wait_for_timeout(1000)
        body = page.inner_text("body")
        if re.search(CONFIRM_TEXT, body, re.I):
            return "submitted"
        if has_captcha(page):
            return "captcha"
    return "unconfirmed"
