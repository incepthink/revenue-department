"""Embedding client for the three case facets (facts / documents / issues).

provider = local | openai
  local   deterministic feature-hashing embedder (word unigrams+bigrams and character 3-grams, signed
          hashing, L2-normalised, 1536 dims). No network. Cosine similarity is meaningful within the
          `local-hash-v1` model only.
  openai  OpenAI API (text-embedding-3-small → 1536 dims), key from OPENAI_API_KEY.

`case_embeddings.model` records which model produced a vector; matching (Phase 3) must compare like with like.
"""
from __future__ import annotations

import hashlib
import math
import re
import unicodedata
from typing import Sequence

from rci.adapters.llm import DataClass, DataResidencyError
from rci.config import Settings, get_settings
from rci.db.models import EMBEDDING_DIM
from rci.pipeline.normalise import ascii_digits

_TOKEN = re.compile(r"[\wऀ-ॿ]+", re.UNICODE)


class EmbeddingClient:
    model: str = "base"
    dim: int = EMBEDDING_DIM

    def allows_real_data(self) -> bool:
        return False

    def embed(self, texts: Sequence[str], *, data_class: DataClass) -> list[list[float]]:
        if data_class == "real" and not self.allows_real_data():
            raise DataResidencyError(f"embedding model {self.model!r} is not approved for real department documents")
        return self._embed(list(texts))

    def _embed(self, texts: list[str]) -> list[list[float]]:
        raise NotImplementedError


class LocalHashEmbedder(EmbeddingClient):
    model = "local-hash-v1"

    def allows_real_data(self) -> bool:
        return True  # runs in-process; nothing leaves the machine

    @staticmethod
    def _features(text: str) -> list[tuple[str, float]]:
        t = ascii_digits(unicodedata.normalize("NFC", text)).casefold()
        words = _TOKEN.findall(t)
        feats = [(f"w:{w}", 1.0) for w in words]
        feats += [(f"b:{a} {b}", 0.7) for a, b in zip(words, words[1:])]
        for w in words:
            padded = f"^{w}$"
            feats += [(f"c:{padded[i:i + 3]}", 0.3) for i in range(max(1, len(padded) - 2))]
        return feats

    def _vector(self, text: str) -> list[float]:
        v = [0.0] * self.dim
        for feat, weight in self._features(text):
            h = hashlib.blake2b(feat.encode(), digest_size=8).digest()
            idx = int.from_bytes(h[:4], "little") % self.dim
            v[idx] += weight if h[4] & 1 else -weight
        norm = math.sqrt(sum(x * x for x in v))
        return [x / norm for x in v] if norm else v

    def _embed(self, texts: list[str]) -> list[list[float]]:
        return [self._vector(t) for t in texts]


class _OpenAIEmbeddings(EmbeddingClient):
    def __init__(self, client, model: str, label: str):
        self._client, self._model, self.model = client, model, label

    def _embed(self, texts: list[str]) -> list[list[float]]:
        resp = self._client.embeddings.create(model=self._model, input=texts, dimensions=self.dim)
        return [d.embedding for d in sorted(resp.data, key=lambda d: d.index)]


class OpenAIEmbeddings(_OpenAIEmbeddings):
    def __init__(self, settings: Settings):
        from openai import OpenAI

        if not settings.openai_api_key:
            raise RuntimeError("OPENAI_API_KEY not configured")
        super().__init__(OpenAI(api_key=settings.openai_api_key), settings.openai_embedding_model,
                         f"openai:{settings.openai_embedding_model}")

    def allows_real_data(self) -> bool:
        return True


def get_embedding_client(settings: Settings | None = None) -> EmbeddingClient:
    s = settings or get_settings()
    if s.embedding_provider == "openai":
        return OpenAIEmbeddings(s)
    return LocalHashEmbedder()


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0
