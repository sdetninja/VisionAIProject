"""Knowledge base: markdown docs chunked by heading + BM25 retrieval (pure Python, no network).
Doc header (optional): lines 'key: value' before first blank line, e.g. 'source: SOP-PPE-01'."""
from __future__ import annotations
import math
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

_TOK = re.compile(r"[a-z0-9_]+")
STOP = {"the", "a", "an", "of", "and", "or", "to", "in", "is", "be", "for", "on", "with", "must", "are", "at", "by", "zone", "violation", "incident"}


def tokenize(t: str) -> list:
    return [w for w in _TOK.findall(t.lower()) if w not in STOP]


@dataclass
class Chunk:
    id: str
    source: str
    heading: str
    text: str


def load_chunks(directory: str) -> list:
    chunks = []
    for p in sorted(Path(directory).glob("**/*.md")):
        raw = p.read_text()
        meta, body = {}, raw
        head, _, rest = raw.partition("\n\n")
        if all(re.match(r"^\w+:\s", l) for l in head.splitlines() if l.strip()) and head.strip():
            meta = {l.split(":", 1)[0]: l.split(":", 1)[1].strip() for l in head.splitlines() if ":" in l}
            body = rest
        source = meta.get("source", p.stem)
        parts = re.split(r"(?m)^#{1,3}\s+", body)
        heads = re.findall(r"(?m)^#{1,3}\s+(.*)$", body)
        if parts and not body.lstrip().startswith("#"):
            heads.insert(0, "Intro")
        else:
            parts = parts[1:]
        for i, (h, t) in enumerate(zip(heads, parts)):
            t = t.split("\n", 1)[1] if t.startswith(h) and "\n" in t else t
            if t.strip():
                chunks.append(Chunk(f"{source}#{i}", source, h.strip(), t.strip()))
    return chunks


class Retriever(Protocol):
    def search(self, query: str, k: int = 4) -> list: ...


class BM25Retriever:
    def __init__(self, chunks: list, k1=1.5, b=0.75):
        self.chunks, self.k1, self.b = chunks, k1, b
        self.toks = [tokenize(c.heading + " " + c.heading + " " + c.text) for c in chunks]  # heading boost
        self.avg = (sum(map(len, self.toks)) / len(self.toks)) if self.toks else 0
        df = Counter(w for t in self.toks for w in set(t))
        n = len(chunks)
        self.idf = {w: math.log(1 + (n - d + 0.5) / (d + 0.5)) for w, d in df.items()}

    def search(self, query: str, k: int = 4, min_score: float = 2.0) -> list:
        q = tokenize(query)
        out = []
        for c, t in zip(self.chunks, self.toks):
            tf, L, s = Counter(t), len(t), 0.0
            for w in q:
                if w in tf:
                    s += self.idf[w] * tf[w] * (self.k1 + 1) / (tf[w] + self.k1 * (1 - self.b + self.b * L / (self.avg or 1)))
            if s >= min_score:
                out.append((s, c))
        out.sort(key=lambda x: -x[0])
        return [(c, round(s, 3)) for s, c in out[:k]]


class QdrantRetriever:  # pragma: no cover - optional, needs qdrant + embedding model
    """Same interface; embed chunks with sentence-transformers and upsert into Qdrant. Left as the production upgrade."""
    def __init__(self, *a, **k):
        raise NotImplementedError("Install qdrant-client + sentence-transformers and implement embedding here")
