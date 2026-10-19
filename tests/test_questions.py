"""Module 4 tests: every label below is copied from a real form Vanguard met (runs/applications.jsonl, 2026-10-18/19)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from vanguard.questions import classify

CASES = [
    ("Name", "text", "full_name"),
    ("Full name ✱", "text", "full_name"),
    ("* First name", "text", "first_name"),
    ("Last Name*", "text", "last_name"),
    ("Email ✱", "text", "email"),
    ("Phone", "text", "phone"),
    ("Link to your Github profile", "text", "github"),
    ("Link to your LinkedIn profile", "text", "linkedin"),
    ("LinkedIn URL", "text", "linkedin"),
    ("GitHub URL", "text", "github"),
    ("Portfolio URL", "text", "website"),
    ("LinkedIn Profile, Github, Personal Website, or Portfolio", "text", "links_combined"),
    ("Name Pronunciation | How do you pronounce your name?", "text", "pronunciation"),
    ("* Where are you located?", "combobox", "location"),
    ("Current location ✱", "text", "location"),
    ("Location (City)*", "text", "city"),
    ("Please select the country where you currently reside. *", "select", "country"),
    ("Address (Optional)", "text", "address"),
    ("postcode", "text", "postcode"),
    ("Current company", "text", "current_employer"),
    ("Who is your current or previous employer?*", "text", "current_employer"),
    ("What is your current or previous job title?*", "text", "current_title"),
    ("High School Name ✱", "text", "high_school"),
    ("Year of High School Graduation ✱", "text", "high_school_year"),
    ("Which university are you currently attending or did you last attend?", "select", "school"),
    ("Please include your intended graduation year for the degree", "text", "grad_year"),
    ("As part of our commitment to understanding candidates' backgrounds, we invite you to share your SAT or ACT scores", "text", "test_scores"),
    ("Are you currently enrolled in a degree programme, or did you complete a degree within the last 12 months?*", "radio", "enrolled_recently"),
    ("Do you currently have the right to work in the UK? If your permission has an expiry date or you would need sponsorship, tell us", "textarea", "rtw"),
    ("Applicants must already have the legal right to work in the UK, as we are unable to offer visa sponsorship. Please confirm", "radio", "rtw"),
    ("Are you currently eligible to work in United Kingdom?*", "radio", "rtw"),
    ("Do you have the right to work in the UK without requiring sponsorship?", "radio", "rtw"),
    ("Are you legally authorized to work in the country for which you are applying?*", "select", "rtw"),
    ("Will you now, or in the future, require sponsorship to work in the UK?", "radio", "sponsorship"),
    ("Do you require visa sponsorship, now or in the future, to continue working in United Kingdom?*", "select", "sponsorship"),
    ("* Please confirm the current right to work document you hold, eg. British passport, settled status, ILR, Skilled Worker", "text", "rtw_document"),
    ("Can you work from our Vauxhall office at least three days a week?", "radio", "office"),
    ("* What is your Notice Period?", "text", "notice_period"),
    ("* What are your current salary expectations?", "text", "salary"),
    ("How did you hear about ElevenLabs?", "radio", "how_heard"),
    ("If other, please specify below", "text", "how_heard_other"),
    ("Do you opt-in to receive WhatsApp messages from Stripe Recruiting?*", "radio", "messaging_opt_in"),
    ("To ensure we can focus fully on our conversation, we use BrightHire to record and transcribe interviews. This", "radio", "recording_consent"),
    ("I have read, understand and accept the content of the Privacy Notice and consent to the processing of my data", "checkbox", "privacy_consent"),
    ("Have you been convicted of any criminal offences that are not yet spent", "radio", "legal_declaration"),
    ("* In the last 5 years, have you had any CCJs, IVAs, bankruptcies or debt management plans?", "radio", "legal_declaration"),
    ("Have you been dismissed from any employment in the past five years", "radio", "legal_declaration"),
    ("Have you previously worked for Starling Group (either directly employed or as a contractor/temporary worker)?", "radio", "previously_employed_here"),
    ("Do you have an active offer deadline or another recruiting-process deadline we should be aware of?", "text", "deadline"),
    ("Please select the top 3 engineer profile types below that are most relevant to your interests", "checkbox", "preferences"),
    ("Second engineering preference*", "select", "preferences"),
    ("Gender", "select", "eeo"),
    ("Cover letter (Optional)", "textarea", "cover_letter"),
    ("Cover letter", "file", "cover_letter_file"),
    ("Resume/CV ✱ Attach resume/CV", "file", "resume_file"),
    ("ADDITIONAL INFORMATION", "textarea", "additional_info"),
    ("Why ElevenLabs, and why now?", "textarea", "why_company"),
    ("Why Stripe?", "textarea", "why_company"),
    ("* Why would Engine regret not hiring you?", "textarea", "why_you"),
    ("What's the most impactful thing you've built? What was your specific contribution?", "textarea", "impact_project"),
    ("How did you know it worked? What did success actually look like?", "textarea", "success_measure"),
    ("Have you used ElevenLabs - even in a personal or side project? What did you build or explore?", "textarea", "product_usage"),
    ("* Describe an LLM or NLP system you personally helped take into production. What did it do, which parts did you build?", "textarea", "llm_production"),
    ("* What is the most demanding Python data or model pipeline you’ve built? Give its approximate scale, how it was", "textarea", "pipeline_scale"),
    ("If Palantir didn't exist, what kind of company or work would you be most excited and interested in working at?", "textarea", "alt_company"),
    ("What is the hardest technical challenge you've faced as part of work experience or a personal project?", "textarea", "hardest_challenge"),
    ("Tell us about a project you are proud of", "textarea", "project_generic"),
    ("Anything we should know about your approach to code review?", "textarea", "free_text"),
]


@pytest.mark.parametrize("label,kind,intent", CASES)
def test_classify(label, kind, intent):
    assert classify(label, kind) == intent


def test_empty_and_unknown():
    assert classify("", "text") == "unknown"
    assert classify("+44", "select") == "unknown"
