"""Deterministic ATS keyword extraction and scoring. No LLM involved, so scores are
reproducible and identical to the Command Center's JS implementation.

- extract(text)     -> {canonical_term: count}, matched on word boundaries against config/skills_lexicon.json
- coverage(jd, cv)  -> weighted share of JD terms that appear in the CV (what an ATS keyword filter approximates)
- cosine(jd, cv)    -> cosine similarity of the two term-frequency vectors
"""
from __future__ import annotations

import json
import math
import re
from functools import lru_cache
from pathlib import Path

LEXICON_PATH = Path(__file__).resolve().parent.parent / "config" / "skills_lexicon.json"
# Hard skills matter more to ATS filters and recruiters than generic concepts.
CATEGORY_WEIGHT = {"languages": 3.0, "frameworks": 3.0, "ai": 2.5, "infra": 2.0, "concepts": 1.0}


@lru_cache(maxsize=1)
def _patterns() -> list[tuple[str, str, re.Pattern]]:
    lex = json.loads(LEXICON_PATH.read_text())
    out = []
    for cat, terms in lex.items():
        if cat.startswith("_") or cat == "verbs":
            continue
        for canon, aliases in terms.items():
            alts = sorted({a.strip() for a in aliases + ([canon] if len(canon) > 2 else []) if a.strip()}, key=len, reverse=True)
            rx = r"(?<![\w+#.])(?:" + "|".join(re.escape(a) for a in alts) + r")(?![\w+#])"
            out.append((canon, cat, re.compile(rx, re.IGNORECASE)))
    return out


def category(term: str) -> str:
    for canon, cat, _ in _patterns():
        if canon == term:
            return cat
    return "concepts"


def extract(text: str) -> dict[str, int]:
    found: dict[str, int] = {}
    for canon, _cat, rx in _patterns():
        n = len(rx.findall(text or ""))
        if n:
            found[canon] = n
    return found


def coverage(jd: dict[str, int], cv: dict[str, int]) -> float:
    if not jd:
        return 0.0
    total = sum(CATEGORY_WEIGHT[category(t)] for t in jd)
    hit = sum(CATEGORY_WEIGHT[category(t)] for t in jd if t in cv)
    return round(hit / total, 4)


def cosine(jd: dict[str, int], cv: dict[str, int]) -> float:
    keys = set(jd) | set(cv)
    w = {k: CATEGORY_WEIGHT[category(k)] for k in keys}
    dot = sum(jd.get(k, 0) * cv.get(k, 0) * w[k] ** 2 for k in keys)
    na = math.sqrt(sum((jd.get(k, 0) * w[k]) ** 2 for k in keys))
    nb = math.sqrt(sum((cv.get(k, 0) * w[k]) ** 2 for k in keys))
    return round(dot / (na * nb), 4) if na and nb else 0.0


def missing(jd: dict[str, int], cv: dict[str, int]) -> list[str]:
    """JD terms the candidate has no evidence for, heaviest first. Report these; never paper over them."""
    return sorted((t for t in jd if t not in cv), key=lambda t: (-CATEGORY_WEIGHT[category(t)], -jd[t]))
