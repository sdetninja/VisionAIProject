"""Validate LLM output; on any failure the caller falls back to the deterministic template."""
from __future__ import annotations
import json
import re

SEV = re.compile(r"\b(low|medium|high|critical)\b[ -]*(severity|risk)", re.I)


def validate(raw: str, facts: dict, allowed_ids: set, max_len=600) -> tuple:
    """Returns (parsed_dict | None, problems list)."""
    problems = []
    try:
        s = raw.strip()
        s = s[s.find("{"): s.rfind("}") + 1]
        d = json.loads(s)
    except Exception:
        return None, ["not valid JSON"]
    if not isinstance(d, dict):
        return None, ["not an object"]
    summ, acts, cits = d.get("summary"), d.get("recommended_actions"), d.get("citations", [])
    if not isinstance(summ, str) or not summ.strip():
        problems.append("missing summary")
    elif len(summ) > max_len:
        problems.append("summary too long")
    if not isinstance(acts, list) or not all(isinstance(a, str) for a in acts) or len(acts) > 6:
        problems.append("bad recommended_actions")
    if not isinstance(cits, list) or any(c not in allowed_ids for c in cits):
        problems.append("citation not in retrieved passages")
    if isinstance(summ, str):
        for m in SEV.finditer(summ):
            if m.group(1).upper() != facts["severity"]:
                problems.append(f"summary contradicts severity ({m.group(1)})")
    if allowed_ids and isinstance(cits, list) and not cits:
        problems.append("no citations despite retrieved passages")
    return (None if problems else d), problems
