"""Offline end-to-end test: fake ATS feed -> filter -> tailor -> enrich -> approve -> draft (dry run)."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pytest

from vanguard import db, filters, grounding, keywords

JD_GOOD = """Applied AI Engineer (Graduate) - London. Join our Agents team. You will build LLM agents in Python and TypeScript,
ship streaming APIs over websockets, write structured outputs with Pydantic, and own CI/CD in GitHub Actions.
Nice to have: React, Docker, AWS, RAG. 0-2 years of experience. Hybrid in London, UK."""
JD_SENIOR = "Senior Software Engineer, London. 6+ years of commercial experience with Go and Kubernetes."
JD_CLEARANCE = "Software Engineer (Graduate), London. Must hold active SC clearance. Python."


@pytest.fixture()
def tmpdb(tmp_path, monkeypatch):
    path = tmp_path / "v.db"
    real = db.connect
    monkeypatch.setattr(db, "connect", lambda p=path: real(p))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    return path


def fake_board(_token):
    yield {"id": "1", "title": "Applied AI Engineer (Graduate)", "location": "London, UK", "url": "https://x/1", "description": JD_GOOD}
    yield {"id": "2", "title": "Senior Software Engineer", "location": "London", "url": "https://x/2", "description": JD_SENIOR}
    yield {"id": "3", "title": "Software Engineer (Graduate)", "location": "London", "url": "https://x/3", "description": JD_CLEARANCE}
    yield {"id": "4", "title": "Software Engineer, New Grad", "location": "New York, NY", "url": "https://x/4", "description": "Python. New York only."}


def test_filters():
    cfg = {"max_years_required": 3}
    assert filters.evaluate({"title": "Applied AI Engineer (Graduate)", "description": JD_GOOD, "location": "London"}, cfg)[0]
    assert filters.evaluate({"title": "Senior Software Engineer", "description": JD_SENIOR, "location": "London"}, cfg)[1].startswith("senior")
    assert filters.evaluate({"title": "Software Engineer", "description": JD_SENIOR, "location": "London"}, cfg)[1] == "requires 6+ years"
    assert filters.evaluate({"title": "Software Engineer (Graduate)", "description": JD_CLEARANCE, "location": "London"}, cfg)[1] == "security clearance required"


def test_keywords_word_boundaries():
    k = keywords.extract("We use Go-to-market tooling, Python, Next.js and C++. Node.js backend.")
    assert {"python", "next.js", "c++", "node.js"} <= set(k)
    assert "go" not in k


def test_grounding_rejects_invented_claims():
    kg = json.loads((ROOT / "data" / "knowledge_graph.json").read_text())
    kepler = next(p for p in kg["projects"] if p["id"] == "kepler")
    assert grounding.check("Integrated OpenRouter streaming LLMs in Kotlin with Android Keystore AES-GCM encryption", kepler) == []
    v = grounding.check("Served 50,000 users with Kubernetes and Kafka", kepler)
    assert any("kubernetes" in x for x in v) and any("50,000" in x for x in v)


def test_end_to_end(tmpdb, monkeypatch):
    import enricher, mailer, scraper, sync, tailor
    monkeypatch.setitem(scraper.ADAPTERS, "greenhouse", fake_board)
    monkeypatch.setattr(scraper, "load_targets", lambda: {"filters": {"max_years_required": 3}, "companies": [
        {"id": "acme", "name": "Acme AI", "tier": "B", "size_bucket": "startup", "employees": 40, "ats": "greenhouse", "ats_token": "acme", "email_domain": "acme.ai", "email_pattern": "{first}"}]})
    monkeypatch.setattr(scraper.time, "sleep", lambda s: None)
    monkeypatch.setattr(scraper.sponsors, "refresh", lambda: None)
    monkeypatch.setattr(scraper.sponsors, "CACHE", Path("/nonexistent/sponsors.csv"))
    stats = scraper.scrape()
    assert stats == {"new": 4, "kept": 1, "filtered": 3, "errors": []}

    res = tailor.run()
    assert len(res) == 1 and res[0]["after"] >= res[0]["before"]
    assert len(res[0]["projects"]) >= 3

    assert enricher.run() == 1
    with db.connect(tmpdb) as conn:
        o = conn.execute("SELECT * FROM outreach").fetchone()
        assert o["word_count"] < 90, o["body"]
        assert o["subject"] == o["subject"].lower()
        personas = [r["persona"] for r in conn.execute("SELECT persona FROM contacts")]
        assert personas[0] == "cto"
        t = conn.execute("SELECT latex FROM tailorings").fetchone()["latex"]
        assert "tabular" not in t and "multicol" not in t

    # Nothing is drafted before a human approves.
    assert mailer.run(dry_run=True)["drafted"] == 0
    appr = ROOT / "out" / "_test_approvals.json"
    appr.write_text(json.dumps({"approvals": [{"jobId": "acme:1", "approvedAt": db.now(), "status": "approved",
                                               "contact": {"name": "Ada Lovelace", "email": "ada@acme.ai"},
                                               "email": {"subject": o["subject"], "body": o["body"]}}]}))
    assert sync.import_approvals(appr) == 1
    appr.unlink()
    assert mailer.run(dry_run=True)["drafted"] == 1
    assert sync.export(ROOT / "out" / "_test_queue.json") == 1
    (ROOT / "out" / "_test_queue.json").unlink()
