from commerce.models import ActionStatus
from commerce.service import CommerceService
from commerce.store import CommerceStore


def make_service():
    store = CommerceStore(":memory:")
    store.seed_demo_data()
    return store, CommerceService(store)


def test_user_cannot_read_another_users_order():
    _, service = make_service()
    result = service.get_order("demo-user", "ORD-OTHER")
    assert result.success is False
    assert result.error_code == "ORDER_NOT_FOUND"


def test_duplicate_payment_is_evidence_not_an_automatic_refund():
    _, service = make_service()
    result = service.get_payment_records("demo-user", "ORD-10005")
    assert result.success is True
    assert result.data["duplicate_candidate"] is True
    assert "仍需" in result.data["conclusion"]


def test_virtual_product_requires_manual_review():
    _, service = make_service()
    result = service.check_return_eligibility("demo-user", "ORD-10004")
    assert result.eligible is False
    assert result.requires_manual_review is True
    assert result.reason_code == "VIRTUAL_PRODUCT_MANUAL_REVIEW"


def test_prepare_refund_does_not_execute_before_confirmation():
    store, service = make_service()
    action = service.prepare_refund("demo-user", "ORD-10001", "不再需要")
    assert action.status is ActionStatus.AWAITING_CONFIRMATION
    assert action.confirmation_required is True
    assert store.fetch_all("SELECT * FROM refund_requests") == []


def test_refund_confirmation_is_idempotent():
    store, service = make_service()
    first = service.prepare_refund(
        "demo-user", "ORD-10001", "不再需要", idempotency_key="refund-demo-1"
    )
    duplicate = service.prepare_refund(
        "demo-user", "ORD-10001", "不再需要", idempotency_key="refund-demo-1"
    )
    assert duplicate.action_id == first.action_id

    confirmed = service.confirm_action("demo-user", first.action_id)
    repeated = service.confirm_action("demo-user", first.action_id)
    assert confirmed.status is ActionStatus.SUCCEEDED
    assert repeated.status is ActionStatus.SUCCEEDED
    assert repeated.resource_id == confirmed.resource_id
    assert len(store.fetch_all("SELECT * FROM refund_requests")) == 1
    assert len(store.fetch_all("SELECT * FROM audit_events WHERE event_type='REFUND_CREATED'")) == 1


def test_other_user_cannot_confirm_action():
    store, service = make_service()
    action = service.prepare_refund("demo-user", "ORD-10001", "不再需要")
    result = service.confirm_action("other-user", action.action_id)
    assert result.status is ActionStatus.FAILED
    assert store.fetch_all("SELECT * FROM refund_requests") == []


def test_cancel_order_requires_confirmation_and_is_idempotent():
    store, service = make_service()
    action = service.prepare_cancel_order("demo-user", "ORD-10001", "不需要了", "cancel-1")
    duplicate = service.prepare_cancel_order("demo-user", "ORD-10001", "不需要了", "cancel-1")

    assert action.status is ActionStatus.AWAITING_CONFIRMATION
    assert duplicate.action_id == action.action_id
    assert service.get_order("demo-user", "ORD-10001").data["status"] == "paid"

    confirmed = service.confirm_action("demo-user", action.action_id)
    repeated = service.confirm_action("demo-user", action.action_id)
    assert confirmed.status is ActionStatus.SUCCEEDED
    assert repeated.status is ActionStatus.SUCCEEDED
    assert service.get_order("demo-user", "ORD-10001").data["status"] == "cancelled"
    assert len(store.fetch_all("SELECT * FROM audit_events WHERE event_type='ORDER_CANCELLED'")) == 1


def test_shipped_order_cannot_be_cancelled_or_have_address_changed():
    _, service = make_service()
    cancel = service.prepare_cancel_order("demo-user", "ORD-10002")
    address = service.prepare_address_change("demo-user", "ORD-10002", "上海市徐汇区测试路 8 号")

    assert cancel.status is ActionStatus.FAILED
    assert address.status is ActionStatus.FAILED


def test_address_change_requires_confirmation_and_hides_address_from_audit():
    store, service = make_service()
    action = service.prepare_address_change("demo-user", "ORD-10005", "上海市徐汇区测试路 8 号", "address-1")

    assert action.status is ActionStatus.AWAITING_CONFIRMATION
    before = service.get_order("demo-user", "ORD-10005").data["address"]
    assert before != "上海市徐汇区测试路 8 号"

    confirmed = service.confirm_action("demo-user", action.action_id)
    assert confirmed.status is ActionStatus.SUCCEEDED
    assert service.get_order("demo-user", "ORD-10005").data["address"] == "上海市徐汇区测试路 8 号"
    audit = store.fetch_all("SELECT * FROM audit_events WHERE event_type='CHANGE_ADDRESS_PREPARED'")
    assert "测试路" not in audit[0]["detail_json"]


def test_handoff_ticket_is_persistent_owned_and_idempotent():
    _, service = make_service()
    first = service.create_handoff_ticket(
        "demo-user", "conv-1", "req-1", "用户要求人工", "human_handoff", "HIGH", {"message": "需要人工"}
    )
    repeated = service.create_handoff_ticket(
        "demo-user", "conv-1", "req-1", "用户要求人工", "human_handoff", "HIGH", {"message": "需要人工"}
    )

    assert first["ticket_id"] == repeated["ticket_id"]
    assert service.get_handoff_ticket("other-user", first["ticket_id"]) is None
    assert service.list_handoff_tickets("demo-user")[0]["status"] == "open"
