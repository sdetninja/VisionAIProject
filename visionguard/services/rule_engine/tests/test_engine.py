import sys, os
sys.path.insert(0, os.path.dirname(__file__))
from helpers import cfg, frame, hand_box, head_box, person, vest_box

from rule_engine.engine import RuleEngine

HELMET = "      - {id: R-H, type: PPE_REQUIRED, item: helmet, cooldown_s: 3, clear_frames: 5}"
GLOVES = "      - {id: R-G, type: PPE_REQUIRED, item: gloves, cooldown_s: 3, clear_frames: 5}"


def run(eng, frames):
    out = []
    for f in frames:
        for ev in eng.process(f):
            out.append((f["frame_id"], ev))
    return out


def seq(n, start=1, **kw):
    """n frames, one person, optional PPE builders via kw: ppe=callable(person)->list"""
    ppe = kw.pop("ppe", lambda p: [])
    p_kw = kw.pop("p", {})
    res = []
    for i in range(start, start + n):
        p = person(**p_kw)
        res.append(frame(i, [p], ppe(p)))
    return res


# --- core behaviour ---------------------------------------------------------------------------
def test_missing_helmet_confirms_once_after_n_of_m():
    eng = RuleEngine(cfg(HELMET))
    evs = run(eng, seq(100))
    assert len(evs) == 1                                      # ONE event per episode, however long it lasts
    fid, e = evs[0]
    assert fid == 8                                           # n=8 usable observations
    assert e["event_type"] == "PPE_VIOLATION" and e["missing_ppe"] == ["helmet"] and e["track_id"] == "CAM_001-T001"
    assert e["first_seen_frame"] == 1 and e["confirmed_frame"] == 8
    assert e["first_seen_frame"] <= min(e["evidence_frames"]) and max(e["evidence_frames"]) == 8
    assert e["evidence_basis"]["inferred"] == 8 and e["needs_review"] is True      # inferred -> human review
    assert e["schema_version"] == "1.0" and e["zone"] == "Z1" and e["window"]["n"] == 8


def test_explicit_no_helmet_is_high_confidence_no_review():
    eng = RuleEngine(cfg(HELMET))
    evs = run(eng, seq(10, ppe=lambda p: [head_box(p, "no_helmet", 0.9)]))
    assert len(evs) == 1 and evs[0][1]["needs_review"] is False
    assert evs[0][1]["evidence_basis"] == {"explicit": 8, "inferred": 0} and evs[0][1]["confidence"] == 0.9


def test_helmet_present_no_event():
    eng = RuleEngine(cfg(HELMET))
    assert run(eng, seq(60, ppe=lambda p: [head_box(p)])) == []


def test_flicker_below_threshold_does_not_alert():
    eng = RuleEngine(cfg(HELMET))
    frames = []
    for i in range(1, 61):      # helmet missed in 6 of every 10 frames -> max 6 violating < 8
        p = person()
        frames.append(frame(i, [p], [] if (i % 10) < 6 else [head_box(p)]))
    assert run(eng, frames) == []


def test_conflicting_present_and_no_helmet_is_unknown_by_default():
    eng = RuleEngine(cfg(HELMET))
    assert run(eng, seq(30, ppe=lambda p: [head_box(p), head_box(p, "no_helmet")])) == []


# --- absence of evidence is not evidence of absence ----------------------------------------------
def test_gloves_not_inferred_from_missing_detection():
    eng = RuleEngine(cfg(GLOVES))
    assert run(eng, seq(200)) == []                           # gloves: inferred_absence defaults to False


def test_explicit_no_gloves_triggers():
    eng = RuleEngine(cfg(GLOVES))
    evs = run(eng, seq(12, ppe=lambda p: [hand_box(p, "no_gloves", 0.75)]))
    assert len(evs) == 1 and evs[0][1]["missing_ppe"] == ["gloves"]


def test_head_cut_off_by_frame_edge_is_unknown():
    eng = RuleEngine(cfg(HELMET))
    assert run(eng, seq(40, p={"bottom": 360, "h": 360})) == []       # top y == 0 -> head not visible


def test_small_distant_person_ignored():
    eng = RuleEngine(cfg(HELMET))
    assert run(eng, seq(40, p={"bottom": 200, "h": 60, "w": 30})) == []   # 60px < 15% of 720


def test_young_track_and_low_conf_person_ignored():
    eng = RuleEngine(cfg(HELMET))
    assert run(eng, seq(30, p={"age": 1})) == []
    eng = RuleEngine(cfg(HELMET))
    assert run(eng, seq(30, p={"conf": 0.3})) == []


# --- association --------------------------------------------------------------------------------
def test_helmet_assigned_to_correct_person():
    eng = RuleEngine(cfg(HELMET))
    frames = []
    for i in range(1, 15):
        a, b = person("CAM_001-T001", x=200), person("CAM_001-T002", x=700)
        frames.append(frame(i, [a, b], [head_box(a)]))       # only A wears a helmet
    evs = run(eng, frames)
    assert [e["track_id"] for _, e in evs] == ["CAM_001-T002"]


def test_one_detection_not_shared_between_overlapping_people():
    eng = RuleEngine(cfg(HELMET))
    frames = []
    for i in range(1, 15):
        a, b = person("CAM_001-T001", x=300), person("CAM_001-T002", x=330)    # heavy overlap
        frames.append(frame(i, [a, b], [head_box(b)]))       # helmet sits on B's head
    ids = {e["track_id"] for _, e in run(eng, frames)}
    assert ids == {"CAM_001-T001"}                           # A is flagged, B is not


# --- state machine: clear, cooldown, dedup ----------------------------------------------------------
def test_cooldown_suppresses_quick_reoffence_then_allows():
    eng = RuleEngine(cfg(HELMET))                             # cooldown 3 s = 30 frames, clear after 5 compliant
    frames = (seq(10)                                         # violating 1-10 -> event @8
              + seq(5, start=11, ppe=lambda p: [head_box(p)])  # compliant 11-15 -> cleared
              + seq(40, start=16))                            # violating again 16-55
    evs = run(eng, frames)
    ids = [f for f, _ in evs]
    assert ids[0] == 8 and len(ids) == 2
    assert 38 <= ids[1] <= 40                                 # only after cooldown (ts 0.8 + 3.0 s)


def test_track_id_switch_does_not_duplicate_incident():
    eng = RuleEngine(cfg(HELMET))
    f1 = seq(12)                                              # T001 confirmed @8
    f2 = [frame(i, [person("CAM_001-T002")]) for i in range(13, 40)]    # same spot, new ID
    evs = run(eng, f1 + f2)
    assert len(evs) == 1 and eng.stats()["suppressed_duplicates"] == 1


def test_stale_state_expires():
    eng = RuleEngine(cfg(HELMET, extra="track_expiry_s: 5"))
    run(eng, seq(5))
    assert eng.stats()["active_states"] == 1
    run(eng, [frame(i, []) for i in range(6, 120)])           # 11 s later, nobody seen
    assert eng.stats()["active_states"] == 0


# --- zones ----------------------------------------------------------------------------------------
def test_foot_point_decides_zone_membership():
    poly = "[[0,0],[1280,0],[1280,400],[0,400]]"              # upper part of the frame
    eng = RuleEngine(cfg(HELMET, polygon=poly))
    # box centre (y=270) is inside, feet (y=450) are outside -> NOT in zone
    assert run(eng, seq(30, p={"bottom": 450})) == []
    eng = RuleEngine(cfg(HELMET, polygon=poly))
    assert len(run(eng, seq(30, p={"bottom": 380, "h": 300}))) == 1       # feet inside -> violation


def test_normalized_polygon_scales_with_frame_size():
    import yaml
    from rule_engine.config import build_config
    doc = yaml.safe_load("""
zones:
  - zone_id: Z1
    camera_id: CAM_001
    coords: normalized
    polygon: [[0.0, 0.0], [0.5, 0.0], [0.5, 1.0], [0.0, 1.0]]
    rules:
      - {id: R-H, type: PPE_REQUIRED, item: helmet}
""")
    left, right = person(x=200), person(x=900)               # 1280 px wide frame: left half = x < 640
    eng = RuleEngine(build_config(doc))
    assert run(eng, [frame(i, [left]) for i in range(1, 12)])
    eng = RuleEngine(build_config(doc))
    assert run(eng, [frame(i, [right]) for i in range(1, 12)]) == []


def test_other_camera_not_evaluated():
    eng = RuleEngine(cfg(HELMET))
    frames = [frame(i, [person()], camera="CAM_002") for i in range(1, 30)]
    assert run(eng, frames) == []


def test_restricted_entry_and_leaving_in_time():
    rules = "      - {id: R-E, type: RESTRICTED_ENTRY, confirm: {n: 5, m: 6}, cooldown_s: 3, clear_frames: 3}"
    poly = "[[0,0],[640,0],[640,720],[0,720]]"               # left half is restricted
    eng = RuleEngine(cfg(rules, polygon=poly))
    inside = [frame(i, [person(x=200)]) for i in range(1, 5)]            # only 4 frames inside
    outside = [frame(i, [person(x=900)]) for i in range(5, 20)]
    assert run(eng, inside + outside) == []
    eng = RuleEngine(cfg(rules, polygon=poly))
    evs = run(eng, [frame(i, [person(x=200)]) for i in range(1, 10)])
    assert len(evs) == 1 and evs[0][1]["event_type"] == "RESTRICTED_AREA_ENTRY" and evs[0][1]["missing_ppe"] == []


def test_leaving_zone_clears_episode_so_reentry_after_cooldown_alerts_again():
    rules = "      - {id: R-E, type: RESTRICTED_ENTRY, confirm: {n: 3, m: 4}, cooldown_s: 1, clear_frames: 3}"
    poly = "[[0,0],[640,0],[640,720],[0,720]]"
    eng = RuleEngine(cfg(rules, polygon=poly))
    frames = ([frame(i, [person(x=200)]) for i in range(1, 6)]            # inside -> event
              + [frame(i, [person(x=900)]) for i in range(6, 12)]         # outside -> cleared
              + [frame(i, [person(x=200)]) for i in range(12, 20)])       # re-enter after cooldown
    assert len(run(eng, frames)) == 2


# --- hazards -------------------------------------------------------------------------------------
def test_hazard_fire_confirms_and_ignores_low_confidence():
    rules = "      - {id: R-F, type: HAZARD, hazard_classes: [fire], confirm: {n: 3, m: 5}, pos_conf: 0.55, cooldown_s: 10}"
    fire = lambda c: {"class": "fire", "confidence": c, "bbox": [500, 400, 600, 520]}      # noqa: E731
    eng = RuleEngine(cfg(rules))
    assert run(eng, [frame(i, hazards=[fire(0.4)]) for i in range(1, 20)]) == []        # below pos_conf
    eng = RuleEngine(cfg(rules))
    evs = run(eng, [frame(i, hazards=[fire(0.8)]) for i in range(1, 20)])
    assert len(evs) == 1 and evs[0][0] == 3
    e = evs[0][1]
    assert e["event_type"] == "HAZARD_DETECTED" and e["hazard_class"] == "fire" and e["track_id"] is None


# --- properties of the engine itself -------------------------------------------------------------------
def test_deterministic_replay_gives_identical_events():
    a = run(RuleEngine(cfg(HELMET + "\n" + GLOVES)), seq(30, ppe=lambda p: [hand_box(p, "no_gloves", 0.8)]))
    b = run(RuleEngine(cfg(HELMET + "\n" + GLOVES)), seq(30, ppe=lambda p: [hand_box(p, "no_gloves", 0.8)]))
    assert a == b and len(a) == 2 and len({e["event_id"] for _, e in a}) == 2


def test_hot_reload_drops_state_of_removed_rules():
    eng = RuleEngine(cfg(HELMET + "\n" + GLOVES))
    run(eng, seq(3))
    assert eng.stats()["active_states"] >= 1
    eng.reload(cfg(GLOVES))
    assert eng.stats()["active_states"] == 0


def test_accepts_pydantic_like_objects():
    class Fake:
        def __init__(self, d): self.d = d
        def model_dump(self, by_alias=False): return self.d
    eng = RuleEngine(cfg(HELMET))
    assert eng.process(Fake(frame(1, [person()]))) == []


def test_vest_rule_uses_torso_region():
    rules = "      - {id: R-V, type: PPE_REQUIRED, item: vest, cooldown_s: 3}"
    eng = RuleEngine(cfg(rules))
    assert run(eng, seq(30, ppe=lambda p: [vest_box(p)])) == []
    eng = RuleEngine(cfg(rules))
    assert len(run(eng, seq(30, ppe=lambda p: [vest_box(p, "no_vest")]))) == 1
