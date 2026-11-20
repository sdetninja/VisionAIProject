"""The ViT ensemble: three vision transformers trained in Milestone 0 (scripts 10-12), probabilities averaged.

Each model has one 7-class head (fire, helmet, no_helmet, no_vest, none, smoke, vest). A check compares only its own
classes, renormalised, exactly as milestone0/scripts/12_eval_vit_judges.py does when it picks the thresholds:
  helmet  P(no_helmet) over {helmet, no_helmet}     claim "this person has no helmet"
  vest    P(no_vest)   over {vest, no_vest}         claim "this person has no vest"
  hazard  P(fire)+P(smoke) over {fire, smoke, none} claim "there is fire/smoke here"
The ensemble averages the three models' 7-class probabilities first, then renormalises (as in the evaluation).
"""
from __future__ import annotations

from pathlib import Path
from typing import Protocol

import yaml

CHECKS = {"helmet": (["no_helmet"], ["helmet"]), "vest": (["no_vest"], ["vest"]), "hazard": (["fire", "smoke"], ["none"])}


class Scorer(Protocol):
    thresholds: dict
    versions: dict

    def score(self, check: str, crops: list) -> dict: ...   # {"models": {name: [p, ...]}, "ensemble": [p, ...]}


def claim_prob(probs, classes, check):
    """probs: rows of 7-class probabilities (lists or a tensor) -> claim probability per row."""
    pos, neg = CHECKS[check]
    pi, ni = [classes.index(c) for c in pos], [classes.index(c) for c in neg]
    out = []
    for row in probs:
        row = [float(v) for v in row]
        p, n = sum(row[i] for i in pi), sum(row[i] for i in ni)
        out.append(p / (p + n) if p + n > 0 else 0.5)
    return out


class VitEnsemble:
    """Loads the checkpoints listed in thresholds.yaml (written by 12_eval_vit_judges.py)."""

    def __init__(self, thresholds_path: str, device: str | None = None):
        import torch
        import timm
        from torchvision import transforms
        cfg = yaml.safe_load(open(thresholds_path))
        base = Path(thresholds_path).resolve().parent
        self.thresholds = cfg["thresholds"]
        self.device = device or ("mps" if torch.backends.mps.is_available() else "cuda" if torch.cuda.is_available() else "cpu")
        self.models, self.versions, self.classes, self._torch = {}, {}, None, torch
        for name, path in cfg["models"].items():
            p = Path(path)
            if not p.is_absolute():
                p = base / p
            ck = torch.load(p, map_location="cpu", weights_only=False)
            m = timm.create_model(ck["timm_name"], pretrained=False, num_classes=len(ck["classes"]), **ck["timm_kwargs"])
            m.load_state_dict(ck["state_dict"])
            tf = transforms.Compose([transforms.Resize((ck["img_size"],) * 2), transforms.ToTensor(),
                                     transforms.Normalize(ck["mean"], ck["std"])])
            self.models[name] = (m.to(self.device).eval(), tf)
            self.versions[name] = f"{ck['timm_name']}@epoch{ck['epoch']}"
            if self.classes is None:
                self.classes = ck["classes"]
            assert ck["classes"] == self.classes, f"{name}: class order differs"

    def probs(self, crops: list) -> dict:
        torch = self._torch
        out = {}
        with torch.no_grad():
            for name, (m, tf) in self.models.items():
                x = torch.stack([tf(c) for c in crops]).to(self.device)
                out[name] = m(x).softmax(1).cpu()
        return out

    def score(self, check: str, crops: list) -> dict:
        if not crops:
            return {"models": {n: [] for n in self.models}, "ensemble": []}
        p = self.probs(crops)
        mean = sum(p.values()) / len(p)
        return {"models": {n: [round(v, 4) for v in claim_prob(q, self.classes, check)] for n, q in p.items()},
                "ensemble": [round(v, 4) for v in claim_prob(mean, self.classes, check)]}
