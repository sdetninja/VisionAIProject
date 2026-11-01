"""Static guards: an alert on a metric nobody emits never fires - the worst kind of monitoring bug."""
import json
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
DEPLOY = ROOT / "ops" / "deploy"


def emitted_metric_names():
    names = set()
    for p in list((ROOT / "ops" / "vg_obs").glob("*.py")) + list((ROOT / "services" / "vision_pipeline").rglob("metrics.py")):
        names |= set(re.findall(r'"(vg_[a-z0-9_]+)"', p.read_text()))
    return names


def used_in(expr):
    return set(re.findall(r"\b(vg_[a-z0-9_]+?)(?:_bucket|_sum|_count)?(?=[\s{\[)]|$)", expr))


def test_every_alert_and_panel_metric_exists():
    have = emitted_metric_names()
    exprs = [r["expr"] for g in yaml.safe_load((DEPLOY / "prometheus/alerts.yml").read_text())["groups"] for r in g["rules"]]
    exprs += [t["expr"] for p in json.loads((DEPLOY / "grafana/dashboards/overview.json").read_text())["panels"] for t in p["targets"]]
    missing = {m for e in exprs for m in used_in(e)} - have
    assert not missing, missing


def test_compose_publishes_only_gateway_and_tools_on_loopback():
    c = yaml.safe_load((DEPLOY / "docker-compose.yml").read_text())
    for name, s in c["services"].items():
        for port in s.get("ports", []):
            assert str(port).startswith("127.0.0.1:"), (name, port)
    assert "ports" not in c["services"]["incident-api"] and "ports" not in c["services"]["agent-worker"]


def test_app_containers_hardened():
    c = yaml.safe_load((DEPLOY / "docker-compose.yml").read_text())
    for n in ("gateway", "incident-api", "incident-worker", "agent-worker", "notification-worker", "rule-engine"):
        s = c["services"][n]
        assert s["read_only"] is True and s["cap_drop"] == ["ALL"] and "no-new-privileges:true" in s["security_opt"], n


def test_dockerfile_runs_non_root():
    t = (DEPLOY / "Dockerfile.service").read_text()
    assert re.search(r"^USER vg", t, re.M)


def test_no_real_secrets_committed():
    env = (DEPLOY / ".env.example").read_text()
    assert "change-me" in env and not re.search(r"(sk-|xox[bp]-|hooks\.slack\.com)", env)


def test_compose_worker_commands_match_bus_paths():
    c = yaml.safe_load((DEPLOY / "docker-compose.yml").read_text())["services"]
    inc = yaml.safe_load((ROOT / "services/incident_service/configs/incident.compose.yaml").read_text())
    assert inc["bus_path"] in c["agent-worker"]["command"] and inc["bus_path"] in c["notification-worker"]["command"]
    assert "/data/bus/rule_events.jsonl" in c["incident-worker"]["command"]
