"""
亮点：多 Agent 路由与编排

核心问题：多 Agent 情况下如何做 Routing？

路由策略（三层决策）：
  1. 意图路由 —— 根据 IntentCategory 直接映射到专属 Agent
  2. 性能路由 —— 同类 Agent 有多个时，选成功率最高、延迟最低的
  3. 降级路由 —— 专属 Agent 不可用时，自动降级到 GeneralAgent

并行协作：
  - 复杂问题（如"技术问题 + 账单问题"）可同时派发给多个 Agent
  - 结果由 Orchestrator 合并后返回

升级机制：
  - Agent 置信度低于阈值 → 自动升级到更高级 Agent 或转人工
"""
import asyncio
import inspect
import json
import logging
import os
import time
import uuid
from collections import deque
from datetime import datetime
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from anthropic import AsyncAnthropic

from agents.tools import (
    AgentToolSpec,
    build_shared_rag_tools,
    billing_tools,
    build_after_sales_tools,
    build_commerce_billing_tools,
    build_order_tools,
    escalation_tools,
    general_tools,
    technical_tools,
)
from core.intent_recognizer import IntentCategory, IntentRecognizer, UrgencyLevel
from core.llm_utils import extract_text_content
from core.llm_client import build_llm_client

logger = logging.getLogger(__name__)


# ── 数据结构 ──────────────────────────────────────────────────────────────────

class AgentType(Enum):
    GENERAL   = "general"    # 通用客服
    TECHNICAL = "technical"  # 技术支持
    BILLING   = "billing"    # 账单/退款
    ESCALATION = "escalation" # 人工升级与交接
    ORDER = "order"           # 订单与物流
    AFTER_SALES = "after_sales" # 退换货与售后


@dataclass(frozen=True)
class AgentProfile:

    role: str
    mission: str
    workflow: Tuple[str, ...]
    input_contract: Tuple[str, ...]
    output_contract: Tuple[str, ...]
    handoff_conditions: Tuple[str, ...] = ()
    tool_scope: Tuple[str, ...] = ()
    model: Optional[str] = None
    temperature: float = 0.2
    max_tokens: int = 1024


def _env_float(name: str, default: float) -> float:
    """读取可选浮点配置；错误配置不应阻塞服务启动。"""
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        logger.warning("忽略非法浮点配置 %s=%r", name, os.getenv(name))
        return default


def _env_int(name: str, default: int) -> int:
    """读取可选整数配置；错误配置不应阻塞服务启动。"""
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        logger.warning("忽略非法整数配置 %s=%r", name, os.getenv(name))
        return default


@dataclass
class AgentStats:
    """Agent 运行时统计，供 Monitor 和路由决策使用。"""
    total:     int   = 0
    success:   int   = 0
    total_ms:  float = 0.0
    monitor_penalty: float = 0.0

    @property
    def success_rate(self) -> float:
        return self.success / self.total if self.total else 1.0

    @property
    def avg_ms(self) -> float:
        return self.total_ms / self.total if self.total else 0.0

    def routing_score(self) -> float:
        """路由评分：成功率高、延迟低的 Agent 得分高。"""
        latency_score = 1.0 / (1.0 + self.avg_ms / 1000)
        base_score = self.success_rate * 0.7 + latency_score * 0.3
        return base_score * max(0.0, 1.0 - self.monitor_penalty)


@dataclass
class AgentResponse:
    agent_type:  AgentType
    content:     str
    success:     bool
    confidence:  float = 1.0
    latency_ms:  float = 0.0
    escalate:    bool  = False   # 是否需要升级
    tools_used:  List[str] = field(default_factory=list)
    tool_traces: List[Dict[str, Any]] = field(default_factory=list)
    pending_actions: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class Request:
    message:     str
    user_id:     str
    conv_id:     str
    context:     str = ""        # 来自 MemoryManager 的格式化上下文
    history:     Optional[List[Dict[str, str]]] = None  # 对话历史，传给意图识别
    entities:    Dict[str, List[str]] = field(default_factory=dict)
    intent:      Optional[IntentCategory] = None
    intent_group: Optional[str] = None
    urgency:     Optional[UrgencyLevel]   = None
    intent_confidence: float = 1.0
    request_id:  str = field(default_factory=lambda: str(uuid.uuid4())[:8])


@dataclass
class OrchestratorResult:
    request_id:  str
    response:    str
    agent_type:  AgentType
    intent:      Optional[IntentCategory]
    escalated:   bool  = False
    latency_ms:  float = 0.0
    agent_types: List[AgentType] = field(default_factory=list)
    primary_agent: Optional[AgentType] = None
    supporting_agents: List[AgentType] = field(default_factory=list)
    tools_used: List[str] = field(default_factory=list)
    tool_traces: List[Dict[str, Any]] = field(default_factory=list)
    routing_reason: str = ""
    routing_confidence: float = 0.0
    pending_actions: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class RoutingDecision:
    """一次请求的结构化路由决策。"""
    primary_agent: AgentType
    supporting_agents: List[AgentType] = field(default_factory=list)
    reason: str = ""
    confidence: float = 0.0

    @property
    def agent_types(self) -> List[AgentType]:
        return [self.primary_agent] + self.supporting_agents

    @property
    def multi_agent(self) -> bool:
        return bool(self.supporting_agents)


# ── 基础 Agent ────────────────────────────────────────────────────────────────

class BaseAgent:
    """所有 Agent 的基类，封装 LLM 调用、角色契约和统计。"""

    agent_type: AgentType
    system_prompt: str
    profile: AgentProfile

    def __init__(
        self,
        client: AsyncAnthropic,
        model: str,
        skill_manager: Optional[Any] = None,
        profile: Optional[AgentProfile] = None,
    ):
        self._client = client
        self.profile = profile or self.profile
        self._model  = self.profile.model or model
        self._skill_manager = skill_manager
        self.stats   = AgentStats()
        self._last_tools_used: List[str] = []
        self._last_tool_traces: List[Dict[str, Any]] = []
        self._last_pending_actions: List[Dict[str, Any]] = []
        self._shared_tools: Dict[str, AgentToolSpec] = {}
        self._domain_tools: Dict[str, AgentToolSpec] = {}

    def get_tools(self) -> Dict[str, AgentToolSpec]:
        """返回该角色真实可调用的工具白名单。"""
        return {**self._shared_tools, **self._domain_tools}

    def set_shared_tools(self, tools: Optional[Dict[str, AgentToolSpec]]) -> None:
        self._shared_tools = dict(tools or {})

    def set_domain_tools(self, tools: Optional[Dict[str, AgentToolSpec]]) -> None:
        """设置仅当前角色可见的业务工具，形成代码层白名单。"""
        self._domain_tools = dict(tools or {})

    async def handle(self, req: Request) -> AgentResponse:
        t0 = time.monotonic()
        self.stats.total += 1
        self._last_tools_used = []
        self._last_tool_traces = []
        self._last_pending_actions = []
        try:
            if os.getenv("DISABLE_LLM", "false").lower() in {"1", "true", "yes"}:
                content = await self._call_local_demo(req)
            else:
                content = await self._call_llm(req)
            ms = (time.monotonic() - t0) * 1000
            self.stats.success += 1
            self.stats.total_ms += ms
            escalate = self._needs_escalation(content)
            return AgentResponse(
                agent_type=self.agent_type,
                content=content,
                success=True,
                latency_ms=ms,
                escalate=escalate,
                tools_used=list(self._last_tools_used),
                tool_traces=list(self._last_tool_traces),
                pending_actions=list(self._last_pending_actions),
            )
        except Exception as ex:
            ms = (time.monotonic() - t0) * 1000
            self.stats.total_ms += ms
            logger.error(f"{self.agent_type.value} 处理失败: {ex}")
            return AgentResponse(
                agent_type=self.agent_type,
                content="抱歉，处理您的请求时出现问题，请稍后重试。",
                success=False,
                latency_ms=ms,
                tool_traces=list(self._last_tool_traces),
                pending_actions=list(self._last_pending_actions),
            )

    async def _call_local_demo(self, req: Request) -> str:
        """Run deterministic domain tools when the local profile has no valid LLM key."""
        tools = self.get_tools()
        order_ids = req.entities.get("order_id", []) if req.entities else []
        order_id = order_ids[0] if order_ids else ""

        async def invoke(name: str, args: Dict[str, Any]) -> Dict[str, Any]:
            spec = tools.get(name)
            if spec is None:
                return {"success": False, "error": f"工具 {name} 不可用"}
            started = time.monotonic()
            try:
                self._validate_tool_input(spec, args)
                value = spec.handler(req, args)
                if inspect.isawaitable(value):
                    value = await value
                result = value if isinstance(value, dict) else {"success": True, "data": value}
                success = bool(result.get("success", result.get("eligible", True)))
                result.setdefault("success", success)
                self._last_tools_used.append(name)
                self._last_tool_traces.append({
                    "agent_type": self.agent_type.value,
                    "tool_name": name,
                    "input": dict(args),
                    "success": success,
                    "result_success": success,
                    "latency_ms": round((time.monotonic() - started) * 1000, 1),
                    "cached": False,
                    "reranked": False,
                    "error": str(result.get("error") or ""),
                })
                if result.get("status") == "awaiting_confirmation":
                    self._last_pending_actions.append({
                        "action_id": str(result.get("action_id", "")),
                        "action_type": str(result.get("action_type", "")),
                        "status": "awaiting_confirmation",
                        "confirmation_required": True,
                        "message": str(result.get("message", "")),
                        "data": dict(result.get("data") or {}),
                    })
                return result
            except Exception as exc:
                return {"success": False, "error": str(exc)}

        if self.agent_type == AgentType.ORDER:
            if not order_id:
                return "请提供订单号（例如 ORD-10002），我才能核验订单归属并查询进度。"
            order = await invoke("get_order", {"order_id": order_id})
            if not order.get("success"):
                return f"未能查询订单 {order_id}：{order.get('error', '订单不存在或无权访问')}。"
            data = order.get("data") or {}
            lines = [f"订单 {order_id} 当前状态：{data.get('status', '未知')}，商品：{data.get('product_name', '未知')}。"]
            if req.intent == IntentCategory.LOGISTICS or any(word in req.message for word in ("物流", "快递", "配送", "运单")):
                logistics = await invoke("get_logistics", {"order_id": order_id})
                if logistics.get("success"):
                    shipping = logistics.get("data") or {}
                    lines.append(
                        f"承运商：{shipping.get('carrier', '未知')}，运单号：{shipping.get('tracking_no', '未知')}，"
                        f"最新状态：{shipping.get('status', '未知')}。"
                    )
                else:
                    lines.append(f"暂未查到物流信息：{logistics.get('error', '暂无记录')}。")
            return "\n".join(lines)

        if self.agent_type == AgentType.BILLING:
            if not order_id:
                return "请提供订单号，我会先核验订单归属和支付流水。"
            if req.intent == IntentCategory.REFUND_STATUS or "退款" in req.message:
                status = await invoke("get_refund_status", {"order_id": order_id})
                if not status.get("success"):
                    return f"暂未查到订单 {order_id} 的退款记录：{status.get('error', '暂无记录')}。"
                data = status.get("data") or {}
                refunds = data.get("refunds") or []
                if not refunds:
                    return f"订单 {order_id} 暂无退款申请记录。"
                latest = refunds[0]
                return f"订单 {order_id} 的退款状态为 {latest.get('status', '未知')}，退款单号 {latest.get('refund_id', '未知')}。"
            payments = await invoke("get_payment_records", {"order_id": order_id})
            if not payments.get("success"):
                return f"支付流水查询失败：{payments.get('error', '暂无记录')}。"
            data = payments.get("data") or {}
            records = data.get("payments") or []
            if data.get("duplicate_candidate"):
                return f"订单 {order_id} 查到 {len(records)} 笔成功支付流水，属于疑似重复扣款；这不是最终结论，仍需支付渠道或人工复核。"
            return f"订单 {order_id} 查到 {len(records)} 笔支付流水，暂未发现同额重复成功记录。"

        if self.agent_type == AgentType.AFTER_SALES:
            if not order_id:
                return "请提供订单号和退款/退换原因，我会先做售后资格预检。"
            eligibility = await invoke("check_return_eligibility", {"order_id": order_id})
            if not eligibility.get("success"):
                return f"售后资格预检失败：{eligibility.get('error', '订单不符合条件或需要人工核验')}。"
            if "退款" not in req.message and "退货" not in req.message:
                return f"订单 {order_id} 已通过基础售后资格预检；请说明希望退款、退货还是换货。"
            action = await invoke("prepare_refund_request", {"order_id": order_id, "reason": req.message})
            return str(action.get("message") or action.get("error") or "退款预检已完成。")

        if self.agent_type == AgentType.TECHNICAL:
            return "请补充错误提示、发生时间、设备与应用版本；不要提供密码、验证码或完整密钥。"
        if self.agent_type == AgentType.GENERAL:
            return "我可以协助查询订单物流、核验重复扣款、处理退款预检或转人工。请提供订单号和具体诉求。"
        return "已记录你的诉求，请补充订单号和希望处理的结果。"

    async def _call_llm(self, req: Request) -> str:
        def _clean(s: str) -> str:
            return s.encode("utf-8", errors="ignore").decode("utf-8")

        messages = []
        if req.context:
            messages.append({"role": "user", "content": f"[背景信息]\n{_clean(req.context)}"})
            messages.append({"role": "assistant", "content": "好的，我已了解背景信息。"})
        if req.entities:
            entities_text = json.dumps(req.entities, ensure_ascii=False)
            messages.append({"role": "user", "content": f"[结构化实体]\n{_clean(entities_text)}"})
            messages.append({"role": "assistant", "content": "好的，我会结合这些结构化实体处理。"})
        role_packet = self._build_role_packet(req)
        if role_packet:
            messages.append({"role": "user", "content": f"[角色输入契约]\n{_clean(role_packet)}"})
            messages.append({"role": "assistant", "content": "好的，我会按照该角色的输入和输出契约处理。"})
        messages.append({"role": "user", "content": _clean(req.message)})

        tools = self.get_tools()
        tools_used: List[str] = []
        tool_traces: List[Dict[str, Any]] = []
        pending_actions: List[Dict[str, Any]] = []
        for _ in range(3):
            request_kwargs: Dict[str, Any] = {
                "model": self._model,
                "max_tokens": self.profile.max_tokens,
                "temperature": self.profile.temperature,
                "system": self._build_system_prompt(req),
                "messages": messages,
            }
            if tools:
                request_kwargs["tools"] = [
                    {
                        "name": spec.name,
                        "description": spec.description,
                        "input_schema": spec.input_schema,
                    }
                    for spec in tools.values()
                ]
            resp = await self._client.messages.create(**request_kwargs)
            tool_uses = [block for block in (resp.content or []) if self._block_type(block) == "tool_use"]
            if not tool_uses:
                self._last_tools_used = tools_used
                self._last_tool_traces = tool_traces
                self._last_pending_actions = pending_actions
                return extract_text_content(resp.content)

            messages.append({"role": "assistant", "content": resp.content})
            tool_results = []
            for block in tool_uses:
                name = self._block_value(block, "name")
                tool_use_id = self._block_value(block, "id")
                args = self._block_value(block, "input") or {}
                spec = tools.get(name)
                tool_t0 = time.monotonic()
                call_success = True
                result_success: Optional[bool] = None
                error_text = ""
                if spec is None:
                    call_success = False
                    result: Any = {"success": False, "error": f"工具不在 {self.agent_type.value} Agent 白名单中"}
                    error_text = result["error"]
                else:
                    try:
                        self._validate_tool_input(spec, args)
                        result = spec.handler(req, args)
                        if inspect.isawaitable(result):
                            result = await result
                        tools_used.append(name)
                        if isinstance(result, dict) and "success" in result:
                            result_success = bool(result.get("success"))
                        if isinstance(result, dict) and result.get("status") == "awaiting_confirmation":
                            pending_actions.append({
                                "action_id": str(result.get("action_id", "")),
                                "action_type": str(result.get("action_type", "")),
                                "status": "awaiting_confirmation",
                                "confirmation_required": True,
                                "message": str(result.get("message", "")),
                                "data": dict(result.get("data") or {}),
                            })
                    except Exception as ex:
                        call_success = False
                        logger.warning("Agent 工具 %s 执行失败: %s", name, ex)
                        error_text = str(ex)
                        result = {"success": False, "error": error_text}
                tool_latency_ms = (time.monotonic() - tool_t0) * 1000
                if not error_text and isinstance(result, dict):
                    error_text = str(result.get("error", "") or "")
                tool_traces.append(
                    {
                        "agent_type": self.agent_type.value,
                        "tool_name": name,
                        "tool_use_id": tool_use_id,
                        "input": self._trace_safe_input(name, args),
                        "success": call_success,
                        "result_success": result_success,
                        "latency_ms": round(tool_latency_ms, 1),
                        "cached": bool(result.get("cached")) if isinstance(result, dict) else False,
                        "reranked": bool(result.get("reranked")) if isinstance(result, dict) else False,
                        "error": error_text,
                        "knowledge_sources": self._knowledge_sources(result) if name == "search_knowledge_base" else [],
                    }
                )
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tool_use_id,
                    "content": json.dumps(result, ensure_ascii=False),
                })
            messages.append({"role": "user", "content": tool_results})

        self._last_tools_used = tools_used
        self._last_tool_traces = tool_traces
        self._last_pending_actions = pending_actions
        raise RuntimeError(f"{self.agent_type.value} 工具调用超过最大轮数")

    @staticmethod
    def _knowledge_sources(result: Any) -> List[Dict[str, Any]]:
        """从 RAG 工具结果中提取可公开的最小引用信息，不记录整段文档。"""
        items = result.get("results", []) if isinstance(result, dict) else []
        sources: List[Dict[str, Any]] = []
        seen = set()
        for item in items if isinstance(items, list) else []:
            if not isinstance(item, dict) or item.get("fallback"):
                continue
            source = {
                "document_id": str(item.get("document_id") or ""),
                "title": str(item.get("title") or "未命名文档"),
                "policy_id": str(item.get("policy_id") or ""),
                "version": str(item.get("version") or ""),
                "category": str(item.get("category") or "general"),
                "source": str(item.get("source") or "internal"),
                "score": item.get("score"),
            }
            key = source["document_id"] or (source["policy_id"], source["version"], source["title"])
            if key not in seen:
                seen.add(key)
                sources.append(source)
        return sources

    @staticmethod
    def _trace_safe_input(tool_name: str, args: Dict[str, Any]) -> Dict[str, Any]:
        """Trace 保留可调试字段，但不持久化完整地址等个人信息。"""
        safe = dict(args)
        for field in ("new_address", "address", "phone", "email"):
            if field in safe:
                safe[field] = "[REDACTED]"
        return safe

    @staticmethod
    def _block_type(block: Any) -> Optional[str]:
        if isinstance(block, dict):
            return block.get("type")
        return getattr(block, "type", None)

    @staticmethod
    def _block_value(block: Any, key: str) -> Any:
        if isinstance(block, dict):
            return block.get(key)
        return getattr(block, key, None)

    @staticmethod
    def _validate_tool_input(spec: AgentToolSpec, args: Any) -> None:
        if not isinstance(args, dict):
            raise ValueError("工具参数必须是 JSON 对象")
        schema = spec.input_schema
        for field_name in schema.get("required", []):
            if field_name not in args:
                raise ValueError(f"缺少必需参数: {field_name}")
        properties = schema.get("properties", {})
        unknown = set(args) - set(properties)
        if unknown and schema.get("additionalProperties") is False:
            raise ValueError(f"不允许的工具参数: {', '.join(sorted(unknown))}")
        type_map = {"string": str, "number": (int, float), "integer": int, "boolean": bool}
        for key, value in args.items():
            expected = properties.get(key, {}).get("type")
            if expected in type_map and not isinstance(value, type_map[expected]):
                raise ValueError(f"参数 {key} 类型错误，期望 {expected}")

    def _build_system_prompt(self, req: Request) -> str:
        """把角色契约和动态 Skills 拼入 system prompt。"""
        profile_prompt = (
            f"\n\n[角色契约]\n"
            f"角色：{self.profile.role}\n"
            f"职责：{self.profile.mission}\n"
            f"处理流程：{' -> '.join(self.profile.workflow)}\n"
            f"可用输入：{'；'.join(self.profile.input_contract)}\n"
            f"输出要求：{'；'.join(self.profile.output_contract)}\n"
            f"升级条件：{'；'.join(self.profile.handoff_conditions) or '无，按通用客服规则处理'}\n"
            f"允许的数据/工具范围：{'、'.join(self.profile.tool_scope) or '仅使用当前请求上下文'}\n"
            "不要声称执行了未提供的查询、修改或退款操作；缺少证据时明确说明需要核验。"
        )
        base_prompt = f"{self.system_prompt}{profile_prompt}"
        if self._skill_manager is None:
            return base_prompt
        skill_prompt = self._skill_manager.prompt_for(req.message, self.agent_type.value)
        if not skill_prompt:
            return base_prompt
        return f"{base_prompt}\n\n[动态 Skills]\n{skill_prompt}"

    def _build_role_packet(self, req: Request) -> str:
        """给子 Agent 的确定性输入包；子类可补充领域字段。"""
        packet = {
            "agent_type": self.agent_type.value,
            "intent": req.intent.value if req.intent else None,
            "intent_group": req.intent_group,
            "urgency": req.urgency.name if req.urgency else None,
            "intent_confidence": round(req.intent_confidence, 4),
            "available_entities": req.entities or {},
        }
        return json.dumps(packet, ensure_ascii=False)

    def _needs_escalation(self, content: str) -> bool:
        """只识别明确执行/要求升级的表述，避免把“可转人工”建议误判为已升级。"""
        keywords = [
            "已转人工", "已经转人工", "需要转人工处理", "必须转人工",
            "无法处理，需要人工", "escalation required", "escalated to",
        ]
        return any(kw in content for kw in keywords)


class GeneralAgent(BaseAgent):
    agent_type    = AgentType.GENERAL
    profile = AgentProfile(
        role="通用客服分诊与首轮接待",
        mission="快速回答基础问题，澄清不完整需求，并识别是否需要专业 Agent 或人工处理。",
        workflow=("复述诉求", "判断业务范围", "直接回答或补充必要信息", "给出下一步"),
        input_contract=("对话历史", "用户画像", "意图与紧急度", "知识库上下文"),
        output_contract=("先回应核心问题", "信息不足时只询问必要字段", "明确下一步和边界"),
        handoff_conditions=("涉及权限、资金、隐私或复杂投诉", "用户明确要求人工"),
        tool_scope=("search_knowledge_base", "inspect_request_context", "suggest_required_fields"),
        temperature=0.3,
        max_tokens=900,
    )
    system_prompt = (
        "你是 CommerceMind 智能客服。友好、简洁地回答用户问题。"
        "如果问题超出你的能力范围，明确说明并建议转接专业客服。"
    )

    def _build_role_packet(self, req: Request) -> str:
        packet = json.loads(super()._build_role_packet(req))
        packet["triage_targets"] = ["technical", "billing", "escalation"]
        packet["response_mode"] = "answer_or_clarify"
        return json.dumps(packet, ensure_ascii=False)

    def get_tools(self) -> Dict[str, AgentToolSpec]:
        tools = super().get_tools()
        tools.update(general_tools())
        return tools


class TechnicalAgent(BaseAgent):
    agent_type    = AgentType.TECHNICAL
    profile = AgentProfile(
        role="技术故障诊断与排障",
        mission="基于错误码、环境和复现信息缩小根因范围，给出低风险、可验证的排查步骤。",
        workflow=("确认现象", "判断影响范围", "按网络/权限/配置/依赖排查", "给出验证方式", "判断升级条件"),
        input_contract=("错误码", "问题发生时间", "运行环境", "影响范围", "最近变更", "知识库上下文"),
        output_contract=("现象复述", "可能原因", "编号排查步骤", "验证结果", "需要补充的信息"),
        handoff_conditions=("生产大面积不可用", "数据丢失或权限异常", "需要后台日志、数据库或人工操作"),
        tool_scope=("search_knowledge_base", "lookup_error_code", "build_diagnostic_plan"),
        temperature=0.1,
        max_tokens=1200,
    )
    system_prompt = (
        "你是技术支持专家。专注于：故障排查、错误诊断、系统配置。"
        "提供清晰的步骤化解决方案。遇到需要后台操作的问题，说明需要升级处理。"
    )

    def _build_role_packet(self, req: Request) -> str:
        packet = json.loads(super()._build_role_packet(req))
        packet["diagnostic_fields"] = {
            "error_codes": req.entities.get("error_code", []),
            "environment_hint": "请从用户消息和背景中确认设备、系统、版本、网络",
            "risk_boundary": "不得要求密码、验证码、完整密钥；不得建议破坏性操作",
        }
        return json.dumps(packet, ensure_ascii=False)

    def get_tools(self) -> Dict[str, AgentToolSpec]:
        tools = super().get_tools()
        tools.update(technical_tools())
        return tools


class BillingAgent(BaseAgent):
    agent_type    = AgentType.BILLING
    profile = AgentProfile(
        role="账单核验与售后处理",
        mission="区分扣款、退款、发票、订阅等资金场景，解释可判断事实，并明确核验和人工审核边界。",
        workflow=("确认账单场景", "收集必要核验字段", "区分订单/实付/退款金额", "说明处理路径与时效", "判断是否升级"),
        input_contract=("订单号", "金额与币种", "支付时间", "支付渠道", "用户期望", "知识库上下文"),
        output_contract=("需要核验的信息", "当前可判断内容", "下一步处理路径", "时效边界"),
        handoff_conditions=("实际退款或补偿", "重复扣款或支付成功但订单未生效", "发票作废/重开", "企业合同或大额订单"),
        tool_scope=(
            "search_knowledge_base", "check_billing_fields", "compare_amounts",
            "get_payment_records", "get_refund_status",
        ),
        temperature=0.0,
        max_tokens=1100,
    )
    system_prompt = (
        "你是账单服务专家。专注于：账单查询、退款申请、发票问题、订阅管理。"
        "对财务问题保持准确和专业。涉及实际退款操作时，说明需要人工审核。"
    )

    def _build_role_packet(self, req: Request) -> str:
        packet = json.loads(super()._build_role_packet(req))
        packet["verification_fields"] = {
            "order_id": req.entities.get("order_id", []),
            "amount": req.entities.get("amount", []),
            "date": req.entities.get("date", []),
            "missing_fields": [
                field for field, values in (
                    ("订单号或交易号", req.entities.get("order_id", [])),
                    ("支付金额", req.entities.get("amount", [])),
                ) if not values
            ],
            "risk_boundary": "不得承诺退款成功、立即到账或直接修改账单",
        }
        return json.dumps(packet, ensure_ascii=False)

    def get_tools(self) -> Dict[str, AgentToolSpec]:
        tools = super().get_tools()
        tools.update(billing_tools())
        return tools


class OrderAgent(BaseAgent):
    """订单与物流领域：只依据业务工具返回的事实回答。"""

    agent_type = AgentType.ORDER
    profile = AgentProfile(
        role="电商订单与物流客服",
        mission="查询订单和物流事实，判断取消订单或修改地址所需条件，不虚构后台状态。",
        workflow=("确认订单归属", "查询订单", "按需查询物流", "说明可判断事实", "给出下一步"),
        input_contract=("订单号", "用户标识", "订单与物流工具结果", "政策证据"),
        output_contract=("真实订单状态", "物流节点或缺失说明", "下一步操作", "事实来源边界"),
        handoff_conditions=("订单归属异常", "物流长期停滞", "需要承运商或人工修改后台数据"),
        tool_scope=("search_knowledge_base", "get_order", "get_logistics", "prepare_cancel_order", "prepare_address_change"),
        temperature=0.0,
        max_tokens=900,
    )
    system_prompt = (
        "你是电商订单与物流客服。订单和物流事实必须来自工具结果；"
        "工具未成功时不得声称已经查询，也不得承诺具体送达时间。"
    )

    def _build_role_packet(self, req: Request) -> str:
        packet = json.loads(super()._build_role_packet(req))
        packet["order_fields"] = {
            "order_ids": req.entities.get("order_id", []),
            "required_if_missing": ["订单号"],
            "risk_boundary": "不得读取其他用户订单，不得把物流预测说成承运商承诺",
        }
        return json.dumps(packet, ensure_ascii=False)


class AfterSalesAgent(BaseAgent):
    """退换货与退款申请领域，写操作只允许生成待确认动作。"""

    agent_type = AgentType.AFTER_SALES
    profile = AgentProfile(
        role="电商退换货与售后客服",
        mission="结合订单事实和适用政策完成售后预检，在用户确认前不执行高风险动作。",
        workflow=("确认订单", "核验售后资格", "说明金额和影响", "创建待确认动作", "确认后执行或升级"),
        input_contract=("订单号", "售后原因", "商品和订单状态", "政策证据"),
        output_contract=("资格预检结果", "证据与限制", "待确认动作", "人工复核条件"),
        handoff_conditions=("虚拟或特殊商品", "超过售后窗口", "大额或争议订单", "证据冲突"),
        tool_scope=("search_knowledge_base", "check_return_eligibility", "prepare_refund_request"),
        temperature=0.0,
        max_tokens=1000,
    )
    system_prompt = (
        "你是电商售后客服。prepare_refund_request 只会创建待确认动作，不代表退款成功；"
        "不得绕过确认、幂等和人工复核边界。"
    )

    def _build_role_packet(self, req: Request) -> str:
        packet = json.loads(super()._build_role_packet(req))
        packet["after_sales_fields"] = {
            "order_ids": req.entities.get("order_id", []),
            "required_if_missing": ["订单号", "退款或退换原因"],
            "risk_boundary": "未经工具预检和用户确认，不得声称已提交或完成退款",
        }
        return json.dumps(packet, ensure_ascii=False)


class EscalationAgent(BaseAgent):
    """人工升级节点。

    升级不是一个普通问答 Prompt：它应该生成标准化的交接信息并停止普通
    Agent 继续编造答案。生产环境可在这里接工单系统、人工队列或 Webhook。
    """

    agent_type = AgentType.ESCALATION
    profile = AgentProfile(
        role="人工升级与交接",
        mission="确认升级原因，整理已知上下文，告知用户下一步，不执行未经授权的业务操作。",
        workflow=("确认升级原因", "整理已知信息", "标记优先级", "生成交接摘要"),
        input_contract=("用户消息", "意图", "紧急度", "结构化实体", "对话背景"),
        output_contract=("升级原因", "已知信息摘要", "还需补充的信息", "保守的后续说明"),
        handoff_conditions=("用户明确要求人工", "紧急或高风险场景"),
        tool_scope=("search_knowledge_base", "create_handoff_summary", "create_handoff_ticket"),
        temperature=0.0,
        max_tokens=500,
    )
    system_prompt = "你负责客服人工升级交接，不要继续模拟已完成的后台操作。"

    def get_tools(self) -> Dict[str, AgentToolSpec]:
        tools = super().get_tools()
        tools.update(escalation_tools())
        return tools

    async def handle(self, req: Request) -> AgentResponse:
        t0 = time.monotonic()
        self.stats.total += 1
        intent = req.intent.value if req.intent else "unknown"
        urgency = req.urgency.name if req.urgency else "UNKNOWN"
        entities = json.dumps(req.entities or {}, ensure_ascii=False)
        ticket = None
        ticket_tool = self.get_tools().get("create_handoff_ticket")
        if ticket_tool is not None:
            ticket = ticket_tool.handler(req, {"reason": "用户明确要求人工或请求达到升级条件"})
            if inspect.isawaitable(ticket):
                ticket = await ticket
        ticket_text = f"\n工单编号：{ticket.get('ticket_id')}，状态：{ticket.get('status')}" if isinstance(ticket, dict) and ticket.get("ticket_id") else ""
        content = (
            "我已将这个问题标记为人工升级处理。\n\n"
            f"升级原因：意图={intent}，紧急度={urgency}\n"
            f"已记录信息：{entities}\n"
            f"请不要发送密码、短信验证码或完整支付凭证；人工客服会根据会话记录继续核验。{ticket_text}"
        )
        ms = (time.monotonic() - t0) * 1000
        self.stats.success += 1
        self.stats.total_ms += ms
        return AgentResponse(
            agent_type=self.agent_type,
            content=content,
            success=True,
            latency_ms=ms,
            escalate=True,
            tools_used=["create_handoff_ticket"] if ticket else [],
            tool_traces=[{
                "agent_type": self.agent_type.value, "tool_name": "create_handoff_ticket",
                "input": {"reason": "用户明确要求人工或请求达到升级条件"}, "success": bool(ticket),
                "result_success": bool(ticket), "latency_ms": 0.0, "cached": False, "reranked": False, "error": "",
            }] if ticket_tool is not None else [],
        )


class ResponseComposer:
    """多 Agent 汇总节点，统一主次、去重和输出边界。"""

    def __init__(self, client: AsyncAnthropic, model: str, skill_manager: Optional[Any] = None):
        self._client = client
        self._model = model
        self._skill_manager = skill_manager

    async def compose(self, req: Request, responses: List[AgentResponse]) -> str:
        successful = [response for response in responses if response.success and response.content.strip()]
        if not successful:
            return "抱歉，所有 Agent 均处理失败。"
        if len(successful) == 1:
            return successful[0].content

        evidence = "\n\n".join(
            f"[{response.agent_type.value} Agent 输出]\n{response.content}"
            for response in successful
        )
        prompt = (
            "你是客服 Response Composer，负责把多个专业 Agent 的结果合并成一条最终回复。\n"
            "要求：以主 Agent 的结论为主，按用户问题优先级组织内容；去掉重复和冲突表述；"
            "不能补造订单、退款、后台查询结果；如果结论冲突，明确说明需要核验；"
            "保留必要的排查步骤、核验字段和升级边界。只输出给用户看的中文回复，不要提及 Agent。\n\n"
            f"主 Agent：{successful[0].agent_type.value}\n"
            f"用户问题：{req.message}\n"
            f"候选结果：\n{evidence}"
        )
        if self._skill_manager is not None:
            skill = self._skill_manager.prompt_for(req.message, "general")
            if skill:
                prompt += f"\n\n[通用客服输出边界]\n{skill}"
        try:
            response = await self._client.messages.create(
                model=self._model,
                max_tokens=_env_int("COMMERCEMIND_COMPOSER_MAX_TOKENS", 1000),
                temperature=_env_float("COMMERCEMIND_COMPOSER_TEMPERATURE", 0.1),
                messages=[{"role": "user", "content": prompt}],
            )
            content = extract_text_content(response.content).strip()
            if content and not self._looks_like_internal_reasoning(content) and not self._looks_incomplete(content):
                return content
            if content:
                logger.warning("Response Composer 返回内部推理或疑似截断文本，使用确定性合并")
        except Exception as ex:
            logger.warning("Response Composer 失败，使用确定性合并: %s", ex)

        # 汇总节点不可用时保留主次标签，避免丢失某个专业 Agent 的结论。
        return "\n\n".join(
            f"{response.content}" if index == 0 else f"补充说明：\n{response.content}"
            for index, response in enumerate(successful)
        )

    @staticmethod
    def _looks_like_internal_reasoning(content: str) -> bool:
        """Reject common prompt/reasoning leakage from compatible model gateways."""
        markers = (
            "我们需要回答用户",
            "需要合并",
            "只输出给用户",
            "候选结果",
            "主 Agent",
            "最终回复要",
            "不要提及 Agent",
        )
        hits = sum(marker in content for marker in markers)
        return hits >= 2

    @staticmethod
    def _looks_incomplete(content: str) -> bool:
        """Detect a visibly cut-off composer answer and fall back to source outputs."""
        text = content.rstrip()
        if len(text) < 20:
            return False
        terminal = ("。", "！", "？", ".", "!", "?", "）", ")", "】", "]", "”", '"', "`", "：", ":")
        return not text.endswith(terminal)


# ── 编排器 ────────────────────────────────────────────────────────────────────

class AgentOrchestrator:
    """
    多 Agent 编排器。

    路由逻辑（三层）：
      1. 意图 → Agent 类型映射
      2. 同类多实例时按 routing_score() 选最优
      3. 专属 Agent 失败时降级到 GeneralAgent
    """

    # 意图 → Agent 类型的静态映射（路由表）
    _INTENT_ROUTING: Dict[IntentCategory, AgentType] = {
        IntentCategory.TECHNICAL:  AgentType.TECHNICAL,
        IntentCategory.TECHNICAL_LOGIN: AgentType.TECHNICAL,
        IntentCategory.TECHNICAL_CRASH: AgentType.TECHNICAL,
        IntentCategory.BILLING:    AgentType.BILLING,
        IntentCategory.INVOICE:    AgentType.BILLING,
        IntentCategory.PAYMENT_ISSUE: AgentType.BILLING,
        IntentCategory.DUPLICATE_PAYMENT: AgentType.BILLING,
        IntentCategory.REFUND_STATUS: AgentType.BILLING,
        IntentCategory.ACCOUNT:    AgentType.BILLING,
        IntentCategory.ACCOUNT_SECURITY: AgentType.BILLING,
        IntentCategory.ORDER_STATUS: AgentType.ORDER,
        IntentCategory.LOGISTICS: AgentType.ORDER,
        IntentCategory.ORDER_CANCEL: AgentType.ORDER,
        IntentCategory.ADDRESS_CHANGE: AgentType.ORDER,
        IntentCategory.REFUND: AgentType.AFTER_SALES,
        IntentCategory.RETURN_EXCHANGE: AgentType.AFTER_SALES,
        IntentCategory.DAMAGED_ITEM: AgentType.AFTER_SALES,
        IntentCategory.ESCALATION: AgentType.ESCALATION,
        IntentCategory.HUMAN_HANDOFF: AgentType.ESCALATION,
        # 其余意图 → GENERAL（默认）
    }

    def __init__(
        self,
        api_key:  str,
        base_url: Optional[str] = None,
        model:    str = "claude-3-5-sonnet-20241022",
        skill_manager: Optional[Any] = None,
        rag_tool_manager: Optional[Any] = None,
        commerce_service: Optional[Any] = None,
    ):
        kwargs: Dict[str, Any] = {"api_key": api_key}
        if base_url:
            kwargs["base_url"] = base_url
        client = build_llm_client(api_key=api_key, base_url=base_url)

        self._intent_recognizer = IntentRecognizer(api_key=api_key, base_url=base_url, model=model)
        self._skill_manager = skill_manager
        self._composer = ResponseComposer(client, model, skill_manager)
        self._shared_tools: Dict[str, AgentToolSpec] = {}
        self._recent_tool_traces = deque(maxlen=_env_int("COMMERCEMIND_TOOL_TRACE_MAX", 200))

        # Agent 池：每种类型可有多个实例（水平扩展）
        self._pool: Dict[AgentType, List[BaseAgent]] = {
            AgentType.GENERAL: [self._make_agent(GeneralAgent, client, model, skill_manager)],
            AgentType.TECHNICAL: [self._make_agent(TechnicalAgent, client, model, skill_manager)],
            AgentType.BILLING: [self._make_agent(BillingAgent, client, model, skill_manager)],
            AgentType.ORDER: [self._make_agent(OrderAgent, client, model, skill_manager)],
            AgentType.AFTER_SALES: [self._make_agent(AfterSalesAgent, client, model, skill_manager)],
            AgentType.ESCALATION: [self._make_agent(EscalationAgent, client, model, skill_manager)],
        }
        self.set_shared_tools(build_shared_rag_tools(rag_tool_manager))
        if commerce_service is not None:
            self._pool[AgentType.ORDER][0].set_domain_tools(build_order_tools(commerce_service))
            self._pool[AgentType.AFTER_SALES][0].set_domain_tools(build_after_sales_tools(commerce_service))
            self._pool[AgentType.BILLING][0].set_domain_tools(build_commerce_billing_tools(commerce_service))
            self._pool[AgentType.ESCALATION][0].set_domain_tools(escalation_tools(commerce_service))

    @staticmethod
    def _make_agent(
        agent_cls: type[BaseAgent],
        client: AsyncAnthropic,
        default_model: str,
        skill_manager: Optional[Any],
    ) -> BaseAgent:
        """按角色创建 Agent，并允许用环境变量覆盖该角色的模型。

        可使用更强模型，通用接待可使用更快模型，升级节点本身不需要调用 LLM。
        """
        profile = agent_cls.profile
        env_name = f"COMMERCEMIND_{agent_cls.agent_type.value.upper()}_MODEL"
        model = os.getenv(env_name, "").strip() or profile.model
        configured_profile = replace(profile, model=model) if model else profile
        return agent_cls(client, default_model, skill_manager, profile=configured_profile)

    def set_skill_manager(self, skill_manager: Optional[Any]) -> None:
        """更新 SkillManager 引用，供运行时重载或测试替换使用。"""
        self._skill_manager = skill_manager
        self._composer._skill_manager = skill_manager
        for agents in self._pool.values():
            for agent in agents:
                agent._skill_manager = skill_manager

    def set_shared_tools(self, tools: Optional[Dict[str, AgentToolSpec]]) -> None:
        """更新所有 Agent 共享的工具白名单。"""
        self._shared_tools = dict(tools or {})
        for agents in self._pool.values():
            for agent in agents:
                agent.set_shared_tools(self._shared_tools)

    async def recognize_intent(
        self,
        message: str,
        history: Optional[List[Dict[str, str]]] = None,
    ):
        """对外暴露意图识别，供 API 层先判断是否需要 RAG 等前置能力。"""
        return await self._intent_recognizer.recognize(message, history=history)

    def _record_tool_trace(self, result: OrchestratorResult) -> None:
        trace = {
            "request_id": result.request_id,
            "timestamp": datetime.now().isoformat(),
            "intent": result.intent.value if result.intent else None,
            "primary_agent": result.primary_agent.value if result.primary_agent else None,
            "supporting_agents": [agent.value for agent in result.supporting_agents],
            "tools_used": list(result.tools_used),
            "tool_calls": list(result.tool_traces),
            "escalated": result.escalated,
            "latency_ms": round(result.latency_ms, 1),
        }
        self._recent_tool_traces.append(trace)

    def get_tool_trace(self, request_id: str) -> Optional[Dict[str, Any]]:
        for trace in reversed(self._recent_tool_traces):
            if trace.get("request_id") == request_id:
                return trace
        return None

    def get_recent_tool_traces(self, limit: int = 20) -> List[Dict[str, Any]]:
        if not self._recent_tool_traces:
            return []
        limit = max(1, min(int(limit or 20), len(self._recent_tool_traces)))
        return list(reversed(list(self._recent_tool_traces)[-limit:]))

    def enrich_trace(self, request_id: str, **fields: Any) -> None:
        """在编排结束后补充引用、Guard 等跨层信息，形成统一请求 Trace。"""
        for trace in reversed(self._recent_tool_traces):
            if trace.get("request_id") == request_id:
                trace.update(fields)
                return

    # ── 主入口 ────────────────────────────────────────────────────────────────

    async def run(self, req: Request) -> OrchestratorResult:
        """
        处理一次请求的完整流程：
          意图识别 → 路由选 Agent → 执行 → 检查升级 → 返回结果
        """
        t0 = time.monotonic()

        # 1. 意图识别（如果调用方已识别则跳过）
        if req.intent is None:
            intent_result = await self._intent_recognizer.recognize(req.message, history=req.history)
            req.intent  = intent_result.intent
            req.intent_group = intent_result.intent_group
            req.urgency = intent_result.urgency
            req.intent_confidence = intent_result.confidence

        if self._needs_clarification(req):
            result = OrchestratorResult(
                request_id=req.request_id,
                response="我还不能确定您要处理的是哪类问题。请补充一下是订单物流、退款账单、账户资料，还是技术故障？",
                agent_type=AgentType.GENERAL,
                intent=req.intent,
                escalated=False,
                latency_ms=(time.monotonic() - t0) * 1000,
                agent_types=[AgentType.GENERAL],
                primary_agent=AgentType.GENERAL,
                routing_reason="低置信度 OTHER 意图，先澄清用户需求",
                routing_confidence=req.intent_confidence,
            )
            self._record_tool_trace(result)
            return result

        # 复杂问题自动并行协作，例如同一句同时涉及登录故障和扣款/退款。
        decision = self._route_decision(req)
        if decision.multi_agent:
            return await self.run_parallel(req, decision)

        # 2. 执行主 Agent（含降级）
        response = await self._execute(req, decision.primary_agent)

        # 4. 升级检查
        escalated = False
        if response.escalate or req.urgency == UrgencyLevel.CRITICAL or req.intent in (
            IntentCategory.ESCALATION,
            IntentCategory.HUMAN_HANDOFF,
        ):
            escalated = True
            logger.warning(f"请求 {req.request_id} 触发升级: urgency={req.urgency}")
            # 生产环境：此处创建工单、通知人工客服

        result = OrchestratorResult(
            request_id=req.request_id,
            response=response.content,
            agent_type=response.agent_type,
            intent=req.intent,
            escalated=escalated,
            latency_ms=(time.monotonic() - t0) * 1000,
            agent_types=[response.agent_type],
            primary_agent=decision.primary_agent,
            supporting_agents=[],
            tools_used=list(response.tools_used),
            tool_traces=list(response.tool_traces),
            routing_reason=decision.reason,
            routing_confidence=decision.confidence,
            pending_actions=list(response.pending_actions),
        )
        self._record_tool_trace(result)
        return result

    async def run_parallel(self, req: Request, decision: RoutingDecision) -> OrchestratorResult:
        """
        并行派发给多个 Agent，合并结果。
        适用于复杂问题（如同时涉及技术和账单）。
        """
        t0 = time.monotonic()
        agent_types = decision.agent_types
        tasks = [self._execute(req, at) for at in agent_types]
        responses = await asyncio.gather(*tasks, return_exceptions=True)

        valid_responses = [r for r in responses if isinstance(r, AgentResponse)]
        combined = await self._composer.compose(req, valid_responses)
        escalated = any(isinstance(r, AgentResponse) and r.escalate for r in responses)
        tools_used = list(dict.fromkeys(
            tool_name
            for response in valid_responses
            for tool_name in response.tools_used
        ))
        tool_traces = [
            trace
            for response in valid_responses
            for trace in response.tool_traces
        ]
        pending_actions = [
            action
            for response in valid_responses
            for action in response.pending_actions
        ]
        result = OrchestratorResult(
            request_id=req.request_id,
            response=combined,
            agent_type=decision.primary_agent,
            intent=req.intent,
            escalated=escalated,
            latency_ms=(time.monotonic() - t0) * 1000,
            agent_types=[
                r.agent_type for r in responses
                if isinstance(r, AgentResponse) and r.success
            ] or agent_types,
            primary_agent=decision.primary_agent,
            supporting_agents=decision.supporting_agents,
            tools_used=tools_used,
            tool_traces=tool_traces,
            routing_reason=decision.reason,
            routing_confidence=decision.confidence,
            pending_actions=pending_actions,
        )
        self._record_tool_trace(result)
        return result

    # ── 路由逻辑 ──────────────────────────────────────────────────────────────

    def _route(self, intent: Optional[IntentCategory], urgency: Optional[UrgencyLevel]) -> AgentType:
        """
        三层路由决策：
          1. 意图映射
          2. 紧急度覆盖（CRITICAL 直接升级）
          3. 默认 GENERAL
        """
        if urgency == UrgencyLevel.CRITICAL:
            return AgentType.ESCALATION

        if intent and intent in self._INTENT_ROUTING:
            target = self._INTENT_ROUTING[intent]
            # 如果目标类型有可用实例则使用，否则降级
            if target in self._pool and self._pool[target]:
                return target

        return AgentType.GENERAL

    def _route_decision(self, req: Request) -> RoutingDecision:
        """
        结构化路由决策。

        先处理紧急/转人工，再用领域分数决定主 Agent 和辅助 Agent。
        这样可以表达“主处理 + 辅助诊断”，避免关键词命中后无主次地拼接。
        """
        if req.urgency == UrgencyLevel.CRITICAL:
            return RoutingDecision(
                primary_agent=AgentType.ESCALATION,
                reason="紧急度为 CRITICAL，触发升级路由",
                confidence=1.0,
            )

        if req.intent in (IntentCategory.ESCALATION, IntentCategory.HUMAN_HANDOFF):
            return RoutingDecision(
                primary_agent=AgentType.ESCALATION,
                reason=f"意图为 {req.intent.value if req.intent else 'unknown'}，触发升级路由",
                confidence=max(req.intent_confidence, 0.8),
            )

        scores = self._domain_scores(req)
        available_scores = {
            agent_type: score
            for agent_type, score in scores.items()
            if agent_type == AgentType.GENERAL or self._pool.get(agent_type)
        }
        if not available_scores:
            return RoutingDecision(
                primary_agent=AgentType.GENERAL,
                reason="无可用专属 Agent，降级到 GeneralAgent",
                confidence=0.1,
            )

        ordered = sorted(available_scores.items(), key=lambda item: item[1], reverse=True)
        primary_agent, primary_score = ordered[0]

        collaboration_targets = self._collaboration_targets(req)
        supporting_agents = [
            agent_type
            for agent_type in collaboration_targets
            if agent_type != primary_agent and agent_type in available_scores
        ]

        if not supporting_agents:
            supporting_agents = [
                agent_type
                for agent_type, score in ordered[1:]
                if agent_type != AgentType.GENERAL
                and score >= 0.45
                and score >= primary_score * 0.55
            ]

        reason = self._routing_reason(req, available_scores, primary_agent, supporting_agents)
        return RoutingDecision(
            primary_agent=primary_agent,
            supporting_agents=supporting_agents,
            reason=reason,
            confidence=round(min(primary_score, 1.0), 3),
        )

    def _domain_scores(self, req: Request) -> Dict[AgentType, float]:
        """按意图、关键词和实体为各领域 Agent 打分。"""
        msg = req.message.lower()
        scores = {
            AgentType.GENERAL: 0.1,
            AgentType.TECHNICAL: 0.0,
            AgentType.BILLING: 0.0,
        }

        if req.intent in (
            IntentCategory.QUERY,
            IntentCategory.REQUEST,
            IntentCategory.COMPLAINT,
            IntentCategory.GREETING,
            IntentCategory.FEEDBACK,
            IntentCategory.OTHER,
        ):
            scores[AgentType.GENERAL] += 0.55

        scores[AgentType.ORDER] = 0.0
        scores[AgentType.AFTER_SALES] = 0.0

        if req.intent in (
            IntentCategory.ORDER_STATUS,
            IntentCategory.LOGISTICS,
            IntentCategory.ORDER_CANCEL,
            IntentCategory.ADDRESS_CHANGE,
        ):
            scores[AgentType.ORDER] += 0.75

        if req.intent in (
            IntentCategory.REFUND,
            IntentCategory.RETURN_EXCHANGE,
            IntentCategory.DAMAGED_ITEM,
        ):
            scores[AgentType.AFTER_SALES] += 0.75

        if req.intent in (
            IntentCategory.TECHNICAL,
            IntentCategory.TECHNICAL_LOGIN,
            IntentCategory.TECHNICAL_CRASH,
        ):
            scores[AgentType.TECHNICAL] += 0.75

        if req.intent in (
            IntentCategory.BILLING,
            IntentCategory.ACCOUNT,
            IntentCategory.ACCOUNT_SECURITY,
            IntentCategory.INVOICE,
            IntentCategory.PAYMENT_ISSUE,
            IntentCategory.DUPLICATE_PAYMENT,
            IntentCategory.REFUND_STATUS,
        ):
            scores[AgentType.BILLING] += 0.75

        technical_kws = ["崩溃", "报错", "error", "crash", "无法登录", "登录失败", "500", "401", "验证码"]
        billing_kws = ["退款到账", "退款进度", "没到账", "扣款", "扣了两次", "重复支付", "发票", "账单", "支付", "订阅", "invoice", "多扣"]
        general_kws = ["订单", "物流", "快递", "配送", "会员", "积分", "咨询", "帮助"]
        order_kws = ["订单状态", "发货", "物流", "快递", "配送", "运单", "修改地址", "取消订单"]
        # “退款进度/退款到账”属于账单状态查询，不能仅因出现“退款”就额外拉起售后 Agent。
        after_sales_kws = ["申请退款", "我要退款", "退货", "换货", "破损", "少件", "错发", "漏发"]

        technical_hits = sum(1 for kw in technical_kws if kw in msg)
        billing_hits = sum(1 for kw in billing_kws if kw in msg)
        general_hits = sum(1 for kw in general_kws if kw in msg)
        order_hits = sum(1 for kw in order_kws if kw in msg)
        after_sales_hits = sum(1 for kw in after_sales_kws if kw in msg)

        scores[AgentType.TECHNICAL] += min(0.45, technical_hits * 0.18)
        scores[AgentType.BILLING] += min(0.45, billing_hits * 0.18)
        scores[AgentType.GENERAL] += min(0.35, general_hits * 0.12)
        scores[AgentType.ORDER] += min(0.45, order_hits * 0.18)
        scores[AgentType.AFTER_SALES] += min(0.45, after_sales_hits * 0.18)

        entities = req.entities or {}
        if entities.get("error_code"):
            scores[AgentType.TECHNICAL] += 0.2
        if entities.get("amount"):
            scores[AgentType.BILLING] += 0.15
        if entities.get("order_id"):
            scores[AgentType.ORDER] += 0.1
            scores[AgentType.AFTER_SALES] += 0.05

        return {agent_type: round(score, 3) for agent_type, score in scores.items()}

    @staticmethod
    def _routing_reason(
        req: Request,
        scores: Dict[AgentType, float],
        primary_agent: AgentType,
        supporting_agents: List[AgentType],
    ) -> str:
        score_text = ", ".join(
            f"{agent_type.value}={score:.2f}"
            for agent_type, score in sorted(scores.items(), key=lambda item: item[1], reverse=True)
        )
        support_text = ", ".join(agent.value for agent in supporting_agents) or "none"
        intent = req.intent.value if req.intent else "unknown"
        return (
            f"intent={intent}, group={req.intent_group or 'unknown'}, "
            f"primary={primary_agent.value}, supporting={support_text}, scores=[{score_text}]"
        )

    def _collaboration_targets(self, req: Request) -> List[AgentType]:
        """
        判断是否需要多个 Agent 并行协作。

        意图识别通常只返回一个主意图；这里用领域关键词补充检测复合问题，
        例如"登录报错且被重复扣款"需要技术和账单 Agent 同时处理。
        """
        msg = req.message.lower()
        targets: List[AgentType] = []

        technical_kws = ["崩溃", "报错", "error", "crash", "无法登录", "登录失败", "500", "401"]
        billing_kws = ["退款到账", "退款进度", "没到账", "扣款", "扣了两次", "重复支付", "发票", "账单", "支付", "订阅", "invoice"]
        order_kws = ["订单状态", "发货", "物流", "快递", "配送", "修改地址", "取消订单"]
        after_sales_kws = ["申请退款", "我要退款", "退货", "换货", "破损", "少件", "错发", "漏发"]

        if req.intent in (
            IntentCategory.TECHNICAL,
            IntentCategory.TECHNICAL_LOGIN,
            IntentCategory.TECHNICAL_CRASH,
        ) or any(kw in msg for kw in technical_kws):
            targets.append(AgentType.TECHNICAL)
        if req.intent in (
            IntentCategory.BILLING,
            IntentCategory.ACCOUNT,
            IntentCategory.ACCOUNT_SECURITY,
            IntentCategory.INVOICE,
            IntentCategory.PAYMENT_ISSUE,
            IntentCategory.DUPLICATE_PAYMENT,
            IntentCategory.REFUND_STATUS,
        ) or any(kw in msg for kw in billing_kws):
            targets.append(AgentType.BILLING)
        if req.intent in (
            IntentCategory.ORDER_STATUS,
            IntentCategory.LOGISTICS,
            IntentCategory.ORDER_CANCEL,
            IntentCategory.ADDRESS_CHANGE,
        ) or any(kw in msg for kw in order_kws):
            targets.append(AgentType.ORDER)
        if req.intent in (
            IntentCategory.REFUND,
            IntentCategory.RETURN_EXCHANGE,
            IntentCategory.DAMAGED_ITEM,
        ) or any(kw in msg for kw in after_sales_kws):
            targets.append(AgentType.AFTER_SALES)

        # 保持顺序去重，并只返回当前有实例的 Agent 类型。
        deduped = list(dict.fromkeys(targets))
        return [agent_type for agent_type in deduped if self._pool.get(agent_type)]

    @staticmethod
    def _needs_clarification(req: Request) -> bool:
        """低置信度且无明确意图时，先追问，避免误路由。"""
        if req.intent != IntentCategory.OTHER:
            return False
        text = (req.message or "").strip()
        if len(text) <= 2:
            return False
        return req.intent_confidence < 0.5

    def _best_agent(self, agent_type: AgentType) -> Optional[BaseAgent]:
        """
        性能路由：从同类 Agent 中选 routing_score() 最高的。
        这是"基于在线表现动态调整路由"的核心。
        """
        agents = self._pool.get(agent_type, [])
        if not agents:
            return None
        return max(agents, key=lambda a: a.stats.routing_score())

    async def _execute(self, req: Request, agent_type: AgentType) -> AgentResponse:
        """执行 Agent，失败时降级到 GeneralAgent。"""
        agent = self._best_agent(agent_type)
        if agent is None:
            agent = self._best_agent(AgentType.GENERAL)
        if agent is None:
            return AgentResponse(
                agent_type=AgentType.GENERAL,
                content="服务暂时不可用，请稍后重试。",
                success=False,
            )

        response = await agent.handle(req)

        # 专属 Agent 失败时降级到 GeneralAgent
        if not response.success and agent_type not in (AgentType.GENERAL, AgentType.ESCALATION):
            logger.warning(f"{agent_type.value} 失败，降级到 GeneralAgent")
            fallback = self._best_agent(AgentType.GENERAL)
            if fallback:
                response = await fallback.handle(req)

        return response

    # ── 统计（供 Monitor 读取）────────────────────────────────────────────────

    def get_stats(self) -> Dict[str, Any]:
        result = {}
        for agent_type, agents in self._pool.items():
            for i, agent in enumerate(agents):
                key = f"{agent_type.value}_{i}"
                result[key] = {
                    "total":        agent.stats.total,
                    "success_rate": round(agent.stats.success_rate, 3),
                    "avg_ms":       round(agent.stats.avg_ms, 1),
                    "monitor_penalty": round(agent.stats.monitor_penalty, 3),
                    "routing_score": round(agent.stats.routing_score(), 3),
                    "role": agent.profile.role,
                    "workflow": list(agent.profile.workflow),
                    "tool_scope": list(agent.profile.tool_scope),
                    "available_tools": list(agent.get_tools()),
                    "model": agent._model,
                }
        return result

    def update_routing_penalties(self, penalties: Dict[str, float]) -> None:
        """
        接收 Monitor 的在线表现反馈，动态调整路由惩罚项。

        penalties 的 key 使用 get_stats() 中的 agent key，例如 technical_0。
        """
        for agent_type, agents in self._pool.items():
            for i, agent in enumerate(agents):
                key = f"{agent_type.value}_{i}"
                penalty = penalties.get(key, 0.0)
                agent.stats.monitor_penalty = min(max(penalty, 0.0), 0.9)
