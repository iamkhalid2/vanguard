"""Module D/E delivery: turns human-approved outreach into Gmail *drafts*. It never sends.

Only rows with approved_at set (from an approvals.json exported by the Command Center and
imported with `python sync.py import approvals.json`) and a contact email are drafted.
A hard cap of `daily_draft_cap` (default 15) drafts per UTC day protects your sender reputation.
You press Send in Gmail yourself; then mark it sent in the Command Center.

One-time auth (run locally, opens a browser):
  python mailer.py --auth client_secret.json
  -> prints GMAIL_REFRESH_TOKEN; store it, GMAIL_CLIENT_ID and GMAIL_CLIENT_SECRET as GitHub Actions secrets.
Scope requested: gmail.compose (create drafts; cannot read your inbox).
"""
from __future__ import annotations

import argparse
import base64
import json
import os
from email.message import EmailMessage
from pathlib import Path

import requests

from vanguard import db

ROOT = Path(__file__).resolve().parent
CAP = json.loads((ROOT / "config" / "targets.json").read_text())["filters"]["daily_draft_cap"]
SCOPES = ["https://www.googleapis.com/auth/gmail.compose"]
SENDER = os.environ.get("VANGUARD_FROM", "contact@iamkhalid.xyz")


def access_token() -> str:
    r = requests.post("https://oauth2.googleapis.com/token", timeout=20, data={
        "client_id": os.environ["GMAIL_CLIENT_ID"], "client_secret": os.environ["GMAIL_CLIENT_SECRET"],
        "refresh_token": os.environ["GMAIL_REFRESH_TOKEN"], "grant_type": "refresh_token"})
    r.raise_for_status()
    return r.json()["access_token"]


def build_message(to: str, subject: str, body: str, pdf: Path | None) -> str:
    msg = EmailMessage()
    msg["To"], msg["From"], msg["Subject"] = to, SENDER, subject
    msg.set_content(body)
    if pdf and pdf.exists():
        msg.add_attachment(pdf.read_bytes(), maintype="application", subtype="pdf", filename="Mohammad-Khalid-Resume.pdf")
    return base64.urlsafe_b64encode(msg.as_bytes()).decode()


def run(dry_run: bool = False) -> dict:
    stats = {"drafted": 0, "skipped_cap": 0}
    with db.connect() as conn:
        room = CAP - db.drafts_today(conn)
        rows = conn.execute("""SELECT o.*, c.email, c.name, t.pdf_path FROM outreach o
                               JOIN contacts c ON c.id=o.contact_id LEFT JOIN tailorings t ON t.job_id=o.job_id
                               WHERE o.approved_at IS NOT NULL AND o.gmail_draft_id IS NULL AND c.email IS NOT NULL
                               ORDER BY o.approved_at""").fetchall()
        token = None if dry_run or not rows else access_token()
        for r in rows:
            if room <= 0:
                stats["skipped_cap"] += 1
                continue
            first = (r["name"] or "there").split()[0]
            body = r["body"].replace("{first_name}", first)
            raw = build_message(r["email"], r["subject"], body, Path(r["pdf_path"]) if r["pdf_path"] else None)
            if dry_run:
                draft_id = "dry-run"
            else:
                resp = requests.post("https://gmail.googleapis.com/gmail/v1/users/me/drafts", timeout=30,
                                     headers={"Authorization": f"Bearer {token}"}, json={"message": {"raw": raw}})
                resp.raise_for_status()
                draft_id = resp.json()["id"]
            conn.execute("UPDATE outreach SET gmail_draft_id=?, drafted_at=? WHERE id=?", (draft_id, db.now(), r["id"]))
            db.set_status(conn, r["job_id"], "cold_email_drafted")
            stats["drafted"] += 1
            room -= 1
    return stats


def auth(client_secret: str) -> None:
    from google_auth_oauthlib.flow import InstalledAppFlow
    creds = InstalledAppFlow.from_client_secrets_file(client_secret, SCOPES).run_local_server(port=0)
    print("GMAIL_CLIENT_ID=", creds.client_id, "\nGMAIL_CLIENT_SECRET=", creds.client_secret, "\nGMAIL_REFRESH_TOKEN=", creds.refresh_token, sep="")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--auth", metavar="CLIENT_SECRET_JSON")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    if a.auth:
        auth(a.auth)
    else:
        print(json.dumps(run(a.dry_run), indent=2))
