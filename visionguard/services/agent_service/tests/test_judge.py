import json

import pytest
from PIL import Image

from agent_service.judge import band, combine_items, hazard_verdict
from agent_service.judge.crops import box_for, checks_for, crop, head_box
from agent_service.judge.panel import SCHEMA, DisabledPanel, parse_answer
from agent_service.judge.vit import claim_prob

CLASSES = ["fire", "helmet", "no_helmet", "no_vest", "none", "smoke", "vest"]


def test_claim_prob_renormalises_over_the_check():
    row = [0.1, 0.2, 0.6, 0.0, 0.05, 0.05, 0.0]
    assert claim_prob([row], CLASSES, "helmet")[0] == pytest.approx(0.6 / 0.8)
    assert claim_prob([row], CLASSES, "hazard")[0] == pytest.approx(0.15 / 0.2)
    assert claim_prob([[0] * 7], CLASSES, "vest")[0] == 0.5          # no mass on the check's classes


def test_band_and_combine():
    t = {"reject_below": 0.3, "confirm_above": 0.7}
    assert [band(s, t) for s in (0.1, 0.3, 0.69, 0.7)] == ["LIKELY_FALSE", "UNSURE", "UNSURE", "CONFIRMED"]
    assert combine_items({}) == "NO_JUDGE"
    assert combine_items({"helmet": "CONFIRMED", "vest": "LIKELY_FALSE"}) == "CONFIRMED"
    assert combine_items({"helmet": "LIKELY_FALSE", "gloves": "NO_JUDGE"}) == "UNSURE"
    assert hazard_verdict(None, {}) == "UNSURE"
    assert hazard_verdict(None, {"a": {"verdict": "REJECTED"}, "b": {"verdict": "REJECTED"}}) == "UNSURE"   # needs the ViT too


def test_crops():
    img = Image.new("RGB", (640, 480))
    assert head_box((100, 100, 200, 400)) == (120.0, 100, 180.0, 160.0)   # square side min(0.6 * 100, 0.3 * 300) = 60
    assert head_box((0, 0, 300, 300)) == (105.0, 0, 195.0, 90.0)           # waist-up framing: 0.3 * height wins
    assert crop(img, "vest", None) is None and crop(img, "vest", [0, 0, 5, 5]) is None
    assert crop(img, "hazard", [600, 400, 700, 500]).size == (55, 95)        # padded 15 %, clipped to the frame
    assert box_for("vest", [0, 0, 100, 200], 640, 480) == (0, 0, 105.0, 210.0)
    assert checks_for({"event_type": "PPE_VIOLATION", "missing_ppe": ["helmet", "gloves", "vest"]}) == ["helmet", "vest"]
    assert checks_for({"event_type": "HAZARD_DETECTED"}) == ["hazard"]
    assert checks_for({"event_type": "RESTRICTED_AREA_ENTRY"}) == []


def test_panel_schema_and_parsing():
    assert SCHEMA["additionalProperties"] is False and set(SCHEMA["required"]) == set(SCHEMA["properties"])
    a = parse_answer(json.dumps({"verdict": "REJECTED", "confidence": 3, "lookalike": "monitor", "reason": "a screen"}))
    assert a == {"verdict": "REJECTED", "confidence": 1.0, "lookalike": "monitor", "reason": "a screen"}
    with pytest.raises(ValueError):
        parse_answer(json.dumps({"verdict": "MAYBE"}))
    d = DisabledPanel("no key").review({}, b"x", b"y")
    assert set(d) == {"opus", "sonnet"} and all(v["verdict"] == "UNSURE" and v["error"] == "no key" for v in d.values())
