"""Keyword tokens and embeddings of provisions, and the scoring helpers of hybrid search (BM25, reciprocal rank fusion).

Tokens are made here rather than by Postgres full-text search or pg_trgm: those decide word characters by locale and
split Devanagari at its matras. A token is a run of letters / digits / Devanagari signs (not the danda) after NFC,
ASCII digits, zero-width removal and Latin lower-casing. A light Marathi suffix stem is added next to each inflected
token (निर्णयान्वये → निर्णयान्वये + निर्णय), the same way for documents and queries.
"""
from __future__ import annotations

import math
import re
import unicodedata
from typing import Iterable, Sequence

from rci.adapters.embeddings import EmbeddingClient, LocalHashEmbedder, _OpenAIEmbeddings
from rci.config import Settings, get_settings
from rci.pipeline.normalise import ascii_digits

_ZW = re.compile("[​-‍⁠﻿­]")
TOKEN = re.compile(r"[\wऀ-ॣ०-ॿ]+")
SUFFIXES = sorted(["च्या", "चे", "ची", "चा", "ला", "ना", "ने", "त", "मध्ये", "साठी", "नुसार", "ान्वये", "कडून"], key=len, reverse=True)


def stem(tok: str) -> str:
    for suf in SUFFIXES:
        if tok.endswith(suf) and len(tok) - len(suf) >= 2:
            return tok[: -len(suf)]
    return tok


def tokens(text: str) -> list[str]:
    """Tokens with repeats (term frequency counts), each inflected token followed by its stem."""
    t = _ZW.sub("", ascii_digits(unicodedata.normalize("NFC", text or ""))).casefold()
    out: list[str] = []
    for tok in TOKEN.findall(t):
        tok = tok.strip("_")
        if not tok:
            continue
        out.append(tok)
        st = stem(tok)
        if st != tok:
            out.append(st)
    return out


def query_tokens(*texts: str) -> list[str]:
    seen: dict[str, None] = {}
    for t in texts:
        for tok in tokens(t):
            seen.setdefault(tok, None)
    return list(seen)


def bm25(query: Sequence[str], doc: Sequence[str], df: dict[str, int], n_docs: int, avgdl: float,
         k1: float = 1.2, b: float = 0.75) -> float:
    tf: dict[str, int] = {}
    for t in doc:
        tf[t] = tf.get(t, 0) + 1
    dl = len(doc)
    score = 0.0
    for q in set(query):
        f = tf.get(q)
        if not f:
            continue
        n = df.get(q, 0)
        idf = math.log(1 + (n_docs - n + 0.5) / (n + 0.5))
        score += idf * f * (k1 + 1) / (f + k1 * (1 - b + b * dl / max(1e-9, avgdl)))
    return score


def rrf(rankings: Iterable[Sequence[str]], k: int = 60) -> dict[str, float]:
    """Reciprocal rank fusion: Σ 1 / (k + rank) over the rankings an id appears in (rank from 1)."""
    out: dict[str, float] = {}
    for ranking in rankings:
        for r, pid in enumerate(ranking, start=1):
            out[pid] = out.get(pid, 0.0) + 1.0 / (k + r)
    return out


def embed_text(subject_mr: str | None, heading_path: Sequence[str], label: str, text: str) -> str:
    head = " › ".join(h for h in heading_path if h)
    return "\n".join(x for x in (subject_mr or "", head, label, text) if x)


def get_library_embedder(settings: Settings | None = None) -> EmbeddingClient:
    """OpenAI text-embedding-3-large at 1536 dimensions; the local hashing embedder without an API key (and in tests)."""
    s = settings or get_settings()
    if s.library_embedding_provider == "openai" and s.openai_api_key:
        from openai import OpenAI

        return _OpenAIEmbeddings(OpenAI(api_key=s.openai_api_key), s.library_embedding_model, f"openai:{s.library_embedding_model}")
    return LocalHashEmbedder()


def embed_all(embedder: EmbeddingClient, texts: list[str], batch: int = 64) -> list[list[float]]:
    out: list[list[float]] = []
    for i in range(0, len(texts), batch):
        out += embedder.embed(texts[i:i + batch], data_class="public")
    return out
