import math
import asyncio
import json

import httpx

from core.embeddings import (
    DEFAULT_EMBEDDING_DIMS,
    EmbeddingProviderError,
    OpenAICompatibleEmbeddingClient,
    local_text_embedding,
)


def test_local_embedding_is_deterministic_and_normalized():
    first = local_text_embedding("订单退款")
    second = local_text_embedding("订单退款")

    assert first == second
    assert len(first) == DEFAULT_EMBEDDING_DIMS
    assert math.isclose(sum(value * value for value in first), 1.0, rel_tol=1e-9)


def test_local_embedding_distinguishes_different_text():
    assert local_text_embedding("订单退款") != local_text_embedding("登录失败")


def test_openai_compatible_embedding_batches_normalizes_and_caches():
    calls = []

    async def handler(request: httpx.Request):
        calls.append(request)
        body = json.loads(request.content)
        return httpx.Response(200, json={
            "data": [
                {"index": index, "embedding": [float(index + 1), 1.0]}
                for index, _ in enumerate(body["input"])
            ]
        })

    async def scenario():
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as http_client:
            client = OpenAICompatibleEmbeddingClient(
                "embedding-test-key", "https://embedding.test", "bge-test", client=http_client
            )
            first = await client.embed(["退款", "物流", "退款"])
            second = await client.embed(["退款"])
        return first, second

    first, second = asyncio.run(scenario())

    assert len(calls) == 1
    assert first[0] == first[2] == second[0]
    assert math.isclose(sum(value * value for value in first[0]), 1.0, rel_tol=1e-9)


def test_openai_compatible_embedding_exposes_provider_failure():
    async def handler(_request: httpx.Request):
        return httpx.Response(503, json={"error": "unavailable"})

    async def scenario():
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as http_client:
            client = OpenAICompatibleEmbeddingClient(
                "embedding-test-key", "https://embedding.test", "bge-test", client=http_client
            )
            await client.embed(["退款"])

    try:
        asyncio.run(scenario())
    except EmbeddingProviderError:
        pass
    else:
        raise AssertionError("provider failure must not be silently converted to a local embedding")
