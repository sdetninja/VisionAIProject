from rule_engine.config import ConfigError, build_config, load_config


def _bad(doc):
    try:
        build_config(doc)
    except ConfigError:
        return True
    return False


def test_example_config_loads():
    c = load_config("configs/rules.example.yaml")
    assert {z.zone_id for z in c.zones} == {"WELDING", "PRESS_RESTRICTED", "FLOOR_ALL"}
    w = [z for z in c.zones if z.zone_id == "WELDING"][0]
    assert w.normalized and all(0 <= v <= 1 for p in w.polygon for v in p)        # reference_size scaling applied
    assert w.rules[0].confirm_n == 8 and w.rules[0].inferred_absence is True      # helmet default
    assert w.rules[1].inferred_absence is False                                   # explicit override


def test_invalid_configs_fail_loudly():
    z = lambda rules, **k: {"zones": [{"zone_id": "A", "rules": rules, **k}]}      # noqa: E731
    assert _bad({})
    assert _bad(z([{"id": "r", "type": "NOPE"}]))
    assert _bad(z([{"id": "r", "type": "PPE_REQUIRED"}]))                          # missing item
    assert _bad(z([{"id": "r", "type": "PPE_REQUIRED", "item": "helmet", "confirm": {"n": 9, "m": 3}}]))
    assert _bad(z([{"id": "r", "type": "RESTRICTED_ENTRY"}, {"id": "r", "type": "RESTRICTED_ENTRY"}]))  # dup id
    assert _bad(z([], polygon=[[0, 0], [1, 1]]))                                   # < 3 points
    assert _bad(z([], polygon=[[0, 0], [2, 0], [2, 2]], coords="normalized"))      # outside 0..1
    assert not _bad(z([{"id": "r", "type": "PPE_REQUIRED", "item": "helmet"}]))
