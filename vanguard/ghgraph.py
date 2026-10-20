"""Module B: rebuild the Candidate Knowledge Graph from the GitHub REST API.

  python -m vanguard.ghgraph [--user iamkhalid2]

Reads public repos, READMEs, dependency manifests and the last 30 commit messages per repo,
then merges into data/knowledge_graph.json. Hand-curated fields (features, metrics,
problem_solution, flagship) are kept; only skills, stars and auto_evidence are refreshed, so a
rebuild can add evidence but never removes what you wrote yourself.
Set GITHUB_TOKEN for 5,000 requests/hour instead of 60 (Actions provides one automatically).
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import tomllib
from pathlib import Path

import requests

from . import keywords

KG_PATH = Path(__file__).resolve().parent.parent / "data" / "knowledge_graph.json"
API = "https://api.github.com"
MANIFESTS = ["package.json", "pyproject.toml", "requirements.txt", "Cargo.toml", "pubspec.yaml",
             "build.gradle.kts", "app/build.gradle.kts", "gradle/libs.versions.toml", "go.mod"]


def gh(path: str, **params):
    h = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    if os.environ.get("GITHUB_TOKEN"):
        h["Authorization"] = f"Bearer {os.environ['GITHUB_TOKEN']}"
    r = requests.get(API + path, headers=h, params=params, timeout=30)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    return r.json()


def file_text(owner: str, repo: str, path: str) -> str | None:
    f = gh(f"/repos/{owner}/{repo}/contents/{path}")
    if not f or isinstance(f, list) or f.get("encoding") != "base64":
        return None
    return base64.b64decode(f["content"]).decode("utf-8", "replace")


def deps_from(path: str, text: str) -> list[str]:
    try:
        if path.endswith("package.json"):
            j = json.loads(text)
            return list({**j.get("dependencies", {}), **j.get("devDependencies", {})})
        if path.endswith("pyproject.toml"):
            t = tomllib.loads(text)
            return [re.split(r"[<>=~ \[]", d)[0] for d in t.get("project", {}).get("dependencies", [])]
        if path.endswith("Cargo.toml"):
            return list(tomllib.loads(text).get("dependencies", {}))
    except Exception:
        return []
    return re.findall(r"^[\s\"']*([A-Za-z][\w.\-]+)", text, re.M)[:80]


def build(user: str) -> dict:
    kg = json.loads(KG_PATH.read_text())
    by_repo = {(p.get("repo") or "").lower().rstrip("/"): p for p in kg["projects"]}
    for r in gh(f"/users/{user}/repos", per_page=100, sort="pushed") or []:
        if r["fork"] or r["archived"]:
            continue
        owner, name = r["owner"]["login"], r["name"]
        readme = file_text(owner, name, "README.md") or ""
        deps = []
        for m in MANIFESTS:
            t = file_text(owner, name, m)
            if t:
                deps += deps_from(m, t)
        commits = [c["commit"]["message"].split("\n")[0] for c in (gh(f"/repos/{owner}/{name}/commits", per_page=30) or [])]
        langs = list((gh(f"/repos/{owner}/{name}/languages") or {}).keys())
        evidence = "\n".join([r.get("description") or "", readme[:8000], " ".join(deps), " ".join(langs), "\n".join(commits)])
        terms = sorted(keywords.extract(evidence))
        url = r["html_url"].lower()
        p = by_repo.get(url)
        if p is None:
            p = {"id": name.lower(), "name": name, "repo": r["html_url"], "demo": r.get("homepage") or None,
                 "years": r["created_at"][:4], "flagship": False, "skills": [], "features": [r["description"]] if r.get("description") else [],
                 "metrics": [], "problem_solution": [], "on_resume": False,
                 # a repo found by the sweep ranks below every curated project until someone ranks it by hand
                 "showcase_rank": max([q.get("showcase_rank", 0) for q in kg["projects"]] + [0]) + 1}
            kg["projects"].append(p)
        p["skills"] = sorted(set(p["skills"]) | set(terms) | {l.lower() for l in langs})
        p["metrics"] = [m for m in p["metrics"] if "GitHub stars" not in m] + ([f"{r['stargazers_count']} GitHub stars"] if r["stargazers_count"] else [])
        p["auto_evidence"] = {"languages": langs, "dependencies": sorted(set(deps))[:60], "recent_commits": commits[:10], "pushed_at": r["pushed_at"]}
    kg["generated_at"] = __import__("datetime").date.today().isoformat()
    KG_PATH.write_text(json.dumps(kg, indent=2))
    return kg


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--user", default="iamkhalid2")
    print(f"knowledge graph now has {len(build(ap.parse_args().user)['projects'])} projects")
