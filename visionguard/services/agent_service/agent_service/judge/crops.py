"""Cut the crops the judges look at, from an UNANNOTATED evidence frame and the event's box.

The event box is the rule engine's subject: the person box for PPE rules, the fire/smoke box for R-HAZARD. Crops follow
the training crops of milestone0/scripts/10_build_judge_crops.py as closely as a run-time box allows:
  helmet  head region: a square at the top of the person box (training: labelled head boxes), padded 15 %
  vest    the person box, padded 5 %
  hazard  the fire/smoke box, padded 15 %

Head square = min(0.6 x person width, 0.3 x person height), chosen on 1,141 person boxes of the Milestone 0 test split
(2026-11-16): helmet AUC 0.987 vs 0.975 for min(width, 0.3 x height) padded 25 % (head + shoulders in waist-up
framing); 99.2 % of real no-helmet claims kept, 87 % of false ones rejected, 5 % unsure. On labelled head boxes the
ensemble reaches 0.999: run-time crops are still the weaker link (ARCHITECTURE_V3_ASSESSMENT.md, known limits).
"""
from __future__ import annotations

from typing import Optional

from PIL import Image

CHECK_OF_ITEM = {"helmet": "helmet", "vest": "vest"}       # PPE item -> judge check; other items have no judge yet
HEAD_SHARE, HEAD_WIDTH, HEAD_PAD = 0.3, 0.6, 0.15     # head square side = min(0.6 width, 0.3 height), padded 15 %
MIN_SIDE = 16             # smaller crops are not judged (too little to see)


def _pad(b, f, W, H):
    x1, y1, x2, y2 = b
    dw, dh = (x2 - x1) * f, (y2 - y1) * f
    return max(0, x1 - dw), max(0, y1 - dh), min(W, x2 + dw), min(H, y2 + dh)


def head_box(person):
    x1, y1, x2, y2 = person
    w, h = x2 - x1, y2 - y1
    s = min(HEAD_WIDTH * w, HEAD_SHARE * h)
    cx = (x1 + x2) / 2
    return cx - s / 2, y1, cx + s / 2, y1 + s


def box_for(check: str, bbox, W: int, H: int):
    if check == "helmet":
        return _pad(head_box(bbox), HEAD_PAD, W, H)
    if check == "vest":
        return _pad(bbox, 0.05, W, H)
    if check == "hazard":
        return _pad(bbox, 0.15, W, H)
    raise ValueError(check)


def crop(img: Image.Image, check: str, bbox) -> Optional[Image.Image]:
    """Crop for one check, or None when the box is missing or too small."""
    if not bbox or len(bbox) != 4:
        return None
    x1, y1, x2, y2 = box_for(check, [float(v) for v in bbox], *img.size)
    if min(x2 - x1, y2 - y1) < MIN_SIDE:
        return None
    return img.crop((int(x1), int(y1), int(x2), int(y2))).convert("RGB")


def checks_for(event: dict) -> list:
    """Judge checks for one event. PPE: one per judged item; hazard: one; anything else: none."""
    if event.get("event_type") == "HAZARD_DETECTED":
        return ["hazard"]
    if event.get("event_type") == "PPE_VIOLATION":
        return [CHECK_OF_ITEM[i] for i in event.get("missing_ppe") or [] if i in CHECK_OF_ITEM]
    return []
