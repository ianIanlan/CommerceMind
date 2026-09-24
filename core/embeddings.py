"""Small, deterministic embeddings for offline and container deployments.

The vector captures character 1/2/3-gram overlap.  It is intentionally not a
replacement for a trained semantic embedding model, but gives Chroma a stable
local fallback without downloading model weights during application startup.
"""
from __future__ import annotations

import hashlib
import math
from typing import Iterable, List


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
