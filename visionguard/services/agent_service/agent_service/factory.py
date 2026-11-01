from .kb import BM25Retriever, load_chunks
from .llm import make_llm
from .risk import load_risk_config
from .service import AgentService, AnalysisStore


def build(kb_dir, risk_path=None, llm="template", db=":memory:", bus=None, llm_obj=None):
    chunks = load_chunks(kb_dir)
    if not chunks:
        raise RuntimeError(f"knowledge base empty: {kb_dir}")
    return AgentService(load_risk_config(risk_path), BM25Retriever(chunks), llm_obj or make_llm(llm), AnalysisStore(db), bus)
