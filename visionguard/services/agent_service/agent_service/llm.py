"""LLM adapters. The LLM only WRITES the explanation from facts we hand it. It cannot alter severity/score."""
from __future__ import annotations
import json
import os
from typing import Protocol


class LLM(Protocol):
    def explain(self, facts: dict, passages: list) -> str: ...   # returns JSON string


class TemplateLLM:
    """Deterministic fallback; also the default when no API key is configured."""
    def explain(self, facts, passages):
        inc = facts["incident"]
        what = {"PPE_VIOLATION": f"missing PPE ({', '.join(inc.get('missing_ppe') or ['unspecified'])})",
                "RESTRICTED_AREA_ENTRY": "entry into a restricted area",
                "HAZARD_DETECTED": f"possible {inc.get('hazard_class') or 'hazard'} detected"}.get(inc["event_type"], inc["event_type"])
        summary = (f"{facts['severity']} severity ({facts['score']}/100): {what} on {inc['camera_id']} in zone {inc['zone']}."
                   + (" Evidence is uncertain; human review is required." if inc.get("needs_review") else ""))
        actions = [f"Follow {p['heading']} ({p['source']})" for p in passages[:3]] or ["Have a supervisor verify the incident on site."]
        return json.dumps({"summary": summary, "recommended_actions": actions, "citations": [p["id"] for p in passages[:3]]})


class AnthropicLLM:  # pragma: no cover - needs network/API key
    def __init__(self, model="claude-sonnet-4-5", max_tokens=600):
        import anthropic
        self.c, self.model, self.max_tokens = anthropic.Anthropic(), model, max_tokens

    def explain(self, facts, passages):
        prompt = (
            "You assist a factory safety supervisor. Use ONLY the facts and passages below. Do not change the severity, "
            "do not invent rules, do not assign blame to the person. Reply with JSON only: "
            '{"summary": str, "recommended_actions": [str], "citations": [passage ids you used]}.\n'
            f"FACTS:\n{json.dumps(facts, default=str)}\nPASSAGES:\n{json.dumps(passages)}")
        r = self.c.messages.create(model=self.model, max_tokens=self.max_tokens, messages=[{"role": "user", "content": prompt}])
        return r.content[0].text


def make_llm(kind: str) -> LLM:
    if kind == "anthropic" and os.environ.get("ANTHROPIC_API_KEY"):
        return AnthropicLLM()
    return TemplateLLM()
