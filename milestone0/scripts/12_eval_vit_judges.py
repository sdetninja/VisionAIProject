"""Evaluate the three ViT judges and their ensemble on the held-out test crops, and pick the judge thresholds.

  python scripts/12_eval_vit_judges.py                     # -> reports/vit_judges_report.md (+ runs/judge/thresholds.yaml)

How a judge is used (assessment service): the rule engine CLAIMS something about a crop, the judge returns the
probability that the claim is true, renormalised over the classes of that check:
  helmet check  P(no_helmet) over {helmet, no_helmet}          claim: "this person has no helmet"
  vest check    P(no_vest)   over {vest, no_vest}              claim: "this person has no vest"
  hazard check  P(fire)+P(smoke) over {fire, smoke, none}       claim: "there is fire/smoke here"
Ensemble = mean of the three models' probabilities.

Thresholds are CHOSEN on val and REPORTED on test (as in 09_tune_hazard_thresholds.py). A verifier must not drop real
violations, so per check:
  reject_below  highest threshold that still keeps >= 98 % (hazard: 99.5 %) of TRUE claims on val  -> below it the judge says REJECTED
  confirm_above lowest threshold whose val precision is >= 98 %                   -> above it the judge says CONFIRMED
  in between    UNSURE -> a person decides
Caveat: test crops come from dataset boxes, not from the detector's mistakes, so the "false claims" here are the
opposite class (a helmet head for a no-helmet claim). Real false alarms (lookalikes, odd angles) can be harder.
"""
import json
import time
from pathlib import Path

import torch
import yaml
from torchvision import datasets, transforms

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data/processed/judge_crops"
RUNS = ROOT / "runs/judge"
REPORT = ROOT / "reports/vit_judges_report.md"
ARCHS = ["vit_s", "dinov2_s", "swin_t"]
CHECKS = {   # check: (claim classes, other classes)
    "helmet": (["no_helmet"], ["helmet"]),
    "vest": (["no_vest"], ["vest"]),
    "hazard": (["fire", "smoke"], ["none"]),
}
KEEP = {"helmet": 0.98, "vest": 0.98, "hazard": 0.995}   # share of TRUE claims that must stay above reject_below on val
PREC = 0.98                    # fire/smoke is stricter: a missed fire costs far more than a reviewed false alarm


def load(arch, dev):
    import timm
    ck = torch.load(RUNS / arch / "best.pt", map_location="cpu", weights_only=False)
    m = timm.create_model(ck["timm_name"], pretrained=False, num_classes=len(ck["classes"]), **ck["timm_kwargs"])
    m.load_state_dict(ck["state_dict"])
    return m.to(dev).eval(), ck


@torch.no_grad()
def predict(model, ck, split, dev):
    tf = transforms.Compose([transforms.Resize((ck["img_size"],) * 2), transforms.ToTensor(),
                             transforms.Normalize(ck["mean"], ck["std"])])
    ds = datasets.ImageFolder(DATA / split, tf)
    dl = torch.utils.data.DataLoader(ds, batch_size=128, num_workers=4)
    probs, ys = [], []
    for x, y in dl:
        probs.append(model(x.to(dev)).softmax(1).cpu()); ys.append(y)
    return torch.cat(probs), torch.cat(ys), ds.classes


def check_scores(p, y, classes, check):
    """claim probability and truth for the crops that belong to this check"""
    pos, neg = CHECKS[check]
    pi, ni = [classes.index(c) for c in pos], [classes.index(c) for c in neg]
    rows = torch.isin(y, torch.tensor(pi + ni))
    sub = p[rows][:, pi + ni]
    score = sub[:, : len(pi)].sum(1) / sub.sum(1)
    truth = torch.isin(y[rows], torch.tensor(pi))
    return score, truth


def auc(score, truth):
    s, t = score.numpy(), truth.numpy()
    order = s.argsort()
    ranks = order.argsort() + 1
    npos, nneg = t.sum(), (~t).sum()
    return float((ranks[t].sum() - npos * (npos + 1) / 2) / (npos * nneg)) if npos and nneg else float("nan")


def pick(score, truth, keep):
    grid = [i / 100 for i in range(1, 100)]
    reject = max([g for g in grid if (score[truth] >= g).float().mean() >= keep] or [0.01])
    confirm = 0.99
    for g in grid:
        sel = score >= g
        if sel.sum() and truth[sel].float().mean() >= PREC:
            confirm = g
            break
    return reject, max(confirm, reject)


def outcome(score, truth, reject, confirm):
    t, f = truth, ~truth
    r = lambda m, sel: round((m & sel).sum().item() / max(m.sum().item(), 1) * 100, 1)
    return {"true_kept_%": r(t, score >= reject), "true_confirmed_%": r(t, score >= confirm),
            "false_rejected_%": r(f, score < reject), "false_confirmed_%": r(f, score >= confirm),
            "unsure_%": round(((score >= reject) & (score < confirm)).float().mean().item() * 100, 1),
            "acc@0.5_%": round(((score >= 0.5) == truth).float().mean().item() * 100, 1), "auc": round(auc(score, truth), 4)}


def main():
    dev = "mps" if torch.backends.mps.is_available() else "cpu"
    preds, lat = {}, {}
    for a in ARCHS:
        m, ck = load(a, dev)
        preds[a] = {s: predict(m, ck, s, dev) for s in ("val", "test")}
        x = torch.randn(3, 3, ck["img_size"], ck["img_size"], device=dev)       # one incident: ~3 crops
        for _ in range(5):
            m(x)
        t = time.time()
        for _ in range(20):
            m(x).cpu()
        lat[a] = round((time.time() - t) / 20 * 1000, 1)
    classes = preds[ARCHS[0]]["test"][2]
    for s in ("val", "test"):
        preds.setdefault("ensemble", {})[s] = (sum(preds[a][s][0] for a in ARCHS) / len(ARCHS), preds[ARCHS[0]][s][1], classes)
    lat["ensemble"] = round(sum(lat[a] for a in ARCHS), 1)

    rows, thresholds = [], {}
    for check in CHECKS:
        for name in ARCHS + ["ensemble"]:
            vs, vt = check_scores(*preds[name]["val"], check)
            ts, tt = check_scores(*preds[name]["test"], check)
            rej, con = pick(vs, vt, KEEP[check])
            o = outcome(ts, tt, rej, con)
            rows.append((check, name, rej, con, o, int(tt.sum()), int((~tt).sum())))
            if name == "ensemble":
                thresholds[check] = {"reject_below": rej, "confirm_above": con}

    seven = {}
    for name in ARCHS + ["ensemble"]:
        p, y, _ = preds[name]["test"]
        seven[name] = round((p.argmax(1) == y).float().mean().item() * 100, 1)

    meta = {a: json.load(open(RUNS / a / "metrics.json")) for a in ARCHS}
    L = ["# ViT judges: three architectures and their ensemble", "",
         "Generated by `scripts/12_eval_vit_judges.py`. Crops: `data/processed/judge_crops` (script 10), models: "
         "`runs/judge/<arch>/best.pt` (script 11). Thresholds chosen on **val**, numbers below on **test**.", "",
         "## Models", "", "| Model | timm weights | Best epoch | Val balanced acc | Test 7-class acc | Latency, 3 crops (ms) |",
         "|---|---|---|---|---|---|"]
    for a in ARCHS:
        best = max(meta[a]["history"], key=lambda h: h["balanced_acc"])
        L.append(f"| {a} | `{meta[a]['timm_name']}` | {best['epoch']} | {best['balanced_acc']} | {seven[a]} % | {lat[a]} |")
    L.append(f"| **ensemble** (mean) | | | | **{seven['ensemble']} %** | {lat['ensemble']} |")
    L += ["", "## Per check (test)", "",
          f"Policy: below `reject` the judge says REJECTED (chosen to keep >= 98 % of true claims on val, 99.5 % for hazard); at or above "
          f"`confirm` CONFIRMED (val precision >= {PREC:.0%}); in between UNSURE (a person decides).", "",
          "| Check | Model | reject | confirm | AUC | acc@0.5 | true kept | true confirmed | false rejected | false confirmed | unsure | test n (true/false) |",
          "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for check, name, rej, con, o, npos, nneg in rows:
        b = "**" if name == "ensemble" else ""
        L.append(f"| {check} | {b}{name}{b} | {rej:.2f} | {con:.2f} | {o['auc']} | {o['acc@0.5_%']} % | {o['true_kept_%']} % | "
                 f"{o['true_confirmed_%']} % | {o['false_rejected_%']} % | {o['false_confirmed_%']} % | {o['unsure_%']} % | {npos}/{nneg} |")
    L += ["", "- **true kept**: real violations/hazards NOT rejected by the judge (must stay high: a rejected real one is a miss).",
          "- **false rejected**: false claims the judge throws out (the false alarms it saves a reviewer from).",
          "- Test crops are dataset boxes, not detector mistakes: real false alarms (lookalikes) can be harder; re-check on "
          "reviewed incidents (M3 `labels`) once there are a few hundred.", "",
          "## Thresholds for the assessment service (ensemble)", "", "```yaml", yaml.safe_dump({"vit_judge": thresholds},
                                                                                             sort_keys=False).strip(), "```"]
    REPORT.write_text("\n".join(L) + "\n")
    # model paths relative to this file, so runs/judge can be copied (e.g. to ops/deploy/models/judge) as one folder
    yaml.safe_dump({"models": {a: f"{a}/best.pt" for a in ARCHS}, "thresholds": thresholds},
                   open(RUNS / "thresholds.yaml", "w"), sort_keys=False)
    print("\n".join(L))


if __name__ == "__main__":
    main()
