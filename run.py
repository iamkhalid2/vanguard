"""Orchestrator for the 6-hourly cron: ingest GitHub -> scrape -> tailor -> enrich -> draft approved mail -> export queue."""
from __future__ import annotations

import json
import sys

import enricher
import mailer
import scraper
import sync
import tailor
from vanguard import ghgraph, llm


def main() -> None:
    report = {}
    try:
        ghgraph.build("iamkhalid2")
        report["github"] = "refreshed"
    except Exception as e:  # stale graph is fine for one run
        report["github"] = f"skipped: {e}"
    report["scrape"] = scraper.scrape()
    report["tailored"] = len(tailor.run(limit=25))
    report["outreach_drafted"] = enricher.run()
    try:
        report["gmail"] = mailer.run()
    except KeyError:
        report["gmail"] = "skipped: Gmail secrets not set"
    report["queue_exported"] = sync.export()
    report["llm"] = llm.MODEL if llm.available() else "off (deterministic)"
    print(json.dumps(report, indent=2))
    if report["scrape"]["errors"]:
        print("some boards failed, see stderr", file=sys.stderr)


if __name__ == "__main__":
    main()
