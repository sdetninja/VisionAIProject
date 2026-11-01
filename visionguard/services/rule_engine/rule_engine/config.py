"""Rule/zone configuration: YAML loader with strict validation (bad config must fail loudly at load time)."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml

from .geometry import point_in_polygon

PPE_REQUIRED = "PPE_REQUIRED"
RESTRICTED_ENTRY = "RESTRICTED_ENTRY"
HAZARD = "HAZARD"
RULE_TYPES = {PPE_REQUIRED, RESTRICTED_ENTRY, HAZARD}

# Where each PPE item is expected, as fractions of the PERSON box: (fx1, fy1, fx2, fy2). Tunable in YAML.
DEFAULT_REGIONS = {
    "helmet": (-0.15, -0.15, 1.15, 0.32),
    "goggles": (-0.10, -0.10, 1.10, 0.32),
    "mask": (-0.10, 0.00, 1.10, 0.38),
    "vest": (-0.10, 0.12, 1.10, 0.70),
    "gloves": (-0.35, 0.25, 1.35, 0.85),
}
# Absence can only be INFERRED (no explicit "no_x" detection) for items that are large and reliably detectable.
DEFAULT_INFERRED_ABSENCE = {"helmet": True, "vest": True, "gloves": False, "goggles": False, "mask": False}
TOP_EDGE_ITEMS = {"helmet", "goggles", "mask"}   # need the top of the person box to be inside the frame


class ConfigError(ValueError):
    pass


@dataclass
class Rule:
    id: str
    type: str
    item: Optional[str] = None                  # PPE_REQUIRED: helmet / vest / gloves / goggles / mask
    hazard_classes: tuple = ()                  # HAZARD: ("fire", "smoke")
    confirm_n: int = 8
    confirm_m: int = 10
    cooldown_s: float = 60.0
    clear_frames: int = 15                      # consecutive compliant observations that close a violation
    pos_conf: float = 0.40                      # min confidence for a PPE "present" detection
    neg_conf: float = 0.40                      # min confidence for an explicit "no_x" detection
    inferred_absence: bool = False
    on_conflict: str = "unknown"                # present AND no_x both detected: unknown | violating | compliant
    min_person_conf: float = 0.50
    min_person_height_frac: float = 0.15        # person box must be at least this fraction of frame height
    min_track_age: int = 3                      # ignore brand-new tracks (ID flicker)
    edge_margin_px: float = 3.0
    review_below: float = 0.70                  # events under this confidence are flagged needs_review
    description: str = ""


@dataclass
class Zone:
    zone_id: str
    camera_id: str = "*"
    polygon: Optional[list] = None              # None = whole frame
    normalized: bool = False
    rules: list = field(default_factory=list)

    def contains(self, pt, width: int, height: int) -> bool:
        if self.polygon is None:
            return True
        if self.normalized:
            if width <= 0 or height <= 0:
                return False
            pt = (pt[0] / width, pt[1] / height)
        return point_in_polygon(pt, self.polygon)

    def applies_to(self, camera_id: str) -> bool:
        return self.camera_id in ("*", camera_id)


@dataclass
class EngineConfig:
    zones: list
    regions: dict
    track_expiry_s: float = 15.0
    version: int = 1

    def zones_for(self, camera_id: str):
        return [z for z in self.zones if z.applies_to(camera_id)]

    def all_rules(self):
        return [(z.zone_id, r) for z in self.zones for r in z.rules]


def _confirm(d: dict, defaults: dict, where: str):
    c = {**defaults.get("confirm", {}), **d.get("confirm", {})}
    n, m = int(c.get("n", 8)), int(c.get("m", 10))
    if not (1 <= n <= m):
        raise ConfigError(f"{where}: confirm requires 1 <= n <= m (got n={n}, m={m})")
    return n, m


def _build_rule(raw: dict, defaults: dict, zone_id: str) -> Rule:
    where = f"zone {zone_id} rule {raw.get('id', '?')}"
    rtype = str(raw.get("type", "")).upper()
    if rtype not in RULE_TYPES:
        raise ConfigError(f"{where}: unknown type {raw.get('type')!r}; expected one of {sorted(RULE_TYPES)}")
    if "id" not in raw:
        raise ConfigError(f"{where}: missing id")
    n, m = _confirm(raw, defaults, where)
    g = lambda k, dv: raw.get(k, defaults.get(k, dv))   # noqa: E731
    item = raw.get("item", raw.get("required_item"))
    hz: tuple = ()
    if rtype == PPE_REQUIRED:
        if not item:
            raise ConfigError(f"{where}: PPE_REQUIRED needs `item` (e.g. helmet)")
        item = str(item).lower()
    if rtype == HAZARD:
        hz = tuple(str(x).lower() for x in raw.get("hazard_classes", ["fire", "smoke"]))
    conflict = str(g("on_conflict", "unknown"))
    if conflict not in ("unknown", "violating", "compliant"):
        raise ConfigError(f"{where}: on_conflict must be unknown|violating|compliant")
    rule = Rule(
        id=str(raw["id"]), type=rtype, item=item, hazard_classes=hz, confirm_n=n, confirm_m=m,
        cooldown_s=float(g("cooldown_s", 60)), clear_frames=int(g("clear_frames", 15)),
        pos_conf=float(g("pos_conf", 0.40)), neg_conf=float(g("neg_conf", 0.40)),
        inferred_absence=bool(raw.get("inferred_absence", DEFAULT_INFERRED_ABSENCE.get(item, False))),
        on_conflict=conflict, min_person_conf=float(g("min_person_conf", 0.50)),
        min_person_height_frac=float(g("min_person_height_frac", 0.15)),
        min_track_age=int(g("min_track_age", 3)), edge_margin_px=float(g("edge_margin_px", 3.0)),
        review_below=float(g("review_below", 0.70)), description=str(raw.get("description", "")))
    if rule.clear_frames < 1:
        raise ConfigError(f"{where}: clear_frames must be >= 1")
    return rule


def build_config(doc: dict) -> EngineConfig:
    if not isinstance(doc, dict) or "zones" not in doc or not doc["zones"]:
        raise ConfigError("config needs a non-empty `zones` list")
    defaults = doc.get("defaults", {}) or {}
    regions = dict(DEFAULT_REGIONS)
    for k, v in (doc.get("ppe_regions") or {}).items():
        if len(v) != 4:
            raise ConfigError(f"ppe_regions.{k} must have 4 numbers")
        regions[str(k).lower()] = tuple(float(x) for x in v)

    zones, seen_zone, seen_rule = [], set(), set()
    for z in doc["zones"]:
        zid = str(z.get("zone_id", "")).strip()
        if not zid:
            raise ConfigError("every zone needs a zone_id")
        cam = str(z.get("camera_id", "*"))
        if (zid, cam) in seen_zone:
            raise ConfigError(f"duplicate zone {zid!r} for camera {cam!r}")
        seen_zone.add((zid, cam))
        poly, normalized = None, False
        if z.get("polygon") is not None:
            poly = [tuple(float(c) for c in p) for p in z["polygon"]]
            if len(poly) < 3 or any(len(p) != 2 for p in poly):
                raise ConfigError(f"zone {zid}: polygon needs >= 3 [x, y] points")
            coords = str(z.get("coords", "pixel")).lower()
            if coords not in ("pixel", "normalized"):
                raise ConfigError(f"zone {zid}: coords must be pixel|normalized")
            normalized = coords == "normalized"
            if normalized and any(not (0 <= c <= 1) for p in poly for c in p):
                raise ConfigError(f"zone {zid}: normalized coordinates must be within 0..1")
            ref = z.get("reference_size")           # pixel polygon drawn on a reference resolution -> normalise
            if not normalized and ref:
                rw, rh = float(ref[0]), float(ref[1])
                poly = [(x / rw, y / rh) for x, y in poly]
                normalized = True
        rules = [_build_rule(r, defaults, zid) for r in (z.get("rules") or [])]
        for r in rules:
            if (zid, cam, r.id) in seen_rule:
                raise ConfigError(f"duplicate rule id {r.id!r} in zone {zid}")
            seen_rule.add((zid, cam, r.id))
        zones.append(Zone(zid, cam, poly, normalized, rules))
    return EngineConfig(zones=zones, regions=regions,
                        track_expiry_s=float(doc.get("track_expiry_s", 15)), version=int(doc.get("version", 1)))


def load_config(path) -> EngineConfig:
    return build_config(yaml.safe_load(Path(path).read_text()))
