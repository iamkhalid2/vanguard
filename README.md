# Vanguard

Human-in-the-loop job search for entry-level SWE / applied AI roles in London. Architecture, schemas and the reasoning behind each design choice are in [ARCHITECTURE.md](ARCHITECTURE.md).

- **Command Center** (works now, no setup): https://claude.ai/artifact/M9vgMy8nwksJ5kPsqUBFd8. The source is in `command-center/index.html`.
- **Backend**: this folder. Push it to a private GitHub repo and it runs every 6 hours on GitHub Actions.

## Layout

```
scraper.py      Module A  board feeds -> filters -> SQLite
tailor.py       Module C  ATS scoring, project selection, grounded bullets, LaTeX, cover letter
enricher.py     Module D  persona routing, LinkedIn search links, <90-word email
mailer.py       Module D/E  approved emails -> Gmail drafts (never sends), 15/day cap
sync.py         bridge     export out/queue.json, import approvals JSON
apply.py        Module E  fill + verify application forms; submit only ids a human approved (see ARCHITECTURE.md 3b)
run.py          the cron entry point (all of the above in order)
vanguard/       db.py (schema), keywords.py, filters.py, grounding.py, latex.py, llm.py, ghgraph.py (Module B),
                profile.py, evidence.py, questions.py, writer.py, answers.py, forms.py, tracker.py, shortlist.py
config/         targets.json (companies + caps), skills_lexicon.json, base_resume.txt
data/           knowledge_graph.json, vanguard.db (created on first run)
tests/          offline end-to-end test (fake board -> draft)
```

## Run locally

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
python -m playwright install chromium   # once, for the form tests
pytest -q                       # all modules + the whole-machine test, offline
python scraper.py --check       # which board tokens resolve (fix 404s in config/targets.json)
python run.py                   # full pipeline; deterministic unless ANTHROPIC_API_KEY is set
python sync.py export           # -> out/queue.json, import it in the Command Center Pipeline
```

## Apply step

```bash
python apply.py --plan --batch config/apply_batch.json   # read each form, write runs/plans/<id>.md answer sheets, touch nothing
python apply.py --brief <id>                             # what Claude needs to write a better answer for <id>
python apply.py --batch config/apply_batch.json          # fill, read every value back, screenshot -> runs/ready-to-submit.json
python apply.py --submit <id>,<id>                        # submit exactly the ids a human approved
```

## Deploy on GitHub Actions ($0)

1. Create a **private** repo and push this folder (the workflow is at `.github/workflows/vanguard.yml`).
2. Gmail drafts (optional; the Command Center can also make drafts through your Gmail connector):
   - Google Cloud console: create a project, enable the Gmail API, create an OAuth client of type *Desktop app*, download `client_secret.json`.
   - `python mailer.py --auth client_secret.json` and log in. It asks only for `gmail.compose` (draft creation, no inbox reading).
   - Add `GMAIL_CLIENT_ID`, `GMAIL_CLIENT_SECRET` and `GMAIL_REFRESH_TOKEN` as repo secrets.
3. Optional secrets: `ANTHROPIC_API_KEY` (backend rewrites, paid), `HUNTER_API_KEY` (email patterns, 25 free lookups a month).
4. Run the workflow once by hand from the Actions tab. After that it runs on cron at 17 minutes past every sixth hour.

## Daily loop

1. Open the Command Center. Import the latest `out/queue.json`, or use **Find jobs** (Workable / Indeed).
2. **Workbench**: check the screen flags and the gap list, then click **Tailor**. Read the bullet diff, edit the cover letter, take the `.tex` to Overleaf or use the backend PDF.
3. Submit the application yourself on the company site, then move the job to *Applied*.
4. **Outreach**: open the LinkedIn search, pick the person, add their email, write the email, then **Approve & stage**. That creates a Gmail draft. Send it from Gmail yourself.
5. To have the backend draft instead, use **Export approvals**, commit the file to `approvals/`, and push. The push triggers the workflow.

## Guarantees

- Nothing is sent or submitted automatically. `approved_at` is set only by an approvals file or the Approve button.
- No bullet ships with a skill or number missing from `data/knowledge_graph.json` (`vanguard/grounding.py`).
- At most 2 approved applications per company in any 30 days, and 15 drafts per day.
