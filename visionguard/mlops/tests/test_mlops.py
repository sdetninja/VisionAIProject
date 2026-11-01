import json
import random
import pytest

from vg_mlops import drift as D, feedback as F
from vg_mlops.cli import main as cli_main
from vg_mlops.datasets import dataset_summary, leakage_report
from vg_mlops.gates import GateConfig, evaluate_candidate
from vg_mlops.promote import to_production, to_staging
from vg_mlops.registry import Registry, RegistryError, load_pointer

TARGETS = {"helmet": {"recall": 0.85, "map50": 0.80}, "vest": {"recall": 0.80, "map50": 0.75}}
CFG = GateConfig(targets=TARGETS, min_staging_hours=24)


def M(helmet=0.90, vest=0.85, n=100, lat=40, map50=0.85):
    return {"per_class": {"helmet": {"recall": helmet, "precision": .9, "map50": map50, "n": n},
                          "vest": {"recall": vest, "precision": .9, "map50": map50, "n": n}}, "latency_ms": lat}


class Clock:
    def __init__(self): self.t = 1_000_000.0
    def __call__(self): return self.t


def make_reg(tmp_path):
    clk = Clock()
    reg = Registry(tmp_path / "reg", clock=clk)
    reg.freeze_test("ppe", "T1", "alice")
    return reg, clk


def add(reg, tmp_path, metrics, test_hash="T1", content=b"w"):
    w = tmp_path / "w.pt"; w.write_bytes(content + str(random.random()).encode())
    return reg.register("ppe", w, metrics, "D1", test_hash, "ci")


def ship(reg, clk, tmp_path, metrics):
    v = add(reg, tmp_path, metrics)
    assert to_staging(reg, "ppe", v, CFG).passed
    clk.t += 25 * 3600
    to_production(reg, "ppe", v, CFG, "bob")
    return v


# ---------- datasets
def mk(root, split, name, data):
    (root / "images" / split).mkdir(parents=True, exist_ok=True)
    (root / "labels" / split).mkdir(parents=True, exist_ok=True)
    (root / "images" / split / name).write_bytes(data)
    (root / "labels" / split / (name.rsplit(".", 1)[0] + ".txt")).write_text("0 .5 .5 .1 .1")


def test_hash_changes_when_label_changes(tmp_path):
    mk(tmp_path, "train", "a.jpg", b"A"); mk(tmp_path, "val", "b.jpg", b"B"); mk(tmp_path, "test", "c.jpg", b"C")
    h1 = dataset_summary(tmp_path)["hashes"]["test"]
    (tmp_path / "labels/test/c.txt").write_text("1 .5 .5 .1 .1")
    assert dataset_summary(tmp_path)["hashes"]["test"] != h1


def test_leakage_detected(tmp_path):
    mk(tmp_path, "train", "a.jpg", b"SAME"); mk(tmp_path, "test", "z.jpg", b"SAME")
    assert not leakage_report(dataset_summary(tmp_path)["manifests"])["ok"]


def test_synthetic_in_test_flagged(tmp_path):
    mk(tmp_path, "train", "a.jpg", b"A"); mk(tmp_path, "test", "synth_1.jpg", b"S")
    assert leakage_report(dataset_summary(tmp_path)["manifests"])["synthetic_outside_train"]


def test_clean_dataset_ok(tmp_path):
    mk(tmp_path, "train", "a.jpg", b"A"); mk(tmp_path, "val", "b.jpg", b"B"); mk(tmp_path, "test", "c.jpg", b"C")
    assert leakage_report(dataset_summary(tmp_path)["manifests"])["ok"]


# ---------- gates
def test_gates_pass_first_model():
    assert evaluate_candidate(M(), None, CFG, "T1", "T1").passed


def test_gate_fails_on_different_test_set():
    r = evaluate_candidate(M(), None, CFG, "T2", "T1")
    assert not r.passed and r.failures()[0][0] == "frozen_test_set"


def test_gate_fails_when_no_frozen_test():
    assert not evaluate_candidate(M(), None, CFG, "T1", None).passed


def test_gate_below_target():
    assert not evaluate_candidate(M(helmet=0.70), None, CFG, "T1", "T1").passed


def test_gate_too_few_instances():
    assert not evaluate_candidate(M(n=5), None, CFG, "T1", "T1").passed


def test_gate_critical_regression_tighter():
    prod = M(helmet=0.95)
    assert not evaluate_candidate(M(helmet=0.93), prod, CFG, "T1", "T1").passed     # -0.02 > 0.01 for critical class
    prod2 = M(vest=0.95)
    assert evaluate_candidate(M(vest=0.93), prod2, CFG, "T1", "T1").passed          # -0.02 <= 0.03 non-critical


def test_gate_latency_budget():
    assert not evaluate_candidate(M(lat=120), None, CFG, "T1", "T1").passed


def test_gate_missing_class():
    m = M(); del m["per_class"]["vest"]
    assert not evaluate_candidate(m, None, CFG, "T1", "T1").passed


# ---------- registry / promotion
def test_versions_and_stage_order(tmp_path):
    reg, _ = make_reg(tmp_path)
    v1, v2 = add(reg, tmp_path, M()), add(reg, tmp_path, M())
    assert (v1, v2) == (1, 2)
    with pytest.raises(RegistryError):
        reg.set_stage("ppe", v1, "production", "x")           # skipping staging


def test_full_promotion_and_pointer(tmp_path):
    reg, clk = make_reg(tmp_path)
    v = ship(reg, clk, tmp_path, M())
    assert reg.current("ppe")["version"] == v
    assert load_pointer(reg.pointer_path("ppe"))["version"] == v


def test_soak_enforced_and_force_needs_reason(tmp_path):
    reg, clk = make_reg(tmp_path)
    v = add(reg, tmp_path, M()); to_staging(reg, "ppe", v, CFG)
    with pytest.raises(RegistryError):
        to_production(reg, "ppe", v, CFG, "bob")
    with pytest.raises(RegistryError):
        to_production(reg, "ppe", v, CFG, "bob", force=True)             # no reason
    to_production(reg, "ppe", v, CFG, "bob", force=True, reason="urgent hotfix, INC-42")
    assert any(e["action"] == "soak_override" for e in reg.events("ppe"))


def test_force_never_overrides_failed_gates(tmp_path):
    reg, clk = make_reg(tmp_path)
    v = add(reg, tmp_path, M()); to_staging(reg, "ppe", v, CFG)
    reg.db.execute("UPDATE models SET metrics=? WHERE version=?", (json.dumps(M(helmet=0.5)), v))
    with pytest.raises(RegistryError):
        to_production(reg, "ppe", v, CFG, "bob", force=True, reason="please")


def test_failed_gate_stays_candidate_and_is_logged(tmp_path):
    reg, _ = make_reg(tmp_path)
    v = add(reg, tmp_path, M(helmet=0.5))
    assert not to_staging(reg, "ppe", v, CFG).passed
    assert reg.get("ppe", v)["stage"] == "candidate"
    assert any(e["action"] == "gate_failed" for e in reg.events("ppe"))


def test_approver_required(tmp_path):
    reg, clk = make_reg(tmp_path)
    v = add(reg, tmp_path, M()); to_staging(reg, "ppe", v, CFG); clk.t += 99999
    with pytest.raises(RegistryError):
        to_production(reg, "ppe", v, CFG, "")


def test_new_production_archives_old_and_rollback(tmp_path):
    reg, clk = make_reg(tmp_path)
    v1 = ship(reg, clk, tmp_path, M())
    v2 = ship(reg, clk, tmp_path, M(helmet=0.91))
    assert reg.get("ppe", v1)["stage"] == "archived"
    assert reg.rollback("ppe", "bob", "regression in cam3") == v1
    assert reg.current("ppe")["version"] == v1
    assert load_pointer(reg.pointer_path("ppe"))["version"] == v1
    assert reg.get("ppe", v2)["stage"] == "archived"
    with pytest.raises(RegistryError):
        reg.set_stage("ppe", v2, "staging", "x")                         # archived is terminal


def test_rollback_needs_reason_and_history(tmp_path):
    reg, clk = make_reg(tmp_path)
    ship(reg, clk, tmp_path, M())
    with pytest.raises(RegistryError):
        reg.rollback("ppe", "bob", "")
    with pytest.raises(RegistryError):
        reg.rollback("ppe", "bob", "why")                                # nothing earlier


def test_tampered_weights_rejected(tmp_path):
    reg, clk = make_reg(tmp_path)
    v = ship(reg, clk, tmp_path, M())
    p = reg.get("ppe", v)["weights_path"]
    open(p, "ab").write(b"x")
    with pytest.raises(RegistryError):
        load_pointer(reg.pointer_path("ppe"))


def test_rebase_requires_reason(tmp_path):
    reg, _ = make_reg(tmp_path)
    with pytest.raises(RegistryError):
        reg.rebase_test("ppe", "T2", "alice", "")
    reg.rebase_test("ppe", "T2", "alice", "added own-camera test images")
    assert reg.frozen_hash("ppe") == "T2"


def test_old_candidate_blocked_after_rebase(tmp_path):
    reg, _ = make_reg(tmp_path)
    v = add(reg, tmp_path, M())
    reg.rebase_test("ppe", "T2", "alice", "new test set")
    assert not to_staging(reg, "ppe", v, CFG).passed


# ---------- feedback
def lab(i, label, rule="R1", etype="PPE_VIOLATION", uris=("e/1.jpg",)):
    return {"incident_id": f"i{i}", "label": label, "rule_id": rule, "reviewer": "r",
            "evidence_uris": list(uris), "event_payload": {"event_type": etype, "camera_id": "c1", "hazard_class": "fire"}}


def test_ppe_fp_goes_to_relabel_not_hard_negatives():
    q = F.build_queues([lab(1, "false_positive")])
    assert len(q["relabel"]) == 1 and not q["hard_negatives"]


def test_hazard_fp_is_hard_negative():
    q = F.build_queues([lab(1, "false_positive", etype="HAZARD_DETECTED")])
    assert len(q["hard_negatives"]) == 1 and not q["relabel"]


def test_confirmed_queue_and_dedupe_and_no_evidence():
    q = F.build_queues([lab(1, "true_positive"), lab(1, "true_positive"), lab(2, "true_positive", uris=())])
    assert len(q["confirmed"]) == 1


def test_rule_stats_and_suggestions():
    ls = [lab(i, "true_positive") for i in range(5)] + [lab(10 + i, "false_positive") for i in range(25)]
    st = F.rule_stats(ls)
    assert st["R1"]["n"] == 30 and st["R1"]["precision"] == round(5 / 30, 3)
    assert F.suggest_threshold_changes(st)
    assert not F.suggest_threshold_changes(F.rule_stats(ls[:5]))          # too few samples to suggest


# ---------- drift
def test_drift_ok_vs_alert():
    rnd = random.Random(1)
    base = {"confidences": {"helmet": [min(.99, max(.01, rnd.gauss(.8, .08))) for _ in range(500)]}, "counts_per_frame": {"person": 2.0}}
    same = {"confidences": {"helmet": [min(.99, max(.01, rnd.gauss(.8, .08))) for _ in range(500)]}, "counts_per_frame": {"person": 2.1}}
    shifted = {"confidences": {"helmet": [min(.99, max(.01, rnd.gauss(.5, .1))) for _ in range(500)]}, "counts_per_frame": {"person": 2.0}}
    assert not D.drift_report(base, same)["needs_attention"]
    assert D.drift_report(base, shifted)["needs_attention"]


def test_drift_insufficient_data_and_count_change():
    base = {"confidences": {"helmet": [.8] * 500}, "counts_per_frame": {"person": 2.0}}
    r = D.drift_report(base, {"confidences": {"helmet": [.1] * 10}, "counts_per_frame": {"person": 5.0}})
    assert r["classes"]["helmet"]["status"] == "insufficient_data" and r["needs_attention"]


# ---------- cli
def test_cli_end_to_end(tmp_path, capsys):
    data = tmp_path / "d"
    mk(data, "train", "a.jpg", b"A"); mk(data, "val", "b.jpg", b"B"); mk(data, "test", "c.jpg", b"C")
    w = tmp_path / "best.pt"; w.write_bytes(b"weights")
    (tmp_path / "m.json").write_text(json.dumps(M()))
    (tmp_path / "t.yaml").write_text("min_test_instances_per_class: 30\nppe:\n  helmet: {recall: 0.85, map50: 0.8}\n  vest: {recall: 0.8, map50: 0.75}\n")
    r = ["--registry", str(tmp_path / "reg")]
    assert cli_main(["audit-data", "--data-root", str(data), "--out", str(tmp_path / "s.json")]) == 0
    assert cli_main(r + ["freeze-test", "--name", "ppe", "--data-root", str(data), "--actor", "a"]) == 0
    assert cli_main(r + ["register", "--name", "ppe", "--weights", str(w), "--metrics", str(tmp_path / "m.json"), "--data-root", str(data)]) == 0
    assert cli_main(r + ["stage", "--name", "ppe", "--version", "1", "--targets", str(tmp_path / "t.yaml"), "--group", "ppe"]) == 0
    # soak not elapsed -> refused with exit 1, not a traceback
    assert cli_main(r + ["promote", "--name", "ppe", "--version", "1", "--targets", str(tmp_path / "t.yaml"), "--group", "ppe", "--approver", "bob"]) == 1
    assert cli_main(r + ["promote", "--name", "ppe", "--version", "1", "--targets", str(tmp_path / "t.yaml"), "--group", "ppe",
                         "--approver", "bob", "--force", "--reason", "test"]) == 0
    assert cli_main(r + ["status", "--name", "ppe"]) == 0


def test_cli_register_refuses_leaky_data(tmp_path):
    data = tmp_path / "d"
    mk(data, "train", "a.jpg", b"SAME"); mk(data, "test", "c.jpg", b"SAME")
    w = tmp_path / "best.pt"; w.write_bytes(b"w"); (tmp_path / "m.json").write_text(json.dumps(M()))
    assert cli_main(["--registry", str(tmp_path / "reg"), "register", "--name", "p", "--weights", str(w), "--metrics", str(tmp_path / "m.json"), "--data-root", str(data)]) == 2
