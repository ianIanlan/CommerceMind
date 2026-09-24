"""Agent 工具定义与实现。

所有 Agent 工具集中在这里，编排器只负责：
  1. 根据 Agent 类型暴露工具白名单
  2. 执行 LLM 返回的 tool_use
  3. 将工具结果回传给 LLM

工具本身保持确定性、可测试，并明确区分：
  - 当前请求分析
  - 技术排障建议
  - 账单字段核验
  - 人工升级摘要
  - 共享知识库 RAG

订单查询、退款执行、账单修改等需要真实业务系统授权的动作不在这里伪造。
"""

from __future__ import annotations

import json
from dataclasses import asdict
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, List, Optional, TYPE_CHECKING, Union

if TYPE_CHECKING:
    from agents.agent_orchestrator import Request


AgentToolHandler = Callable[["Request", Dict[str, Any]], Union[Any, Awaitable[Any]]]


@dataclass(frozen=True)
class AgentToolSpec:
    """Agent 可见工具的定义和执行函数。"""

    name: str
    description: str
    input_schema: Dict[str, Any]
    handler: AgentToolHandler


def make_tool(
    name: str,
    description: str,
    properties: Dict[str, Any],
    handler: AgentToolHandler,
    required: Optional[List[str]] = None,
) -> AgentToolSpec:
    """创建带 JSON Schema 的 Agent 工具。"""
    return AgentToolSpec(
        name=name,
        description=description,
        input_schema={
            "type": "object",
            "properties": properties,
            "required": required or [],
            "additionalProperties": False,
        },
        handler=handler,
    )


def inspect_request_context(req: Request, args: Dict[str, Any]) -> Dict[str, Any]:
    """通用客服工具：返回脱敏后的当前请求快照。"""
    return {
        "intent": req.intent.value if req.intent else None,
        "intent_group": req.intent_group,
        "urgency": req.urgency.name if req.urgency else None,
        "intent_confidence": round(req.intent_confidence, 4),
        "entities": req.entities or {},
        "context_available": bool(req.context),
        "requested_focus": str(args.get("focus", "general"))[:40],
    }


def suggest_required_fields(req: Request, args: Dict[str, Any]) -> Dict[str, Any]:
    """通用客服工具：按业务类型计算下一轮只需询问的字段。"""
    intent = req.intent.value if req.intent else "other"
    fields: List[str] = []
    if intent in {"order_status", "logistics"}:
        fields = ["订单号或下单时间"]
    elif intent in {"account", "account_security"}:
        fields = ["登录方式或账号标识", "问题发生时间"]
    elif intent in {"complaint", "request"}:
        fields = ["事件时间", "期望处理方式"]
    elif intent == "other":
        fields = ["希望解决的具体问题"]
    return {
        "intent": intent,
        "required_fields": fields,
        "known_entities": req.entities or {},
    }


def lookup_error_code(req: Request, args: Dict[str, Any]) -> Dict[str, Any]:
    """技术工具：解释常见错误码的排查方向，不声称读取了服务端日志。"""
    code = str(args.get("error_code", "")).upper().strip()
    mapping = {
        "401": ("认证失败", ["确认 Token/API Key 是否过期", "确认请求时间戳和签名", "确认账号登录状态"]),
        "403": ("权限不足", ["确认账号或套餐权限", "确认资源权限和 IP 白名单"]),
        "404": ("资源或路径不存在", ["确认接口路径和环境", "确认资源标识是否正确"]),
        "500": ("服务端处理异常", ["记录 request_id 和发生时间", "检查依赖服务、参数格式和服务端日志"]),
    }
    meaning, steps = mapping.get(
        code,
        ("暂未识别的错误码", ["补充完整错误信息、发生时间和运行环境"]),
    )
    return {
        "error_code": code,
        "meaning": meaning,
        "next_steps": steps,
        "server_log_checked": False,
    }


def build_diagnostic_plan(req: Request, args: Dict[str, Any]) -> Dict[str, Any]:
    """技术工具：生成低风险排障顺序。"""
    environment = str(args.get("environment", "unknown"))[:80]
    reproduced = bool(args.get("reproduced", False))
    steps = [
        "复现并记录完整错误信息",
        "确认网络、DNS、代理和证书",
        "确认版本、配置和权限",
    ]
    if reproduced:
        steps.append("用最小请求复现并记录 request_id")
    return {
        "environment": environment,
        "reproduced": reproduced,
        "diagnostic_steps": steps,
    }


def check_billing_fields(req: Request, args: Dict[str, Any]) -> Dict[str, Any]:
    """账单工具：检查必要核验字段是否齐全。"""
    fields = {
        "order_id": bool(req.entities.get("order_id")),
        "amount": bool(req.entities.get("amount")),
        "date": bool(req.entities.get("date")),
        "payment_channel": bool(args.get("payment_channel")),
    }
    return {
        "fields": fields,
        "missing_fields": [name for name, present in fields.items() if not present],
        "can_confirm_refund": False,
        "reason": "当前工具只做字段检查，不连接订单或支付系统",
    }


def compare_amounts(req: Request, args: Dict[str, Any]) -> Dict[str, Any]:
    """账单工具：只做用户明确提供金额之间的算术。"""
    try:
        first = float(args["amount_a"])
        second = float(args["amount_b"])
    except (KeyError, TypeError, ValueError):
        return {"success": False, "error": "amount_a 和 amount_b 必须是数字"}
    return {
        "success": True,
        "amount_a": first,
        "amount_b": second,
        "difference": round(first - second, 2),
        "interpretation": "仅表示金额差值，不代表重复扣款或退款结论",
    }


def create_handoff_summary(req: Request, args: Dict[str, Any]) -> Dict[str, Any]:
    """升级工具：生成可交给人工客服的结构化摘要。"""
    return {
        "request_id": req.request_id,
        "reason": str(args.get("reason", "需要人工客服继续核验"))[:120],
        "intent": req.intent.value if req.intent else "unknown",
        "urgency": req.urgency.name if req.urgency else "UNKNOWN",
        "entities": req.entities or {},
        "sensitive_data_required": False,
    }


def build_shared_rag_tools(tool_manager: Any) -> Dict[str, AgentToolSpec]:
    """构建所有 Agent 可共享的 RAG 工具。"""

    def knowledge_category(req: Request) -> str:
        intent = req.intent.value if req.intent else ""
        # 退款到账时效既属于账单意图，也依赖售后退款政策；知识域应落到
        # after_sales，否则严格分类过滤会把“退款政策”文档全部排除。
        if intent in {"refund", "refund_status", "return_exchange"}:
            return "after_sales"
        if intent in {"order_status", "logistics", "cancel_order", "change_address"}:
            return "order"
        if intent in {"billing", "invoice", "payment_issue"}:
            return "billing"
        if intent in {"technical", "technical_login", "technical_crash", "account_security"}:
            return "technical"
        return req.intent_group or ""

    async def search_knowledge_base(req: Request, args: Dict[str, Any]) -> Dict[str, Any]:
        query = str(args.get("query") or req.message or "").strip()
        top_k = int(args.get("top_k", 5) or 5)
        if not query:
            return {"success": False, "error": "query 不能为空", "results": []}
        if tool_manager is None:
            return {"success": False, "error": "RAG 工具未初始化", "results": []}

        result = await tool_manager.search_with_rewrite(
            "knowledge_search",
            query,
            top_k=top_k,
            context={"category": knowledge_category(req)},
        )
        if not getattr(result, "success", False):
            return {
                "success": False,
                "query": query,
                "error": getattr(result, "error", "知识库检索失败"),
                "results": [],
                "reranked": False,
            }

        return {
            "success": True,
            "query": query,
            "top_k": top_k,
            "results": result.data,
            "reranked": bool(getattr(result, "reranked", False)),
        }

    return {
        "search_knowledge_base": make_tool(
            "search_knowledge_base",
            "检索知识库并返回最相关的文档片段；可用于通用、技术、账单和升级场景。",
            {
                "query": {"type": "string", "description": "用户问题或检索关键词"},
                "top_k": {"type": "integer", "description": "返回结果条数"},
            },
            search_knowledge_base,
            required=["query"],
        )
    }


def _serialize_domain_result(value: Any) -> Dict[str, Any]:
    """把领域 dataclass/Enum 安全转换为可回传给模型的 JSON 数据。"""
    if hasattr(value, "__dataclass_fields__"):
        value = asdict(value)
    if not isinstance(value, dict):
        return {"success": True, "data": value}

    def convert(item: Any) -> Any:
        if hasattr(item, "value"):
            return item.value
        if isinstance(item, dict):
            return {key: convert(val) for key, val in item.items()}
        if isinstance(item, list):
            return [convert(val) for val in item]
        return item

    return convert(value)


def build_order_tools(commerce_service: Any) -> Dict[str, AgentToolSpec]:
    """订单/物流 Agent 的实际工具集合。"""

    def get_order(req: Request, args: Dict[str, Any]) -> Dict[str, Any]:
        result = _serialize_domain_result(commerce_service.get_order(
            req.user_id, str(args["order_id"]), request_id=req.request_id,
        ))
        # 订单归属由服务端完成校验；模型不需要看到用户标识和完整地址。
        # 地址修改等独立场景应使用专门的最小权限工具，而不是复用订单全量对象。
        data = result.get("data")
        if isinstance(data, dict):
            data.pop("user_id", None)
            data.pop("address", None)
        return result

    def get_logistics(req: Request, args: Dict[str, Any]) -> Dict[str, Any]:
        return _serialize_domain_result(commerce_service.get_logistics(
            req.user_id, str(args["order_id"]), request_id=req.request_id,
        ))

    def prepare_cancel_order(req: Request, args: Dict[str, Any]) -> Dict[str, Any]:
        return _serialize_domain_result(commerce_service.prepare_cancel_order(
            req.user_id, str(args["order_id"]), str(args.get("reason") or "用户申请取消"),
            idempotency_key=str(args.get("idempotency_key") or "") or None, request_id=req.request_id,
        ))

    def prepare_address_change(req: Request, args: Dict[str, Any]) -> Dict[str, Any]:
        return _serialize_domain_result(commerce_service.prepare_address_change(
            req.user_id, str(args["order_id"]), str(args["new_address"]),
            idempotency_key=str(args.get("idempotency_key") or "") or None, request_id=req.request_id,
        ))

    return {
        "get_order": make_tool(
            "get_order",
            "查询当前用户的订单事实。只有工具返回成功后，才能声称查到订单状态。",
            {"order_id": {"type": "string", "description": "订单号，例如 ORD-10001"}},
            get_order,
            required=["order_id"],
        ),
        "get_logistics": make_tool(
            "get_logistics",
            "查询当前用户订单的物流轨迹；没有物流记录时返回明确错误。",
            {"order_id": {"type": "string", "description": "订单号"}},
            get_logistics,
            required=["order_id"],
        ),
        "prepare_cancel_order": make_tool(
            "prepare_cancel_order", "核验订单状态并创建待用户确认的取消动作，不直接取消订单。",
            {"order_id": {"type": "string"}, "reason": {"type": "string"}, "idempotency_key": {"type": "string"}},
            prepare_cancel_order, required=["order_id"],
        ),
        "prepare_address_change": make_tool(
            "prepare_address_change", "核验订单状态并创建待用户确认的地址修改动作，不直接修改地址。",
            {"order_id": {"type": "string"}, "new_address": {"type": "string"}, "idempotency_key": {"type": "string"}},
            prepare_address_change, required=["order_id", "new_address"],
        ),
    }


def build_after_sales_tools(commerce_service: Any) -> Dict[str, AgentToolSpec]:
    """售后 Agent 工具。prepare 只创建待确认动作，不直接退款。"""

    def check_return_eligibility(req: Request, args: Dict[str, Any]) -> Dict[str, Any]:
        return _serialize_domain_result(commerce_service.check_return_eligibility(
            req.user_id, str(args["order_id"]),
        ))

    def prepare_refund_request(req: Request, args: Dict[str, Any]) -> Dict[str, Any]:
        return _serialize_domain_result(commerce_service.prepare_refund(
            req.user_id,
            str(args["order_id"]),
            str(args.get("reason") or "用户申请退款"),
            idempotency_key=str(args.get("idempotency_key") or "") or None,
            request_id=req.request_id,
        ))

    return {
        "check_return_eligibility": make_tool(
            "check_return_eligibility",
            "根据订单状态和商品类型执行基础退款资格预检；结果不等于退款已经成功。",
            {"order_id": {"type": "string", "description": "订单号"}},
            check_return_eligibility,
            required=["order_id"],
        ),
        "prepare_refund_request": make_tool(
            "prepare_refund_request",
            "预检退款并创建等待用户确认的动作。此工具绝不直接执行退款。",
            {
                "order_id": {"type": "string", "description": "订单号"},
                "reason": {"type": "string", "description": "退款原因"},
                "idempotency_key": {"type": "string", "description": "可选幂等键"},
            },
            prepare_refund_request,
            required=["order_id", "reason"],
        ),
    }


def build_commerce_billing_tools(commerce_service: Any) -> Dict[str, AgentToolSpec]:
    """支付和退款状态查询工具；均为只读。"""

    def get_payment_records(req: Request, args: Dict[str, Any]) -> Dict[str, Any]:
        return _serialize_domain_result(commerce_service.get_payment_records(
            req.user_id, str(args["order_id"]), request_id=req.request_id,
        ))

    def get_refund_status(req: Request, args: Dict[str, Any]) -> Dict[str, Any]:
        return _serialize_domain_result(commerce_service.get_refund_status(
            req.user_id, str(args["order_id"]), request_id=req.request_id,
        ))

    return {
        "get_payment_records": make_tool(
            "get_payment_records",
            "查询当前用户订单的支付流水。重复候选只表示需要核验，不会自动退款。",
            {"order_id": {"type": "string", "description": "订单号"}},
            get_payment_records,
            required=["order_id"],
        ),
        "get_refund_status": make_tool(
            "get_refund_status",
            "查询当前用户订单的退款申请及真实处理状态。",
            {"order_id": {"type": "string", "description": "订单号"}},
            get_refund_status,
            required=["order_id"],
        ),
    }


def general_tools() -> Dict[str, AgentToolSpec]:
    return {
        "inspect_request_context": make_tool(
            "inspect_request_context",
            "查看当前请求的意图、紧急度、实体和上下文可用性；不查询外部业务系统。",
            {"focus": {"type": "string", "description": "希望关注的业务方向"}},
            inspect_request_context,
        ),
        "suggest_required_fields": make_tool(
            "suggest_required_fields",
            "根据当前意图建议下一轮只需向用户补充的字段。",
            {},
            suggest_required_fields,
        ),
    }


def technical_tools() -> Dict[str, AgentToolSpec]:
    return {
        "lookup_error_code": make_tool(
            "lookup_error_code",
            "解释常见 HTTP 错误码的可能含义和低风险排查方向；不会读取服务端日志。",
            {"error_code": {"type": "string", "description": "例如 401、403、500"}},
            lookup_error_code,
            required=["error_code"],
        ),
        "build_diagnostic_plan": make_tool(
            "build_diagnostic_plan",
            "根据运行环境和是否可复现生成排障顺序，不执行修改配置等操作。",
            {
                "environment": {"type": "string", "description": "App、浏览器、服务端或 Docker 等"},
                "reproduced": {"type": "boolean", "description": "问题是否可以稳定复现"},
            },
            build_diagnostic_plan,
            required=["environment", "reproduced"],
        ),
    }


def billing_tools() -> Dict[str, AgentToolSpec]:
    return {
        "check_billing_fields": make_tool(
            "check_billing_fields",
            "检查账单核验字段是否齐全；不连接订单、支付或退款系统。",
            {"payment_channel": {"type": "string", "description": "支付渠道，例如微信、支付宝、银行卡"}},
            check_billing_fields,
        ),
        "compare_amounts": make_tool(
            "compare_amounts",
            "计算用户明确提供的两笔金额差值；不判断是否重复扣款，也不执行退款。",
            {
                "amount_a": {"type": "number", "description": "第一笔金额"},
                "amount_b": {"type": "number", "description": "第二笔金额"},
            },
            compare_amounts,
            required=["amount_a", "amount_b"],
        ),
    }


def escalation_tools(commerce_service: Any = None) -> Dict[str, AgentToolSpec]:
    tools = {
        "create_handoff_summary": make_tool(
            "create_handoff_summary",
            "生成交给人工客服的结构化交接摘要，不会创建真实工单。",
            {"reason": {"type": "string", "description": "需要升级的原因"}},
            create_handoff_summary,
        ),
    }
    if commerce_service is not None:
        def create_handoff_ticket(req: Request, args: Dict[str, Any]) -> Dict[str, Any]:
            return commerce_service.create_handoff_ticket(
                req.user_id, req.conv_id, req.request_id, str(args.get("reason") or "用户要求人工客服"),
                req.intent.value if req.intent else "unknown", req.urgency.name if req.urgency else "UNKNOWN",
                {"entities": req.entities or {}, "message": req.message[:500]},
            )

        tools["create_handoff_ticket"] = make_tool(
            "create_handoff_ticket", "创建并持久化人工客服工单，相同 request_id 不重复创建。",
            {"reason": {"type": "string"}}, create_handoff_ticket,
        )
    return tools
