"""Optional Claude calls for the backend. If ANTHROPIC_API_KEY is unset the pipeline runs fully
deterministic (no rewrites, no cover letters) and the Command Center does the writing instead,
using Claude from inside the artifact at no API cost."""
from __future__ import annotations

import json
import os
import re

MODEL = os.environ.get("VANGUARD_MODEL", "claude-sonnet-5-5")


def available() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def complete_json(system: str, user: str, max_tokens: int = 2000) -> dict:
    import anthropic

    client = anthropic.Anthropic()
    msg = client.messages.create(
        model=MODEL, max_tokens=max_tokens, system=system,
        messages=[{"role": "user", "content": user}],
    )
    text = "".join(b.text for b in msg.content if b.type == "text")
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError("model returned no JSON object")
    return json.loads(m.group(0))
