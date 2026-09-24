from commerce.guard import CommerceResponseGuard


def test_guard_blocks_unverified_refund_claim():
    result = CommerceResponseGuard().review("已经为您退款成功，请等待到账。", [])
    assert result.changed is True
    assert "UNVERIFIED_REFUND_CLAIM" in result.violations
    assert "尚未执行退款" in result.content


def test_guard_blocks_sensitive_data_request():
    result = CommerceResponseGuard().review("请提供支付密码和短信验证码。", [])
    assert result.violations == ["SENSITIVE_DATA_REQUEST"]
    assert "请不要提供" in result.content


def test_guard_allows_verified_query_claim():
    result = CommerceResponseGuard().review(
        "已查询订单 ORD-10001，当前状态为待发货。", ["get_order"]
    )
    assert result.changed is False


def test_guard_rewrites_query_claim_without_tool_evidence():
    result = CommerceResponseGuard().review("已查询订单，当前为待发货。", [])
    assert "UNVERIFIED_QUERY_CLAIM" in result.violations
    assert "当前尚未完成查询" in result.content
