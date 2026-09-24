"""Dependency-free knowledge base for the local demo profile."""
import asyncio
import re
from typing import Any, Dict, List


DEFAULT_DOCUMENTS = [
    {
        "title": "退款政策",
        "content": "普通实物商品签收后 7 天内可申请无理由退货退款。虚拟商品、已激活服务和特殊商品需按商品政策人工核验。退款审核通常需要 1-3 个工作日，审核通过后 5-7 个工作日原路退回。",
        "metadata": {"policy_id": "refund-general", "version": "2.0", "category": "after_sales"},
    },
    {
        "title": "订单与物流",
        "content": "订单可通过订单号查询。物流信息通常在发货后 24 小时内更新；已发货超过 7 天未收到可申请查件。修改地址需要在发货前完成。",
        "metadata": {"policy_id": "order-logistics", "version": "1.0", "category": "order"},
    },
    {
        "title": "支付问题",
        "content": "疑似重复扣款需要先核验订单支付流水。系统只能将相同订单、相同金额的多笔成功流水标记为疑似重复扣款，最终结论需支付渠道核验。",
        "metadata": {"policy_id": "payment-review", "version": "1.0", "category": "billing"},
    },
    {
        "title": "账户与登录安全",
        "content": "登录失败 401 通常表示认证失败，应检查登录状态或重置密码。发现异常登录时应立即修改密码并开启两步验证。客服不会索要密码或验证码。",
        "metadata": {"policy_id": "account-security", "version": "1.0", "category": "technical"},
    },
    {
        "title": "会员与积分",
        "content": "每消费 1 元累积 1 积分，100 积分可抵扣 1 元。积分有效期为 1 年，生日当月消费可获得双倍积分。",
        "metadata": {"policy_id": "membership", "version": "1.0", "category": "general"},
    },
    {
        "title": "配送时效",
        "content": "标准配送通常 3-5 个工作日，加急配送通常 1-2 个工作日，偏远地区可能额外增加 2-3 天。订单满 99 元可免标准运费。",
        "metadata": {"policy_id": "delivery", "version": "1.0", "category": "order"},
    },
]


class LocalKnowledgeBase:
    """Small lexical retriever with the same API as KnowledgeBase."""

    def __init__(self):
        self._documents = list(DEFAULT_DOCUMENTS)

    def add_documents(self, documents: List[Dict[str, Any]]) -> int:
        accepted = [doc for doc in documents if str(doc.get("content", "")).strip()]
        self._documents.extend(accepted)
        return len(accepted)

    async def add_documents_async(self, documents: List[Dict[str, Any]]) -> int:
        return self.add_documents(documents)

    @staticmethod
    def _tokens(text: str) -> set[str]:
        words = set(re.findall(r"[a-zA-Z0-9]+", text.lower()))
        chinese = set(re.findall(r"[\u4e00-\u9fff]{2,4}", text))
        return words | chinese

    def search(self, query: str, top_k: int = 5, category: str = "") -> List[Dict[str, Any]]:
        query_tokens = self._tokens(query)
        ranked = []
        for doc in self._documents:
            metadata = doc.get("metadata") or {}
            if category and metadata.get("category") not in {category, "general"}:
                continue
            haystack = f"{doc.get('title', '')} {doc.get('content', '')}"
            overlap = len(query_tokens & self._tokens(haystack))
            keyword_bonus = sum(
                2 for keyword in ("退款", "订单", "物流", "扣款", "支付", "地址")
                if keyword in query and keyword in haystack
            )
            score = overlap + keyword_bonus
            if score:
                ranked.append((score, doc))
        ranked.sort(key=lambda item: item[0], reverse=True)
        results = []
        for score, doc in ranked[:top_k]:
            metadata = doc.get("metadata") or {}
            results.append({
                "title": doc.get("title", ""),
                "content": doc.get("content", ""),
                "score": round(min(0.99, 0.5 + score * 0.08), 4),
                "policy_id": metadata.get("policy_id", "local-demo"),
                "version": metadata.get("version", "1.0"),
                "category": metadata.get("category", "general"),
                "source": "local_demo",
                "document_id": f"{metadata.get('policy_id', 'local-demo')}:{metadata.get('version', '1.0')}:0",
                "chunk": 0,
            })
        return results

    async def search_async(self, query: str, top_k: int = 5, category: str = "") -> List[Dict[str, Any]]:
        return await asyncio.to_thread(self.search, query, top_k, category)

    async def search_handler(self, params: Dict[str, Any], context: Any) -> List[Dict[str, Any]]:
        category = str((context or {}).get("category", ""))
        return await self.search_async(str(params.get("query", "")), int(params.get("top_k", 5)), category)

    @property
    def doc_count(self) -> int:
        return len(self._documents)

    async def doc_count_async(self) -> int:
        return self.doc_count
