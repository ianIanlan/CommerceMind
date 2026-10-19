from urllib.parse import parse_qs

import httpx

from commerce.models import ActionStatus
from commerce.payment_gateway import GatewayRefund, StripePaymentGateway
from commerce.service import CommerceService
from commerce.store import CommerceStore


class FakeGateway:
    def __init__(self, success=True):
        self.success = success
        self.calls = []

    def refund(self, payment_reference, amount, idempotency_key, metadata=None):
        self.calls.append((payment_reference, amount, idempotency_key, metadata))
        if not self.success:
            return GatewayRefund(False, None, "failed", "provider_down", "temporary failure")
        return GatewayRefund(True, "re_test_123", "succeeded")


class TimeoutAfterCommitGateway:
    """Simulates a provider commit followed by a lost HTTP response."""

    def __init__(self):
        self.calls = []
        self.provider_refunds = {}

    def refund(self, payment_reference, amount, idempotency_key, metadata=None):
        self.calls.append(idempotency_key)
        refund_id = self.provider_refunds.setdefault(idempotency_key, "re_timeout_committed")
        if len(self.calls) == 1:
            return GatewayRefund(
                False, None, "unknown", "PROVIDER_OUTCOME_UNKNOWN", "read timeout", True
            )
        return GatewayRefund(True, refund_id, "succeeded")


def test_stripe_adapter_sends_minor_units_and_idempotency_key():
    observed = {}

    def handler(request: httpx.Request):
        observed["headers"] = request.headers
        observed["form"] = parse_qs(request.content.decode())
        return httpx.Response(200, json={"id": "re_123", "status": "succeeded"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    gateway = StripePaymentGateway("sk_test_example", client=client)

    result = gateway.refund("pi_123", 12.34, "act_123", {"order_id": "ORD-1"})

    assert result.success is True
    assert result.refund_id == "re_123"
    assert observed["headers"]["idempotency-key"] == "act_123"
    assert observed["form"]["payment_intent"] == ["pi_123"]
    assert observed["form"]["amount"] == ["1234"]
    assert observed["form"]["metadata[order_id]"] == ["ORD-1"]


def test_stripe_timeout_is_reported_as_unknown_not_failed():
    def handler(request: httpx.Request):
        raise httpx.ReadTimeout("response lost after request", request=request)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    gateway = StripePaymentGateway("sk_test_example", client=client)

    result = gateway.refund("pi_123", 12.34, "act_timeout")

    assert result.success is False
    assert result.status == "unknown"
    assert result.error_code == "PROVIDER_OUTCOME_UNKNOWN"
    assert result.outcome_unknown is True


def test_confirmed_stripe_refund_calls_provider_once_and_persists_provider_id():
    store = CommerceStore(":memory:")
    store.seed_demo_data()
    store.execute(
        "UPDATE payments SET payment_id=?, channel='stripe' WHERE order_id=?",
        ("pi_test_123", "ORD-10001"),
    )
    gateway = FakeGateway()
    service = CommerceService(store, payment_gateway=gateway)
    action = service.prepare_refund("demo-user", "ORD-10001", "测试 Stripe 退款", "stripe-refund-1")

    first = service.confirm_action("demo-user", action.action_id)
    repeated = service.confirm_action("demo-user", action.action_id)

    assert first.status is ActionStatus.SUCCEEDED
    assert first.resource_id == "re_test_123"
    assert repeated.resource_id == "re_test_123"
    assert len(gateway.calls) == 1
    assert gateway.calls[0][2] == action.action_id
    persisted = store.fetch_one("SELECT * FROM refund_requests WHERE refund_id=?", ("re_test_123",))
    assert persisted["status"] == "succeeded"


def test_provider_failure_never_reports_refund_success():
    store = CommerceStore(":memory:")
    store.seed_demo_data()
    store.execute(
        "UPDATE payments SET payment_id=?, channel='stripe' WHERE order_id=?",
        ("pi_test_456", "ORD-10001"),
    )
    gateway = FakeGateway(success=False)
    service = CommerceService(store, payment_gateway=gateway)
    action = service.prepare_refund("demo-user", "ORD-10001", "失败路径", "stripe-refund-fail")

    result = service.confirm_action("demo-user", action.action_id)

    assert result.status is ActionStatus.FAILED
    assert store.fetch_all("SELECT * FROM refund_requests") == []
    pending = store.fetch_one("SELECT * FROM pending_actions WHERE action_id=?", (action.action_id,))
    assert pending["status"] == ActionStatus.AWAITING_CONFIRMATION.value


def test_timeout_after_provider_commit_requires_reconciliation_and_never_duplicates():
    store = CommerceStore(":memory:")
    store.seed_demo_data()
    store.execute(
        "UPDATE payments SET payment_id=?, channel='stripe' WHERE order_id=?",
        ("pi_test_timeout", "ORD-10001"),
    )
    gateway = TimeoutAfterCommitGateway()
    service = CommerceService(store, payment_gateway=gateway)
    action = service.prepare_refund("demo-user", "ORD-10001", "超时核对", "refund-timeout-1")

    uncertain = service.confirm_action("demo-user", action.action_id)

    assert uncertain.status is ActionStatus.RECONCILIATION_REQUIRED
    assert "不能宣称" in uncertain.message
    assert store.fetch_all("SELECT * FROM refund_requests") == []
    persisted = store.fetch_one("SELECT * FROM pending_actions WHERE action_id=?", (action.action_id,))
    assert persisted["status"] == ActionStatus.RECONCILIATION_REQUIRED.value

    reconciled = service.confirm_action("demo-user", action.action_id)
    repeated = service.confirm_action("demo-user", action.action_id)

    assert reconciled.status is ActionStatus.SUCCEEDED
    assert repeated.resource_id == "re_timeout_committed"
    assert gateway.calls == [action.action_id, action.action_id]
    assert len(gateway.provider_refunds) == 1
    assert len(store.fetch_all("SELECT * FROM refund_requests")) == 1
    assert len(store.fetch_all(
        "SELECT * FROM audit_events WHERE event_type='REFUND_PROVIDER_OUTCOME_UNKNOWN'"
    )) == 1
