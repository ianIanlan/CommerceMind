import asyncio

from agents.agent_orchestrator import BaseAgent, Request
from agents.tools import build_shared_rag_tools
from core.intent_recognizer import IntentCategory
from mcp.local_knowledge_base import LocalKnowledgeBase
from mcp.knowledge_base import KnowledgeBase
from mcp.tool_manager import MCPToolManager, ToolResult


def test_local_retrieval_filters_by_business_category():
    kb = LocalKnowledgeBase()

    results = kb.search("订单退款支付", top_k=5, category="after_sales")

    assert results
    assert {item["category"] for item in results} <= {"after_sales", "general"}
    assert all(item["document_id"] for item in results)


def test_squared_l2_distance_is_converted_to_bounded_similarity():
    assert KnowledgeBase._distance_to_similarity(0.0) == 1.0
    assert KnowledgeBase._distance_to_similarity(1.5) == 0.25
    assert KnowledgeBase._distance_to_similarity(4.0) == 0.0


def test_rag_sources_are_minimal_deduplicated_citations():
    result = {
        "results": [
            {
                "document_id": "refund-general:2.0:0",
                "title": "退款政策",
                "content": "这段正文不应进入引用元数据",
                "policy_id": "refund-general",
                "version": "2.0",
                "category": "after_sales",
                "source": "policy_center",
                "score": 0.91,
            },
            {
                "document_id": "refund-general:2.0:0",
                "title": "退款政策",
                "policy_id": "refund-general",
                "version": "2.0",
            },
        ]
    }

    sources = BaseAgent._knowledge_sources(result)

    assert len(sources) == 1
    assert sources[0]["document_id"] == "refund-general:2.0:0"
    assert "content" not in sources[0]


def test_multi_query_recall_deduplicates_by_stable_document_id(monkeypatch):
    manager = MCPToolManager(api_key="test", model="test")

    async def rewrite(_query, n=3):
        return ["退款", "退货"]

    async def call(_name, params, context=None, use_cache=True):
        return ToolResult(
            success=True,
            data=[{
                "document_id": "refund-general:2.0:0",
                "title": "退款政策",
                "content": f"来自查询：{params['query']}",
            }],
            tool_name="knowledge_search",
        )

    async def rerank(_query, items, top_k):
        return items[:top_k]

    monkeypatch.setattr(manager, "rewrite_query", rewrite)
    monkeypatch.setattr(manager, "call", call)
    monkeypatch.setattr(manager, "_rerank", rerank)

    result = asyncio.run(manager.search_with_rewrite("knowledge_search", "退款政策", top_k=5))

    assert result.success is True
    assert len(result.data) == 1


def test_return_exchange_uses_after_sales_knowledge_domain():
    class CapturingManager:
        context = None

        async def search_with_rewrite(self, _tool_name, _query, top_k=5, context=None):
            self.context = context
            return ToolResult(True, [], "knowledge_search", reranked=True)

    manager = CapturingManager()
    spec = build_shared_rag_tools(manager)["search_knowledge_base"]
    request = Request(
        message="普通商品几天可以退货",
        user_id="u1",
        conv_id="c1",
        intent=IntentCategory.RETURN_EXCHANGE,
        intent_group="billing",
    )

    asyncio.run(spec.handler(request, {"query": request.message}))

    assert manager.context == {"category": "after_sales"}


def test_refund_status_uses_after_sales_policy_domain():
    class CapturingManager:
        context = None

        async def search_with_rewrite(self, _tool_name, _query, top_k=5, context=None):
            self.context = context
            return ToolResult(True, [], "knowledge_search", reranked=True)

    manager = CapturingManager()
    spec = build_shared_rag_tools(manager)["search_knowledge_base"]
    request = Request(
        message="退款多久到账",
        user_id="u1",
        conv_id="c1",
        intent=IntentCategory.REFUND_STATUS,
        intent_group="billing",
    )

    asyncio.run(spec.handler(request, {"query": request.message}))

    assert manager.context == {"category": "after_sales"}
