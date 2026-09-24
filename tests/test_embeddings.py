import math

from core.embeddings import DEFAULT_EMBEDDING_DIMS, local_text_embedding


def test_local_embedding_is_deterministic_and_normalized():
    first = local_text_embedding("订单退款")
    second = local_text_embedding("订单退款")

    assert first == second
    assert len(first) == DEFAULT_EMBEDDING_DIMS
    assert math.isclose(sum(value * value for value in first), 1.0, rel_tol=1e-9)


def test_local_embedding_distinguishes_different_text():
    assert local_text_embedding("订单退款") != local_text_embedding("登录失败")
