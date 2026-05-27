"""
LLM client module.

DashScopeClient is DEPRECATED for chat operations.
New code uses app.core.langchain_factory:
  - Chat: get_chat_model() → ChatOpenAI
  - Embeddings: get_embeddings() → OpenAIEmbeddings

Kept for:
  - local_embedding() hash-based fallback
  - Backward compat: chroma.py vector_store still uses llm_client.embed_texts()
"""

from __future__ import annotations

import hashlib
import math
from collections import Counter
from typing import Iterable

import requests

from app.core.config import settings


class DashScopeClient:
    def __init__(self) -> None:
        self.api_key = settings.dashscope_api_key

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        if self.api_key:
            try:
                return self._dashscope_embeddings(texts)
            except Exception:
                pass
        return [self.local_embedding(text) for text in texts]

    def chat(self, system_prompt: str, user_prompt: str) -> str | None:
        if not self.api_key:
            return None
        try:
            response = requests.post(
                "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": settings.dashscope_chat_model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    "temperature": 0.2,
                },
                timeout=60,
            )
            response.raise_for_status()
            data = response.json()
            return data["choices"][0]["message"]["content"]
        except Exception:
            return None

    def _dashscope_embeddings(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for text in texts:
            response = requests.post(
                "https://dashscope.aliyuncs.com/api/v1/services/embeddings/text-embedding/text-embedding",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": settings.dashscope_embedding_model,
                    "input": {"texts": [text]},
                },
                timeout=60,
            )
            response.raise_for_status()
            data = response.json()
            vectors.append(data["output"]["embeddings"][0]["embedding"])
        return vectors

    @staticmethod
    def local_embedding(text: str, dims: int = 384) -> list[float]:
        tokens = tokenize(text)
        counts = Counter(tokens)
        vector = [0.0] * dims
        for token, count in counts.items():
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            idx = int.from_bytes(digest[:4], "big") % dims
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            vector[idx] += sign * (1.0 + math.log(count))
        norm = math.sqrt(sum(v * v for v in vector)) or 1.0
        return [v / norm for v in vector]


def tokenize(text: str) -> list[str]:
    lowered = text.lower()
    current = []
    tokens = []
    for char in lowered:
        if char.isalnum() or "\u4e00" <= char <= "\u9fff":
            current.append(char)
        elif current:
            tokens.append("".join(current))
            current = []
    if current:
        tokens.append("".join(current))
    return tokens


def cosine(a: Iterable[float], b: Iterable[float]) -> float:
    aa = list(a)
    bb = list(b)
    if not aa or not bb:
        return 0.0
    dot = sum(x * y for x, y in zip(aa, bb))
    na = math.sqrt(sum(x * x for x in aa)) or 1.0
    nb = math.sqrt(sum(y * y for y in bb)) or 1.0
    return dot / (na * nb)


llm_client = DashScopeClient()

