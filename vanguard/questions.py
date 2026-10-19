"""Module 4: question classifier. Maps a form label to one intent, so the answer step never has to
guess from loose substrings.

Rules are ordered and the first match wins. Each rule is anchored on what the question is asking,
not on any word it happens to contain: "Link to your GitHub profile" is `github`, but "Tell us about
a GitHub project" is `project_generic`. Unknown labels return "unknown", and the planner treats an
unknown long-text question as free text written from the question itself, never as a blank.
"""
from __future__ import annotations

import re

# intents whose answer is a written paragraph, not a fact
WRITEUPS = {
    "why_company", "impact_project", "success_measure", "product_usage", "hardest_challenge",
    "llm_production", "pipeline_scale", "why_you", "alt_company", "cover_letter", "additional_info",
    "project_generic", "android_project", "frontend_project", "free_text",
}
# intents only Khalid can answer truthfully (legal declarations); never defaulted
PERSONAL = {"legal_declaration"}

_R = [
    # --- consent / opt-in / EEO (checked first: their labels mention many other words)
    ("eeo", r"\b(gender|sex|race|racial|ethnic\w*|veteran|disabilit\w*|sexual orientation|transgender|hispanic|latin[oax]|"
            r"religio\w*|socio-?economic|caring responsibilit\w*|first generation|free school meals|neurodiver\w*)\b"),
    ("pronouns", r"\bpronouns?\b"),
    ("messaging_opt_in", r"\b(whatsapp|sms|text messages?|newsletter|marketing|talent (pool|community)|future (roles|opportunities|openings))\b"),
    ("recording_consent", r"\b(record(ing)?|transcrib\w*|brighthire|metaview)\b.*\binterview"),
    ("privacy_consent", r"\b(privacy (notice|policy)|data protection|processing of (my|your) (personal )?data|i (have read|agree|consent|acknowledge))\b"),
    ("legal_declaration", r"\b(convicted|criminal|offen[cs]es?|ccjs?|county court|ivas?|bankrupt\w*|debt management|dismissed from|"
                          r"background checks?|credit checks?|disciplinary)\b"),
    # --- identity and contact
    ("pronunciation", r"\bpronounc\w*\b"),
    ("preferred_name", r"\b(preferred|chosen) (first )?name\b"),
    ("first_name", r"\b(first|given|fore) ?name\b"),
    ("last_name", r"\b(last|family|sur) ?name\b"),
    ("email", r"\be-?mail\b"),
    ("phone", r"\b(phone|mobile|telephone|cell)\b"),
    ("linkedin", r"\blinked ?in\b(?!.*\b(github|portfolio|website)\b)"),
    ("links_combined", r"\blinked ?in\b.*\b(github|portfolio|website)\b"),
    ("github", r"^\W*(link to )?(your )?github( profile| url| username)?\W*$|github (profile|url|link|username)"),
    ("website", r"\b(website|portfolio|personal site|blog|other (url|link|website))\b"),
    ("full_name", r"^\W*(full |legal )?name\W*$|^\W*your (full )?name\b|^\W*name\b(?!.*pronoun)"),
    # --- right to work
    ("rtw_document", r"\b(document|passport|settled status|ilr|visa type|type of visa|immigration status)\b"),
    ("rtw", r"\b(right to work|eligib\w* to work|work authori[sz]\w*|legally (allowed|entitled|authori[sz]ed)|permission to work)\b"),
    ("sponsorship", r"\bsponsor\w*\b"),
    ("clearance", r"\b(security clearance|sc clearance|dv clearance|vetting)\b"),
    # --- location
    ("postcode", r"\b(post ?code|zip( code)?|postal code)\b"),
    ("address", r"^\W*(home |current |street |postal |residential )?address\b|address line 1"),
    ("country", r"^\W*(country|nationality)\b|country (of|where|you)|which country|(select|choose) (the|your) country"),
    ("city", r"^\W*(city|town)\b|location \(city\)"),
    ("relocation", r"\brelocat\w*"),
    ("office", r"\b(office|on-?site|in person|hybrid|days a week|commute)\b"),
    ("location", r"\b(location|where are you (currently )?(based|located)|where do you live|currently resid\w*|confirm your current location)\b"),
    # --- education and employment facts
    ("high_school_year", r"\bhigh school\b.*\b(year|date|graduat\w*)\b"),
    ("high_school", r"\b(high school|secondary school|a-?levels?)\b"),
    ("test_scores", r"\b(sat|act|gpa|ucas points|a-level grades)\b.*\bscores?\b|\bscores?\b.*\b(sat|act)\b"),
    ("enrolled_recently", r"\b(currently enrolled|complete[d]? a degree within|recent graduate)\b"),
    ("grad_year", r"\bgraduat\w* (year|date)|year of graduation|when did you graduate|intended graduation\b"),
    ("degree", r"^\W*(degree|qualification|field of study|major|discipline)\b|highest (level of )?(education|qualification)"),
    ("school", r"\b(university|school|college|institution)\b"),
    ("years_experience", r"\b(how many )?years of (commercial |professional |relevant |software |engineering |industry )*experience\b|how many years"),
    ("current_employer", r"\b(current|previous|most recent) (or previous )?(company|employer)\b|who is your current"),
    ("current_title", r"\b(current|previous|most recent) (or previous )?(job )?(title|role|position)\b"),
    ("previously_employed_here", r"\b(previously|ever|before) (worked|been employed|applied)\b.*\b(for|at|with|to)\b"),
    # --- logistics
    ("notice_period", r"\bnotice( period)?\b"),
    ("salary", r"\b(salary|compensation|pay|remuneration)( expectations?| requirements?)?\b"),
    ("start_date", r"\b(start date|when can you start|earliest (start|availability)|available to start)\b"),
    ("deadline", r"\b(deadline|other offers?|timeline)\b"),
    ("how_heard_other", r"^\W*if other|please specify\W*$"),
    ("how_heard", r"\bhow did you (hear|find|learn)|where did you (hear|find|see)|^\W*source\W*$"),
    ("referral", r"\b(referr\w*|who referred)\b"),
    ("preferences", r"\b(preference|top 3|top three|rank|which teams?|areas? of interest|engineer profile types?)\b"),
    # --- write-ups
    ("cover_letter", r"\bcover letter\b"),
    ("product_usage", r"\b(have you used|used .{0,40}(before|personal|side project)|experience with our (product|api)|what did you build or explore)\b"),
    ("why_you", r"\bwhy (should we hire you|would \w+ regret|are you a (good|great) fit)|what makes you\b|what would you bring"),
    ("why_company", r"\bwhy (do you want to (work|join)|are you (interested in|applying)|(this|our|the) (role|company|team|position|opportunity))|"
                    r"\bwhy (?-i:[A-Z])[\w.&-]*\b(,? and why now)?\W*$|\bwhy now\b|what (excites|interests|attracts|draws) you|\bmotivat\w*"),
    ("alt_company", r"\bif \w+ (didn.t|did not) exist|other than \w+, (what|where)"),
    ("llm_production", r"\b(llm|nlp|language model|generative ai|genai)\b"),
    ("pipeline_scale", r"\b(pipeline|data processing|etl|model training)\b"),
    ("hardest_challenge", r"\b(hardest|most (difficult|challenging)|toughest|biggest) (technical )?(challenge|problem|bug)\b|challenge you.ve faced"),
    ("impact_project", r"\bmost (impactful|proud|significant|interesting)|proudest|impact\w* thing you.ve built"),
    ("success_measure", r"\b(how did you know it worked|what did success look like|how did you measure|how did you validate)\b"),
    ("android_project", r"\b(android|kotlin|mobile) (project|experience|app)\b"),
    ("frontend_project", r"\b(frontend|front-end|web|react) (project|experience)\b"),
    ("project_generic", r"\b(tell us about|describe|walk us through|share) (a|an|one|your)\b.*\b(project|thing you built|system|product)\b"),
    ("additional_info", r"^\W*(additional information|anything else|is there anything else|other information|additional comments)\b"),
]
_RULES = [(intent, re.compile(rx, re.I)) for intent, rx in _R]


def classify(label: str, kind: str = "text") -> str:
    """Intent for one field label. `kind` is the control type (text, textarea, select, radio, checkbox, file, combobox)."""
    text = re.sub(r"\s+", " ", (label or ""))
    text = re.sub(r"\((optional|required)\)", "", text, flags=re.I).strip(" *✱:")
    if not text:
        return "unknown"
    if kind == "file":
        if re.search(r"cover", text, re.I):
            return "cover_letter_file"
        if re.search(r"photo|avatar|picture|image|autofill", text, re.I):
            return "photo"
        if re.search(r"resume|cv|attach|upload|choose file|drop", text, re.I):
            return "resume_file"
        return "unknown"
    for intent, rx in _RULES:
        if rx.search(text):
            return intent
    if kind == "textarea":
        return "free_text"
    return "unknown"
