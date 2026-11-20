"""The hybrid judge (ARCHITECTURE_V3_ASSESSMENT.md, section 3).

  PPE (helmet, vest)        ViT ensemble only, local
  fire / smoke              ViT ensemble + LLM panel (Claude Opus 5.5 on the crop, Claude Sonnet 5.5 on the full frame)
  gloves, goggles, mask     NO_JUDGE (no trained check yet)
  restricted area           NOT_APPLICABLE (geometry, not appearance)

The judge is advisory: it returns a verdict and its evidence; it never closes, hides or down-ranks fire/smoke.
"""
from __future__ import annotations

import io
import logging
from typing import Callable, Optional

from PIL import Image

from .crops import CHECK_OF_ITEM, checks_for, crop
from .panel import DisabledPanel

log = logging.getLogger("assessment.judge")
CONFIRMED, LIKELY_FALSE, UNSURE, NO_JUDGE, NOT_APPLICABLE = "CONFIRMED", "LIKELY_FALSE", "UNSURE", "NO_JUDGE", "NOT_APPLICABLE"


def band(score: float, t: dict) -> str:
    """Threshold policy chosen on val in milestone0/scripts/12_eval_vit_judges.py."""
    if score >= t["confirm_above"]:
        return CONFIRMED
    if score < t["reject_below"]:
        return LIKELY_FALSE
    return UNSURE


def combine_items(item_verdicts: dict) -> str:
    """Grouped PPE incident: CONFIRMED if any item is; LIKELY_FALSE only if EVERY item is; NO_JUDGE if none judged."""
    v = list(item_verdicts.values())
    if not v or all(x == NO_JUDGE for x in v):
        return NO_JUDGE
    if CONFIRMED in v:
        return CONFIRMED
    if all(x == LIKELY_FALSE for x in v):
        return LIKELY_FALSE
    return UNSURE


def hazard_verdict(vit_band: Optional[str], llm: dict) -> str:
    """Fire/smoke table: both LLMs CONFIRMED -> CONFIRMED; ViT below reject_below AND both LLMs REJECTED -> LIKELY_FALSE
    (flagged only, never down-ranked); anything else (disagreement, UNSURE, error, no ViT) -> UNSURE."""
    votes = [j.get("verdict") for j in llm.values()]
    if votes and all(v == "CONFIRMED" for v in votes):
        return CONFIRMED
    if vit_band == LIKELY_FALSE and votes and all(v == "REJECTED" for v in votes):
        return LIKELY_FALSE
    return UNSURE


def _jpeg(img: Image.Image, q=90) -> bytes:
    b = io.BytesIO()
    img.save(b, "JPEG", quality=q)
    return b.getvalue()


class Judge:
    def __init__(self, scorer=None, panel=None):
        self.scorer, self.panel = scorer, panel or DisabledPanel()

    def versions(self) -> dict:
        from .panel import JUDGES
        return {"vit": dict(self.scorer.versions) if self.scorer else None,
                "llm_panel": {n: m for n, (m, _) in JUDGES.items()} if not isinstance(self.panel, DisabledPanel) else None}

    def judge(self, inc: dict, load: Callable[[str], bytes]) -> dict:
        """inc: M3 incident detail (with `events` and `raw_evidence`); load(uri) -> JPEG bytes."""
        et = inc.get("event_type")
        if et == "RESTRICTED_AREA_ENTRY":
            return {"verdict": NOT_APPLICABLE, "judge_mode": "none", "vit": None, "llm": None,
                    "reason": "restricted-area entry is geometry, not appearance"}
        crops, frames = self._crops(inc, load)
        if et == "HAZARD_DETECTED":
            return self._hazard(inc, crops.get("hazard", []), frames)
        if et == "PPE_VIOLATION":
            return self._ppe(inc, crops)
        return {"verdict": NO_JUDGE, "judge_mode": "none", "vit": None, "llm": None, "reason": f"no judge for {et}"}

    # ---------------------------------------------------------------- crops
    def _crops(self, inc, load):
        """{check: [(frame_id, PIL crop)]} and [(frame_id, PIL frame)] from the unannotated evidence frames."""
        events = {e["event_id"]: e for e in inc.get("events") or []}
        out, frames = {}, []
        for item in inc.get("raw_evidence") or []:
            ev = events.get(item.get("event_id"))
            if not ev:
                continue
            try:
                img = Image.open(io.BytesIO(load(item["uri"]))).convert("RGB")
            except Exception as e:
                log.warning("cannot load %s: %s", item.get("uri"), e)
                continue
            frames.append((item.get("frame_id", 0), img, ev))
            for check in checks_for(ev):
                c = crop(img, check, ev.get("bbox"))
                if c is not None:
                    out.setdefault(check, []).append((item.get("frame_id", 0), c))
        return out, frames

    def _vit(self, check, crops):
        s = self.scorer.score(check, [c for _, c in crops])
        n = len(s["ensemble"])
        t = self.scorer.thresholds[check]
        mean = round(sum(s["ensemble"]) / n, 4)
        return {"frames": n, "ensemble": mean, "models": {m: round(sum(v) / n, 4) for m, v in s["models"].items()},
                "per_frame": s["ensemble"], "reject_below": t["reject_below"], "confirm_above": t["confirm_above"],
                "verdict": band(mean, t)}

    # ---------------------------------------------------------------- PPE
    def _ppe(self, inc, crops):
        items = sorted(set(inc.get("missing_ppe") or []))
        vit, item_verdicts, notes = {}, {}, []
        for item in items:
            check = CHECK_OF_ITEM.get(item)
            if check is None:
                item_verdicts[item] = NO_JUDGE
                notes.append(f"{item}: no trained check")
            elif self.scorer is None:
                item_verdicts[item] = NO_JUDGE
                notes.append(f"{item}: ViT models not loaded")
            elif not crops.get(check):
                item_verdicts[item] = NO_JUDGE
                notes.append(f"{item}: no usable evidence frame")
            else:
                vit[item] = self._vit(check, crops[check])
                item_verdicts[item] = vit[item]["verdict"]
                notes.append(f"{item}: P(missing) {vit[item]['ensemble']:.2f} -> {item_verdicts[item]}")
        verdict = combine_items(item_verdicts)
        return {"verdict": verdict, "judge_mode": "vit" if vit else "none", "vit": vit or None, "llm": None,
                "items": item_verdicts, "reason": "; ".join(notes)}

    # ---------------------------------------------------------------- fire / smoke
    def _hazard(self, inc, crops, frames):
        vit = self._vit("hazard", crops) if (self.scorer is not None and crops) else None
        claim = {"text": f"{inc.get('hazard_class') or 'fire/smoke'} detected", "camera_id": inc.get("camera_id"),
                 "zone": inc.get("zone")}
        crop_jpeg = _jpeg(max(crops, key=lambda x: x[0])[1]) if crops else None        # latest frame's crop
        frame_jpeg = _jpeg(max(frames, key=lambda x: x[0])[1]) if frames else None
        llm = self.panel.review(claim, crop_jpeg, frame_jpeg)
        verdict = hazard_verdict(vit["verdict"] if vit else None, llm)
        parts = [f"ViT P(fire/smoke) {vit['ensemble']:.2f} -> {vit['verdict']}" if vit else "ViT: no score"]
        parts += [f"{n}: {j['verdict']}" + (f" ({j['error']})" if j.get("error") else "") for n, j in llm.items()]
        return {"verdict": verdict, "judge_mode": "vit+llm", "vit": {"hazard": vit} if vit else None, "llm": llm,
                "reason": "; ".join(parts)}
