import asyncio

from agents.agent_orchestrator import (
    AfterSalesAgent,
    AgentOrchestrator,
    AgentType,
    BillingAgent,
    OrderAgent,
    Request,
)
from agents.tools import build_after_sales_tools, build_commerce_billing_tools, build_order_tools
from commerce.service import CommerceService
from commerce.store import CommerceStore
from core.intent_recognizer import IntentCategory, UrgencyLevel
from core.intent_recognizer import IntentRecognizer


class FakeClient:
    pass


def make_service():
    store = CommerceStore(":memory:")
    store.seed_demo_data()
    return CommerceService(store)


def make_orchestrator_shell():
    orchestrator = AgentOrchestrator.__new__(AgentOrchestrator)
    orchestrator._pool = {
        AgentType.GENERAL: [object()],
        AgentType.ORDER: [object()],
        AgentType.AFTER_SALES: [object()],
        AgentType.BILLING: [object()],
        AgentType.TECHNICAL: [object()],
    }
    return orchestrator


def request(message, intent, entities=None):
    return Request(
        message=message,
        user_id="demo-user",
        conv_id="c1",
        intent=intent,
        intent_group="billing",
        urgency=UrgencyLevel.LOW,
        intent_confidence=0.9,
        entities=entities or {},
    )


def test_plain_refund_routes_to_after_sales_without_unneeded_billing_agent():
    decision = make_orchestrator_shell()._route_decision(
        request("订单 ORD-10001 不想要了，申请退款", IntentCategory.REFUND, {"order_id": ["ORD-10001"]})
    )
    assert decision.primary_agent is AgentType.AFTER_SALES
    assert AgentType.BILLING not in decision.supporting_agents


def test_order_and_duplicate_payment_request_routes_to_two_domains():
    decision = make_orchestrator_shell()._route_decision(
        request(
            "订单 ORD-10005 还没发货，而且扣了两次款",
            IntentCategory.ORDER_STATUS,
            {"order_id": ["ORD-10005"], "amount": ["299元"]},
        )
    )
    assert decision.primary_agent is AgentType.ORDER
    assert AgentType.BILLING in decision.supporting_agents


def test_role_tool_sets_are_isolated():
    service = make_service()
    order = OrderAgent(FakeClient(), "test")
    after_sales = AfterSalesAgent(FakeClient(), "test")
    billing = BillingAgent(FakeClient(), "test")
    order.set_domain_tools(build_order_tools(service))
    after_sales.set_domain_tools(build_after_sales_tools(service))
    billing.set_domain_tools(build_commerce_billing_tools(service))

    assert "get_order" in order.get_tools()
    assert "prepare_refund_request" not in order.get_tools()
    assert {"prepare_cancel_order", "prepare_address_change"} <= set(order.get_tools())
    assert "prepare_refund_request" in after_sales.get_tools()
    assert "get_payment_records" not in after_sales.get_tools()
    assert "get_payment_records" in billing.get_tools()
    assert "prepare_refund_request" not in billing.get_tools()


def test_prepare_refund_tool_only_returns_pending_confirmation():
    service = make_service()
    agent = AfterSalesAgent(FakeClient(), "test")
    agent.set_domain_tools(build_after_sales_tools(service))
    tool = agent.get_tools()["prepare_refund_request"]
    result = tool.handler(
        request("申请退款", IntentCategory.REFUND, {"order_id": ["ORD-10001"]}),
        {"order_id": "ORD-10001", "reason": "不再需要"},
    )
    assert result["status"] == "awaiting_confirmation"
    assert result["confirmation_required"] is True


def test_order_tool_minimizes_personal_data_before_llm():
    tool = build_order_tools(make_service())["get_order"]
    result = tool.handler(
        request("查询订单", IntentCategory.ORDER_STATUS, {"order_id": ["ORD-10002"]}),
        {"order_id": "ORD-10002"},
    )

    assert result["success"] is True
    assert "user_id" not in result["data"]
    assert "address" not in result["data"]
    assert result["data"]["status"] == "shipped"


def test_ecommerce_patterns_choose_specific_intents():
    recognizer = IntentRecognizer.__new__(IntentRecognizer)
    assert recognizer._pattern_recognize("订单 ORD-10005 被扣了两次款")["intent"] is IntentCategory.DUPLICATE_PAYMENT
    assert recognizer._pattern_recognize("收到的包裹少件了")["intent"] is IntentCategory.DAMAGED_ITEM
    assert recognizer._pattern_recognize("订单地址填错了")["intent"] is IntentCategory.ADDRESS_CHANGE
    assert recognizer._pattern_recognize("退款多久到账？")["intent"] is IntentCategory.REFUND_STATUS


def test_order_id_entity_supports_demo_order_format():
    recognizer = IntentRecognizer.__new__(IntentRecognizer)
    entities = recognizer._extract_entities("查询订单 ORD-10005 的支付记录")
    assert entities["order_id"] == ["ORD-10005"]


def test_agent_exposes_pending_action_as_structured_result():
    class ToolUseBlock:
        type = "tool_use"
        id = "tool_refund_1"
        name = "prepare_refund_request"
        input = {"order_id": "ORD-10001", "reason": "不再需要"}

    class TextBlock:
        type = "text"
        text = "退款动作已准备，请确认后执行。"

    class Client:
        def __init__(self):
            self.responses = [
                type("Response", (), {"content": [ToolUseBlock()]})(),
                type("Response", (), {"content": [TextBlock()]})(),
            ]

        class Messages:
            def __init__(self, owner):
                self.owner = owner

            async def create(self, **kwargs):
                return self.owner.responses.pop(0)

        @property
        def messages(self):
            return self.Messages(self)

    agent = AfterSalesAgent(Client(), "test")
    agent.set_domain_tools(build_after_sales_tools(make_service()))
    response = asyncio.run(agent.handle(
        request("订单 ORD-10001 申请退款", IntentCategory.REFUND, {"order_id": ["ORD-10001"]})
    ))
    assert response.success is True
    assert response.pending_actions[0]["status"] == "awaiting_confirmation"
    assert response.pending_actions[0]["action_id"].startswith("act_")
    assert response.tool_traces[0]["tool_name"] == "prepare_refund_request"
