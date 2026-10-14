"""Schema pre-retrieval for many-table deployments (the "RAG" step for Text2SQL).

With a handful of tables the LLM can read the whole schema catalog directly.
With dozens or hundreds of tables that breaks down (context bloat, wandering
attention, cost), so we first *retrieve* a small candidate set with cheap
retrieval, then let the LLM pick precisely from the candidates
(two stages: recall -> precise select).

Backends:
- "tfidf" (default): pure-Python TF-IDF + cosine over table descriptions.
  Zero dependencies, works fully offline (fits the 本地部署 story).
- "embedding": dense vectors via an OpenAI-compatible embeddings endpoint
  (e.g. Gemini ``text-embedding-004``). Corpus vectors are cached on disk so
  the corpus is embedded once; each question costs one embedding call.
  Falls back to tfidf when the endpoint/key is unavailable.

Buyers with many tables: extend TABLE_KEYWORDS_CN below with a Chinese
business description per table -- retrieval quality lives or dies on it.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import re

from .schema_catalog import SCHEMA_CATALOG

log = logging.getLogger(__name__)

# Per-table Chinese business keywords. These matter more than anything else
# for Chinese-question retrieval: add a line per table when you add tables.
TABLE_KEYWORDS_CN: dict = {
    "corporates": "客户 公司 企业 信用评级 客户经理 开户",
    "letters_of_credit": "信用证 开证 证号 到期日 交单 承兑 通知行 开证行 金额 币种",
    "shipments": "发货 货运 船期 船名 航次 提单 港口 装港 卸港 到港 在途 货柜",
    "documents": "单据 交单 审单 不符点 商业发票 提单 产地证 保险单 汇票 相符",
    "payments": "付款 逾期 待付款 已付款 付款人 收款人 到期日 欠款",
}

_CACHE_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
_CACHE_FILE = os.path.join(_CACHE_DIR, "table_vectors.json")

_WORD_RE = re.compile(r"[a-zA-Z0-9_]+")
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")


def _tokenize(text: str) -> list:
    """English word tokens + Chinese character bigrams (no jieba needed)."""
    tokens = [w.lower() for w in _WORD_RE.findall(text)]
    chars = _CJK_RE.findall(text)
    tokens.extend(a + b for a, b in zip(chars, chars[1:]))
    return tokens


def build_corpus(catalog: dict | None = None) -> list:
    """[(table_name, retrieval_text)] for every table in the catalog."""
    catalog = catalog or SCHEMA_CATALOG
    corpus = []
    for t, info in catalog.items():
        parts = [t, info.get("comment", "")]
        for col, comment in info.get("columns", {}).items():
            parts.append(col)
            parts.append(comment or "")
        parts.append(TABLE_KEYWORDS_CN.get(t, ""))
        corpus.append((t, "\n".join(parts)))
    return corpus


def _tfidf_index(corpus: list) -> tuple:
    """Return (vocab idf dict, doc vectors as {term: weight})."""
    docs = [_tokenize(text) for _, text in corpus]
    df: dict = {}
    for toks in docs:
        for term in set(toks):
            df[term] = df.get(term, 0) + 1
    n = len(docs)
    idf = {term: math.log((n + 1) / (c + 1)) + 1.0 for term, c in df.items()}
    vectors = []
    for toks in docs:
        tf: dict = {}
        for term in toks:
            tf[term] = tf.get(term, 0) + 1
        total = len(toks) or 1
        vectors.append({term: (c / total) * idf[term] for term, c in tf.items()})
    return idf, vectors


def cosine(a: dict, b: dict) -> float:
    dot = sum(w * b.get(term, 0.0) for term, w in a.items())
    na = math.sqrt(sum(w * w for w in a.values()))
    nb = math.sqrt(sum(w * w for w in b.values()))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def tfidf_retrieve(query: str, top_k: int = 10, catalog: dict | None = None) -> list:
    """Rank tables by TF-IDF cosine similarity to the question."""
    corpus = build_corpus(catalog)
    idf, doc_vecs = _tfidf_index(corpus)
    q_toks = _tokenize(query)
    q_tf: dict = {}
    for term in q_toks:
        q_tf[term] = q_tf.get(term, 0) + 1
    total = len(q_toks) or 1
    # Terms unseen in the corpus get a middling idf so they don't dominate.
    default_idf = math.log((len(corpus) + 1) / 2) + 1.0
    q_vec = {t: (c / total) * idf.get(t, default_idf) for t, c in q_tf.items()}
    scored = sorted(
        ((cosine(q_vec, dv), table) for (table, _), dv in zip(corpus, doc_vecs)),
        key=lambda x: (-x[0], x[1]),
    )
    return [t for _, t in scored[:top_k]]


def _cache_key(model: str, corpus: list) -> str:
    h = hashlib.sha1()
    h.update(model.encode("utf-8"))
    for table, text in sorted(corpus):
        h.update(table.encode("utf-8"))
        h.update(text.encode("utf-8"))
    return h.hexdigest()


def _load_cached_vectors(model: str, corpus: list) -> dict | None:
    try:
        with open(_CACHE_FILE, encoding="utf-8") as f:
            data = json.load(f)
        if data.get("key") == _cache_key(model, corpus):
            return data["vectors"]
    except (OSError, ValueError):
        pass
    return None


def _save_cached_vectors(model: str, corpus: list, vectors: dict) -> None:
    try:
        os.makedirs(_CACHE_DIR, exist_ok=True)
        with open(_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump({"key": _cache_key(model, corpus), "vectors": vectors}, f)
    except OSError as e:
        log.warning("could not cache table vectors: %s", e)


def _embed(texts: list, model: str, base_url: str, api_key: str) -> list:
    import httpx

    resp = httpx.post(
        base_url.rstrip("/") + "/embeddings",
        headers={"Authorization": f"Bearer {api_key}"},
        json={"model": model, "input": texts},
        timeout=30,
    )
    resp.raise_for_status()
    return [item["embedding"] for item in resp.json()["data"]]


def embedding_retrieve(
    query: str, top_k: int = 10, catalog: dict | None = None,
    model: str | None = None,
) -> list:
    """Rank tables by dense-vector cosine similarity.

    Raises RuntimeError when no embedding endpoint/key is configured so the
    caller can fall back to tfidf.
    """
    from .config import llm_settings

    s = llm_settings()
    model = model or os.getenv("EMBEDDING_MODEL", "text-embedding-004").strip()
    if not s["api_key"]:
        raise RuntimeError("embedding backend needs LLM_API_KEY")
    corpus = build_corpus(catalog)
    vectors = _load_cached_vectors(model, corpus)
    if vectors is None:
        texts = [text for _, text in corpus]
        embs = _embed(texts, model, s["base_url"], s["api_key"])
        vectors = {table: emb for (table, _), emb in zip(corpus, embs)}
        _save_cached_vectors(model, corpus, vectors)
    (q_emb,) = _embed([query], model, s["base_url"], s["api_key"])
    nq = math.sqrt(sum(x * x for x in q_emb)) or 1.0
    scored = []
    for table, _ in corpus:
        v = vectors[table]
        nv = math.sqrt(sum(x * x for x in v)) or 1.0
        dot = sum(a * b for a, b in zip(q_emb, v))
        scored.append((dot / (nq * nv), table))
    scored.sort(key=lambda x: (-x[0], x[1]))
    return [t for _, t in scored[:top_k]]


def retrieve_tables(
    query: str, top_k: int = 10, method: str = "tfidf", catalog: dict | None = None
) -> list:
    """Top-k table names for a question; embedding falls back to tfidf."""
    catalog = catalog or SCHEMA_CATALOG
    top_k = max(1, min(top_k, len(catalog)))
    if method == "embedding":
        try:
            return embedding_retrieve(query, top_k=top_k, catalog=catalog)
        except Exception as e:  # noqa: BLE001
            log.warning("embedding retrieval failed (%s); falling back to tfidf", e)
    return tfidf_retrieve(query, top_k=top_k, catalog=catalog)


def prefilter_tables(question: str, catalog: dict | None = None) -> list | None:
    """Candidate table names, or None when pre-retrieval is disabled.

    Controlled by env: SCHEMA_RETRIEVAL=auto|tfidf|embedding|off
    (default auto), SCHEMA_TOP_K (default 10),
    SCHEMA_RETRIEVAL_THRESHOLD (default 12).
    """
    from .config import schema_retrieval_settings

    catalog = catalog or SCHEMA_CATALOG
    s = schema_retrieval_settings()
    n = len(catalog)
    method = s["method"]
    if method == "off":
        return None
    if method == "auto":
        if n <= s["threshold"]:
            return None
        method = "tfidf"
    if min(s["top_k"], n) >= n:
        return None
    try:
        return retrieve_tables(question, top_k=s["top_k"], method=method, catalog=catalog)
    except Exception as e:  # noqa: BLE001
        log.warning("schema pre-retrieval failed (%s); using full schema", e)
        return None
