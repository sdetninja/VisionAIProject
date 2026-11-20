"""LLM panel for fire/smoke incidents: two Claude models judge the same claim from different views, in parallel.

  opus    Claude Opus 5.5   sees the hazard crop (the detector's box + margin)
  sonnet  Claude Sonnet 5.5 sees the full frame (context: is it a screen, a reflection, steam, a sunset?)

Each answers through structured outputs: {verdict: CONFIRMED|REJECTED|UNSURE, confidence, lookalike, reason}.
Any failure (no key, network, timeout, refusal, bad output) becomes that judge's UNSURE with an `error`: the panel can
never block or close an incident. Hazard frames are sent to the Anthropic API; PPE images never are.
"""
from __future__ import annotations

import base64
import json
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Protocol

LLM_VERDICTS = ("CONFIRMED", "REJECTED", "UNSURE")
SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": list(LLM_VERDICTS)},
        "confidence": {"type": "number"},
        "lookalike": {"anyOf": [{"type": "string"}, {"type": "null"}]},
        "reason": {"type": "string"},
    },
    "required": ["verdict", "confidence", "lookalike", "reason"],
    "additionalProperties": False,
}
JUDGES = {   # name: (model id, which image it sees)
    "opus": ("claude-opus-5-5", "crop"),
    "sonnet": ("claude-sonnet-5-5", "frame"),
}
PRICE_PER_MTOK = {"claude-opus-5-5": (4.0, 20.0), "claude-sonnet-5-5": (2.0, 10.0)}   # (input, output) USD

PROMPT = """You are checking an automatic fire/smoke alarm from a factory camera before a safety supervisor sees it.
The detector claims: {claim}. Camera {camera}, zone {zone}.
The image is {view}.

Judge ONLY what is visible in the image:
- CONFIRMED: you can see real flames or real smoke.
- REJECTED: there is clearly no fire or smoke, or what the detector saw is a lookalike (a screen or picture of fire,
  a reflection, a lamp or sunlight, steam or vapour from a known process, dust, fog). Name the lookalike.
- UNSURE: the image is too small, dark, blurred or ambiguous to tell.
A missed fire is far worse than a false alarm: when in doubt, answer UNSURE, never REJECTED.
Give a confidence from 0 to 1 and a one-sentence reason."""


class Panel(Protocol):
    def review(self, claim: dict, crop_jpeg: bytes | None, frame_jpeg: bytes | None) -> dict: ...


def unsure(error: str, model: str | None = None) -> dict:
    return {"verdict": "UNSURE", "confidence": 0.0, "lookalike": None, "reason": "", "error": error, "model": model}


def parse_answer(text: str) -> dict:
    d = json.loads(text)
    if d.get("verdict") not in LLM_VERDICTS:
        raise ValueError(f"bad verdict {d.get('verdict')!r}")
    return {"verdict": d["verdict"], "confidence": max(0.0, min(1.0, float(d.get("confidence") or 0))),
            "lookalike": d.get("lookalike") or None, "reason": str(d.get("reason") or "")[:500]}


class DisabledPanel:
    """No API key / not enabled: both judges UNSURE, so fire/smoke verdicts are UNSURE (a person decides)."""
    def __init__(self, why="LLM panel not enabled"):
        self.why = why

    def review(self, claim, crop_jpeg, frame_jpeg):
        return {name: unsure(self.why, model) for name, (model, _) in JUDGES.items()}


class AnthropicPanel:  # pragma: no cover - needs network and an API key
    def __init__(self, timeout_s=20.0, max_tokens=4000, effort="low"):
        import anthropic
        # Opus 5.5 always thinks (adaptively); low effort keeps a visual yes/no check short. Thinking tokens count
        # towards max_tokens, so leave headroom above the ~100-token answer.
        self.c = anthropic.Anthropic(timeout=timeout_s, max_retries=1)
        self.max_tokens, self.effort = max_tokens, effort

    def _ask(self, model, jpeg, claim, view):
        t0 = time.time()
        text = PROMPT.format(claim=claim["text"], camera=claim.get("camera_id"), zone=claim.get("zone"), view=view)
        r = self.c.beta.messages.create(
            model=model, max_tokens=self.max_tokens,
            output_config={"effort": self.effort, "format": {"type": "json_schema", "schema": SCHEMA}},
            betas=["server-side-fallback-2026-07-01"], fallbacks="default",   # a safety decline is re-run server-side
            messages=[{"role": "user", "content": [
                {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                             "data": base64.standard_b64encode(jpeg).decode()}},
                {"type": "text", "text": text}]}])
        if r.stop_reason in ("refusal", "max_tokens"):
            return unsure(f"stopped: {r.stop_reason}", model)
        out = next((b.text for b in r.content if b.type == "text"), None)   # content may start with a thinking block
        if out is None:
            return unsure("no text block", model)
        ans = parse_answer(out)
        pin, pout = PRICE_PER_MTOK.get(model, (0, 0))
        u = r.usage
        ans.update(model=r.model, input_tokens=u.input_tokens, output_tokens=u.output_tokens,
                   cost_usd=round((u.input_tokens * pin + u.output_tokens * pout) / 1e6, 5),
                   latency_s=round(time.time() - t0, 2))
        return ans

    def review(self, claim, crop_jpeg, frame_jpeg):
        views = {"crop": (crop_jpeg, "a close crop around the box where the detector saw fire/smoke"),
                 "frame": (frame_jpeg, "the full camera frame")}

        def one(item):
            name, (model, which) = item
            jpeg, view = views[which]
            if not jpeg:
                return name, unsure(f"no {which} image", model)
            try:
                return name, self._ask(model, jpeg, claim, view)
            except Exception as e:      # network, timeout, API error, invalid JSON: never block the incident
                return name, unsure(f"{type(e).__name__}: {str(e)[:200]}", model)

        with ThreadPoolExecutor(len(JUDGES)) as ex:
            return dict(ex.map(one, JUDGES.items()))


def make_panel(kind: str):
    import os
    if kind == "anthropic":
        if not os.environ.get("ANTHROPIC_API_KEY"):
            return DisabledPanel("ANTHROPIC_API_KEY not set")
        return AnthropicPanel()
    return DisabledPanel()
