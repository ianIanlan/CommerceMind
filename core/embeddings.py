"""Small, deterministic embeddings for offline and container deployments.

The vector captures character 1/2/3-gram overlap.  It is intentionally not a
replacement for a trained semantic embedding model, but gives Chroma a stable
local fallback without downloading model weights during application startup.
"""
from __future__ import annotations

import hashlib
import math
from typing import Dict, Iterable, List, Optional

import httpx


DEFAULT_EMBEDDING_DIMS = 384


def local_text_embedding(text: str, dims: int = DEFAULT_EMBEDDING_DIMS) -> List[float]:
    """Return a deterministic, unit-normalized character n-gram vector."""
    normalized = str(text or "").lower().strip()
    vector = [0.0] * dims
    tokens = set()
    for size in (1, 2, 3):
        if len(normalized) >= size:
            tokens.update(
                normalized[index:index + size]
                for index in range(len(normalized) - size + 1)
            )
    if not tokens:
        tokens.add(normalized)

    for token in tokens:
        digest = hashlib.md5(token.encode("utf-8")).digest()
        index = int.from_bytes(digest[:4], "big") % dims
        vector[index] += 1.0 if digest[4] % 2 == 0 else -1.0

    norm = math.sqrt(sum(value * value for value in vector))
    return [value / norm for value in vector] if norm else vector


def local_text_embeddings(texts: Iterable[str]) -> List[List[float]]:
    """Vectorize multiple texts with the same deterministic embedding."""
    return [local_text_embedding(text) for text in texts]


class EmbeddingProviderError(RuntimeError):
    """Raised when a configured semantic embedding provider cannot respond."""


class OpenAICompatibleEmbeddingClient:
    """Small async client for the standard ``POST /v1/embeddings`` contract."""

    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str,
        timeout_seconds: float = 30.0,
        client: Optional[httpx.AsyncClient] = None,
    ):
        if not api_key or not base_url or not model:
            raise ValueError("embedding api_key, base_url and model are required")
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.client = client
        self._cache: Dict[str, List[float]] = {}

    async def embed(self, texts: Iterable[str]) -> List[List[float]]:
        items = [str(text) for text in texts]
        missing = list(dict.fromkeys(text for text in items if text not in self._cache))
        if missing:
            vectors = await self._request(missing)
            if len(vectors) != len(missing):
                raise EmbeddingProviderError(
                    f"embedding count mismatch: requested={len(missing)}, returned={len(vectors)}"
                )
            self._cache.update(zip(missing, vectors))
        return [self._cache[text] for text in items]

    async def _request(self, texts: List[str]) -> List[List[float]]:
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        payload = {"model": self.model, "input": texts, "encoding_format": "float"}
        owns_client = self.client is None
        client = self.client or httpx.AsyncClient(timeout=self.timeout_seconds)
        try:
            response = await client.post(
                f"{self.base_url}/v1/embeddings",
                headers=headers,
                json=payload,
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
            body = response.json()
            data = sorted(body.get("data", []), key=lambda item: int(item.get("index", 0)))
            vectors = [self._normalized(list(map(float, item["embedding"]))) for item in data]
            if not vectors or any(not vector for vector in vectors):
                raise EmbeddingProviderError("provider returned empty embeddings")
            return vectors
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            raise EmbeddingProviderError(str(exc)) from exc
        finally:
            if owns_client:
                await client.aclose()

    @staticmethod
    def _normalized(vector: List[float]) -> List[float]:
        norm = math.sqrt(sum(value * value for value in vector))
        return [value / norm for value in vector] if norm else vector
