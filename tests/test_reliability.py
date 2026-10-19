from concurrent.futures import ThreadPoolExecutor

from agents.agent_orchestrator import BaseAgent
from commerce.models import ActionStatus
from commerce.payment_gateway import GatewayRefund
from commerce.service import CommerceService
from commerce.store import CommerceStore


def make_service():
    store = CommerceStore(":memory:")
    store.seed_demo_data()
    return store, CommerceService(store)


def test_concurrent_prepare_with_same_key_creates_one_action():
    store, service = make_service()
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(
            lambda _: service.prepare_refund("demo-user", "ORD-10001", "并发测试", "same-key"), range(16)
        ))

    assert len({item.action_id for item in results}) == 1
    assert len(store.fetch_all("SELECT * FROM pending_actions")) == 1


def test_state_change_between_prepare_and_confirm_rolls_back_action():
    store, service = make_service()
    action = service.prepare_cancel_order("demo-user", "ORD-10001", "取消")
    store.execute("UPDATE orders SET status='shipped' WHERE order_id='ORD-10001'")

    result = service.confirm_action("demo-user", action.action_id)

    assert result.status is ActionStatus.FAILED
    row = store.fetch_one("SELECT * FROM pending_actions WHERE action_id=?", (action.action_id,))
    assert row["status"] == "awaiting_confirmation"


def test_concurrent_confirmation_executes_payment_provider_once():
    class CountingGateway:
        def __init__(self):
            self.calls = 0

        def refund(self, payment_reference, amount, idempotency_key, metadata=None):
            self.calls += 1
            return GatewayRefund(True, "re_concurrent", "succeeded")

    store = CommerceStore(":memory:")
    store.seed_demo_data()
    store.execute(
        "UPDATE payments SET payment_id=?, channel='stripe' WHERE order_id=?",
        ("pi_concurrent", "ORD-10001"),
    )
    gateway = CountingGateway()
    service = CommerceService(store, payment_gateway=gateway)
    action = service.prepare_refund("demo-user", "ORD-10001", "并发确认", "confirm-once")

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(
            lambda _: service.confirm_action("demo-user", action.action_id), range(16)
        ))

    assert all(result.status is ActionStatus.SUCCEEDED for result in results)
    assert gateway.calls == 1
    assert len(store.fetch_all("SELECT * FROM refund_requests")) == 1


def test_confirmation_is_bound_to_original_order_and_payload():
    store, service = make_service()
    action = service.prepare_refund("demo-user", "ORD-10001", "原始订单", "bound-action")

    # A later user message mentioning another order cannot mutate the persisted action.
    other = service.prepare_refund("demo-user", "ORD-10005", "新订单", "bound-action-new")
    confirmed = service.confirm_action("demo-user", action.action_id)

    assert other.action_id != action.action_id
    assert confirmed.data["order_id"] == "ORD-10001"
    refund = store.fetch_one("SELECT * FROM refund_requests WHERE refund_id=?", (confirmed.resource_id,))
    assert refund["order_id"] == "ORD-10001"


def test_sensitive_tool_input_is_redacted_from_trace():
    safe = BaseAgent._trace_safe_input("prepare_address_change", {
        "order_id": "ORD-10001", "new_address": "上海市测试区隐私路 1 号"
    })

    assert safe["order_id"] == "ORD-10001"
    assert safe["new_address"] == "[REDACTED]"
