"""Head crops cut from PERSON boxes, as the assessment service does at run time, on the combined test split.

  python scripts/13_check_runtime_head_crops.py      # needs runs/judge (scripts 10-12); ~1 min on an M4

The judges were trained on labelled head boxes; at run time the service only has the person box from the rule engine.
For ~1,200 test persons with one labelled helmet/no_helmet head, this compares head-crop rules: AUC, and the verdicts
with the ensemble thresholds. Result 2026-11-16: min(0.6 w, 0.3 h) + 15 % (used in agent_service/judge/crops.py).
"""
import random, sys, glob
from pathlib import Path
import torch
from PIL import Image
sys.path.insert(0, "../visionguard/services/agent_service")
from agent_service.judge.vit import VitEnsemble
NAMES = ["person", "helmet", "no_helmet", "vest", "gloves", "no_gloves", "goggles", "mask", "fire", "smoke"]
SRC = Path("data/processed/combined")
def boxes(lp, W, H):
    out = []
    for l in lp.read_text().split("\n") if lp.exists() else []:
        p = l.split()
        if len(p) == 5:
            c, x, y, w, h = int(p[0]), *map(float, p[1:]); out.append((NAMES[c], (x-w/2)*W, (y-h/2)*H, (x+w/2)*W, (y+h/2)*H))
    return out
def pad(b, f, W, H):
    x1, y1, x2, y2 = b; dw, dh = (x2-x1)*f, (y2-y1)*f
    return max(0, x1-dw), max(0, y1-dh), min(W, x2+dw), min(H, y2+dh)
def head(p, share, wfrac):
    x1, y1, x2, y2 = p; w, h = x2-x1, y2-y1; s = min(wfrac*w, share*h); cx = (x1+x2)/2
    return cx-s/2, y1, cx+s/2, y1+s
VARIANTS = {"now: min(w,0.30h) pad25": (0.30, 1.0, .25), "min(0.6w,0.30h) pad15": (0.30, 0.6, .15),
            "min(0.5w,0.25h) pad15": (0.25, 0.5, .15), "min(0.45w,0.2h) pad15": (0.20, 0.45, .15)}
rng = random.Random(0); samples = []
imgs = sorted((SRC/"images/test").glob("*")); rng.shuffle(imgs)
for ip in imgs:
    im = None
    W = H = None
    for _ in [0]:
        im = Image.open(ip); W, H = im.size
    bs = boxes(SRC/"labels/test"/(ip.stem+".txt"), W, H)
    heads = [(n, b) for n, *b in bs if n in ("helmet", "no_helmet")]
    for n, *p in bs:
        if n != "person" or p[3]-p[1] < 96 or p[2]-p[0] < 32: continue
        inside = [hn for hn, hb in heads if p[0] <= (hb[0]+hb[2])/2 <= p[2] and p[1] <= (hb[1]+hb[3])/2 <= p[1]+0.4*(p[3]-p[1])]
        if len(set(inside)) == 1: samples.append((ip, p, inside[0] == "no_helmet", W, H))
    if len(samples) >= 1200: break
pos = sum(s[2] for s in samples); print(f"{len(samples)} persons with one labelled head ({pos} no_helmet)")
ens = VitEnsemble("runs/judge/thresholds.yaml"); t = ens.thresholds["helmet"]
def auc(s, y):
    s, y = torch.tensor(s), torch.tensor(y); r = s.argsort().argsort().float()+1; n1, n0 = y.sum(), (~y).sum()
    return float((r[y].sum() - n1*(n1+1)/2) / (n1*n0))
for name, (share, wf, pd) in VARIANTS.items():
    scores, ys = [], []
    for i in range(0, len(samples), 64):
        batch = samples[i:i+64]; crops = []
        for ip, p, y, W, H in batch:
            crops.append(Image.open(ip).convert("RGB").crop(tuple(int(v) for v in pad(head(p, share, wf), pd, W, H)))); ys.append(y)
        scores += ens.score("helmet", crops)["ensemble"]
    s, y = torch.tensor(scores), torch.tensor(ys)
    kept = (s[y] >= t["reject_below"]).float().mean()*100; frej = (s[~y] < t["reject_below"]).float().mean()*100
    conf_prec = y[s >= t["confirm_above"]].float().mean()*100
    print(f"{name:26} AUC {auc(scores, ys):.4f}  real kept {kept:.1f}%  false rejected {frej:.1f}%  confirmed precision {conf_prec:.1f}%  "
          f"unsure {(((s>=t['reject_below'])&(s<t['confirm_above'])).float().mean()*100):.1f}%")
