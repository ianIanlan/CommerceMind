from concurrent.futures import ThreadPoolExecutor

from agents.agent_orchestrator import BaseAgent
from commerce.models import ActionStatus
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


def test_sensitive_tool_input_is_redacted_from_trace():
    safe = BaseAgent._trace_safe_input("prepare_address_change", {
        "order_id": "ORD-10001", "new_address": "上海市测试区隐私路 1 号"
    })

    assert safe["order_id"] == "ORD-10001"
    assert safe["new_address"] == "[REDACTED]"
