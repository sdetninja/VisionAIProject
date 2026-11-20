"""Train the ViT judges: three different vision-transformer families on the same crops (an ensemble at run time).

  python scripts/11_train_vit_judges.py                              # all three: 5 epochs, <= 4000 crops/class (~2 h on an M4)
  python scripts/11_train_vit_judges.py --arch vit_s --epochs 8      # one of them
  python scripts/11_train_vit_judges.py --limit 512 --epochs 1       # smoke test

Data: data/processed/judge_crops (scripts/10_build_judge_crops.py), 7 classes:
  helmet, no_helmet, vest, no_vest, fire, smoke, none
Models (ImageNet-pretrained in timm, fine-tuned end to end at 224 px). Three families so their mistakes differ:
  vit_s    ViT-S/16, AugReg (supervised ImageNet-21k -> 1k)      plain ViT
  dinov2_s ViT-S/14, DINOv2 (self-supervised, LVD-142M)           same shape, very different pretraining
  swin_t   Swin-T (ImageNet-22k -> 1k)                            hierarchical, shifted-window attention
Output: runs/judge/<arch>/best.pt (best val balanced accuracy) + metrics.json + train.log
"""
import argparse
import json
import time
from pathlib import Path

import torch
import torch.nn as nn
from torch.utils.data import DataLoader, Subset
from torchvision import datasets, transforms

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data/processed/judge_crops"
OUT = ROOT / "runs/judge"
ARCHS = {
    "vit_s": dict(name="vit_small_patch16_224.augreg_in21k_ft_in1k", lr=1e-4),
    "dinov2_s": dict(name="vit_small_patch14_dinov2.lvd142m", lr=3e-5, kw={"img_size": 224}),
    "swin_t": dict(name="swin_tiny_patch4_window7_224.ms_in22k_ft_in1k", lr=1e-4),
}
CLASSES = ["fire", "helmet", "no_helmet", "no_vest", "none", "smoke", "vest"]   # ImageFolder order (alphabetical)
MEAN, STD = (0.485, 0.456, 0.406), (0.229, 0.224, 0.225)
IMG = 224


def tfms(train):
    if train:
        return transforms.Compose([
            transforms.RandomResizedCrop(IMG, scale=(0.6, 1.0), ratio=(0.6, 1.6)),
            transforms.RandomHorizontalFlip(),
            transforms.ColorJitter(0.3, 0.3, 0.2, 0.02),
            transforms.ToTensor(), transforms.Normalize(MEAN, STD)])
    return transforms.Compose([transforms.Resize((IMG, IMG)), transforms.ToTensor(), transforms.Normalize(MEAN, STD)])


def device():
    return "mps" if torch.backends.mps.is_available() else "cuda" if torch.cuda.is_available() else "cpu"


@torch.no_grad()
def evaluate(model, loader, dev):
    model.eval()
    n = len(CLASSES)
    conf = torch.zeros(n, n, dtype=torch.long)
    for x, y in loader:
        p = model(x.to(dev)).argmax(1).cpu()
        for t, q in zip(y, p):
            conf[t, q] += 1
    recall = (conf.diag().float() / conf.sum(1).clamp(min=1).float())
    return {"balanced_acc": round(recall.mean().item(), 4), "acc": round((conf.diag().sum() / conf.sum()).item(), 4),
            "recall": {c: round(r, 4) for c, r in zip(CLASSES, recall.tolist())}}


def train_one(key, a, dev):
    import timm
    spec = ARCHS[key]
    out = OUT / key
    out.mkdir(parents=True, exist_ok=True)
    log = open(out / "train.log", "a")

    def say(*m):
        s = " ".join(str(x) for x in m)
        print(f"[{key}] {s}", flush=True)
        log.write(s + "\n"); log.flush()

    tr = datasets.ImageFolder(DATA / "train", tfms(True))
    va = datasets.ImageFolder(DATA / "val", tfms(False))
    assert tr.classes == CLASSES, tr.classes
    if a.train_cap:                                              # at most N crops per class (bounded training time)
        g = torch.Generator().manual_seed(0)
        per = {}
        for i in torch.randperm(len(tr.samples), generator=g).tolist():
            per.setdefault(tr.samples[i][1], []).append(i)
        tr = Subset(tr, sorted(i for v in per.values() for i in v[: a.train_cap]))
    if a.limit:
        g = torch.Generator().manual_seed(0)
        tr = Subset(tr.dataset if isinstance(tr, Subset) else tr, torch.randperm(len(tr), generator=g)[: a.limit].tolist())
        va = Subset(va, torch.randperm(len(va), generator=g)[: a.limit // 2].tolist())
    targets = [tr.dataset.samples[i][1] for i in tr.indices] if isinstance(tr, Subset) else [y for _, y in tr.samples]
    counts = torch.bincount(torch.tensor(targets), minlength=len(CLASSES)).float()
    weights = (counts.sum() / counts.clamp(min=1)) ** 0.5            # inverse sqrt frequency: vest/fire are smaller
    weights = weights / weights.mean()
    dl = dict(batch_size=a.batch, num_workers=a.workers, persistent_workers=a.workers > 0)
    tl = DataLoader(tr, shuffle=True, drop_last=True, **dl)
    vl = DataLoader(va, shuffle=False, **dl)

    model = timm.create_model(spec["name"], pretrained=True, num_classes=len(CLASSES), **spec.get("kw", {})).to(dev)
    opt = torch.optim.AdamW(model.parameters(), lr=spec["lr"], weight_decay=0.05)
    steps = a.epochs * len(tl)
    assert steps >= 10, f"only {steps} training steps: use a larger --limit"
    sched = torch.optim.lr_scheduler.OneCycleLR(opt, max_lr=spec["lr"], total_steps=steps, pct_start=min(0.3, max(0.1, 3 / steps)))
    loss_fn = nn.CrossEntropyLoss(weight=weights.to(dev), label_smoothing=0.1)
    say(f"model {spec['name']}  train {len(tr)}  val {len(va)}  epochs {a.epochs}  device {dev}  class weights",
        dict(zip(CLASSES, [round(w, 2) for w in weights.tolist()])))
    best, hist = -1, []
    for ep in range(1, a.epochs + 1):
        model.train()
        t0, tot, n = time.time(), 0.0, 0
        for i, (x, y) in enumerate(tl):
            x, y = x.to(dev), y.to(dev)
            loss = loss_fn(model(x), y)
            opt.zero_grad(set_to_none=True)
            loss.backward()
            opt.step(); sched.step()
            tot += loss.item() * len(y); n += len(y)
            if i % 50 == 0:
                say(f"epoch {ep} step {i}/{len(tl)} loss {loss.item():.3f}  {n / (time.time() - t0):.0f} img/s")
        m = evaluate(model, vl, dev)
        m.update(epoch=ep, train_loss=round(tot / max(n, 1), 4), minutes=round((time.time() - t0) / 60, 1))
        hist.append(m)
        say(f"epoch {ep} done: train loss {m['train_loss']}  val balanced acc {m['balanced_acc']}  acc {m['acc']}  "
            f"({m['minutes']} min)  recall {m['recall']}")
        if m["balanced_acc"] > best:
            best = m["balanced_acc"]
            torch.save({"arch": key, "timm_name": spec["name"], "timm_kwargs": spec.get("kw", {}), "classes": CLASSES,
                        "img_size": IMG, "mean": MEAN, "std": STD, "state_dict": model.state_dict(),
                        "epoch": ep, "val": m}, out / "best.pt")
            say(f"saved best.pt (val balanced acc {best})")
    json.dump({"arch": key, "timm_name": spec["name"], "best_val_balanced_acc": best, "history": hist},
              open(out / "metrics.json", "w"), indent=2)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--arch", choices=list(ARCHS), action="append", help="default: all three")
    ap.add_argument("--epochs", type=int, default=5)
    ap.add_argument("--train-cap", type=int, default=4000, help="max train crops per class (0 = all)")
    ap.add_argument("--batch", type=int, default=64)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--limit", type=int, default=0, help="train on N random crops (smoke test)")
    a = ap.parse_args()
    torch.manual_seed(0)
    dev = device()
    for key in a.arch or list(ARCHS):
        train_one(key, a, dev)


if __name__ == "__main__":
    main()
