import logging
from pathlib import Path

from .judge import Judge
from .judge.panel import make_panel
from .kb import BM25Retriever, load_chunks
from .llm import make_llm
from .m3client import HttpM3
from .risk import load_risk_config
from .service import AssessmentService

log = logging.getLogger("assessment")


def load_scorer(thresholds_path):
    """The ViT ensemble from milestone0/runs/judge/thresholds.yaml, or None (PPE verdicts become NO_JUDGE)."""
    if not thresholds_path:
        log.warning("no judge thresholds configured: PPE incidents get NO_JUDGE")
        return None
    if not Path(thresholds_path).exists():
        log.warning("judge thresholds %s not found (train them: milestone0 scripts 10-12): PPE incidents get NO_JUDGE",
                    thresholds_path)
        return None
    from .judge.vit import VitEnsemble
    s = VitEnsemble(thresholds_path)
    log.info("ViT judges loaded on %s: %s", s.device, ", ".join(s.versions))
    return s


def build(kb_dir, risk_path=None, llm="template", m3_url="http://localhost:8003", judge_thresholds=None, panel="none",
          llm_obj=None, m3=None, scorer=None, panel_obj=None):
    chunks = load_chunks(kb_dir)
    if not chunks:
        raise RuntimeError(f"knowledge base empty: {kb_dir}")
    judge = Judge(scorer if scorer is not None else load_scorer(judge_thresholds), panel_obj or make_panel(panel))
    kb_version = f"{Path(kb_dir).name}:{len(chunks)} sections"
    return AssessmentService(load_risk_config(risk_path), BM25Retriever(chunks), llm_obj or make_llm(llm), judge,
                             m3 or HttpM3(m3_url), kb_version)
