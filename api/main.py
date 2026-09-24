"""
CommerceMind 智能客服系统 — FastAPI 入口

启动时打印小熊饼干图案。
所有核心组件在 lifespan 中初始化，通过环境变量配置。
"""
import asyncio
import logging
import os
import pathlib
import sys
import uuid
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional


_ROOT = str(pathlib.Path(__file__).parent.parent.resolve())
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Response, UploadFile, File, Header
from fastapi.middleware.cors import CORSMiddleware
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from pydantic import BaseModel, Field

# 本机密钥优先放在不提交的 .env.local；公共 .env 只保存非敏感默认配置。
load_dotenv(".env.local")
load_dotenv()

logging.basicConfig(
    level=getattr(logging, os.getenv("LOG_LEVEL", "INFO")),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

BANNER = r"""
    ʕ•ᴥ•ʔ  ʕ•ᴥ•ʔ  ʕ•ᴥ•ʔ
   ╔══════════════════════╗
   ║  CommerceMind v0.1   ║
   ║   电商售后 Agent     ║
   ╚══════════════════════╝
    ʕ•ᴥ•ʔ  ʕ•ᴥ•ʔ  ʕ•ᴥ•ʔ
"""

# ── 全局组件（lifespan 中初始化）─────────────────────────────────────────────
_orchestrator = None
_memory       = None
_tool_manager = None
_monitor      = None
_evaluator    = None
_skill_manager = None
_commerce_service = None

def _anthropic_cfg() -> Dict[str, Any]:
    key = os.getenv("ANTHROPIC_API_KEY", "")
    if not key:
        raise RuntimeError("未设置 ANTHROPIC_API_KEY")
    cfg: Dict[str, Any] = {
        "api_key":  key,
        "model":    os.getenv("ANTHROPIC_MODEL", "claude-3-5-sonnet-20241022").strip(),
    }
    base_url = os.getenv("ANTHROPIC_BASE_URL", "").strip()
    if base_url:
        cfg["base_url"] = base_url
    return cfg


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _orchestrator, _memory, _tool_manager, _monitor, _evaluator, _skill_manager, _commerce_service

    print(BANNER, flush=True)

    from agents.agent_orchestrator import AgentOrchestrator, Request, build_shared_rag_tools
    from core.intent_recognizer import IntentRecognizer
    from evaluation.evaluator import EndToEndEvaluator
    from mcp.tool_manager import MCPToolManager, Tool
    from monitor.performance_monitor import PerformanceMonitor
    from core.skill_loader import SkillManager
    from commerce.service import CommerceService
    from commerce.store import CommerceStore

    cfg = _anthropic_cfg()
    local_demo = os.getenv("LOCAL_DEMO_MODE", "false").lower() in {"1", "true", "yes"}
    llm_disabled = os.getenv("DISABLE_LLM", "false").lower() in {"1", "true", "yes"}
    logger.info(f"模型: {cfg['model']}  base_url: {cfg.get('base_url', '(官方)')}")
    logger.info("基础设施模式: %s", "本地（内存 + 轻量检索）" if local_demo else "Redis + ChromaDB")
    logger.info(
        "LLM 模式: %s",
        "关闭（确定性降级）" if llm_disabled else f"启用（{os.getenv('LLM_API_FORMAT', 'anthropic')}）",
    )

    # 意图识别器（Orchestrator 内部也会创建，这里单独暴露给 Evaluator）
    recognizer = IntentRecognizer(
        api_key=cfg["api_key"],
        base_url=cfg.get("base_url"),
        model=cfg["model"],
    )

    # Skills：启动时从目录加载业务能力说明，并在 Agent 调用 LLM 时动态注入。
    skills_dir = os.getenv("COMMERCEMIND_SKILLS_DIR", str(pathlib.Path(_ROOT) / "skills"))
    _skill_manager = SkillManager(
        root_dir=skills_dir,
        max_prompt_chars=int(os.getenv("COMMERCEMIND_SKILLS_MAX_PROMPT_CHARS", "5000")),
    )
    _skill_manager.load()

    # 电商业务事实与动作状态机。SQLite 用于可复现演示，接口可替换为 PostgreSQL。
    database_url = os.getenv("DATABASE_URL", "").strip()
    if database_url:
        from commerce.postgres_store import PostgresCommerceStore
        commerce_store = PostgresCommerceStore(database_url)
    else:
        commerce_store = CommerceStore(
            os.getenv("COMMERCE_DB_PATH", str(pathlib.Path(_ROOT) / "data" / "commerce.sqlite3"))
        )
    commerce_store.seed_demo_data()
    payment_gateway = None
    stripe_secret_key = os.getenv("STRIPE_SECRET_KEY", "").strip()
    if stripe_secret_key:
        from commerce.payment_gateway import StripePaymentGateway
        payment_gateway = StripePaymentGateway(
            stripe_secret_key,
            base_url=os.getenv("STRIPE_BASE_URL", "https://api.stripe.com"),
        )
        logger.info("Stripe payment gateway enabled")
    _commerce_service = CommerceService(commerce_store, payment_gateway=payment_gateway)

    # Agent 编排器
    _orchestrator = AgentOrchestrator(
        api_key=cfg["api_key"],
        base_url=cfg.get("base_url"),
        model=cfg["model"],
        skill_manager=_skill_manager,
        commerce_service=_commerce_service,
    )

    # 记忆管理器（Redis 工作记忆 + ChromaDB 情景记忆/用户画像）
    if local_demo:
        from memory.local_memory import LocalMemoryManager
        _memory = LocalMemoryManager()
    else:
        from memory.conversation_memory import MemoryManager
        _memory = MemoryManager(
            redis_url=os.getenv("REDIS_URL", "redis://redis:6379/0"),
            chroma_host=os.getenv("CHROMA_HOST", "chromadb"),
            chroma_port=int(os.getenv("CHROMA_PORT", "8000")),
            chroma_path=os.getenv("CHROMA_PERSIST_DIRECTORY", "/app/data/chroma"),
            api_key=cfg["api_key"],
            base_url=cfg.get("base_url"),
            model=cfg["model"],
        )

    # MCP 工具管理器 + RAG 知识库（基于 ChromaDB 的真实检索）
    _tool_manager = MCPToolManager(
        api_key=cfg["api_key"],
        base_url=cfg.get("base_url"),
        model=cfg["model"],
    )
    if local_demo:
        from mcp.local_knowledge_base import LocalKnowledgeBase
        kb = LocalKnowledgeBase()
    else:
        from mcp.knowledge_base import KnowledgeBase
        kb = KnowledgeBase(
            chroma_host=os.getenv("CHROMA_HOST", "chromadb"),
            chroma_port=int(os.getenv("CHROMA_PORT", "8000")),
            chroma_path=os.getenv("CHROMA_PERSIST_DIRECTORY", "/app/data/chroma"),
        )
    logger.info(f"知识库已加载: {await kb.doc_count_async()} 个文档片段")

    def knowledge_fallback(params: Dict[str, Any], context: Optional[Dict[str, Any]], error: str):
        query = params.get("query", "")
        return [{
            "title": "知识库降级结果",
            "content": f"知识库暂时不可用，未能完成对“{query}”的语义检索。请稍后重试，或转人工客服确认。",
            "score": 0.0,
            "fallback": True,
            "error": error,
        }]

    _tool_manager.register(Tool(
        name="knowledge_search",
        description="搜索知识库（基于 ChromaDB 向量检索）",
        handler=kb.search_handler,
        schema={
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "top_k": {"type": "integer"},
            },
            "required": ["query"],
        },
        cache_ttl=300.0,
        supports_rerank=True,
        fallback=knowledge_fallback,
    ))
    if _orchestrator is not None:
        _orchestrator.set_shared_tools(build_shared_rag_tools(_tool_manager))

    # 性能监控（可选启动 Prometheus）
    prom_port = int(os.getenv("PROMETHEUS_PORT", "0")) or None
    _monitor = PerformanceMonitor(
        orchestrator=_orchestrator,
        tool_manager=_tool_manager,
        interval_s=float(os.getenv("MONITOR_INTERVAL", "10")),
        webhook_url=os.getenv("ALERT_WEBHOOK_URL") or None,
        prometheus_port=prom_port,
    )
    await _monitor.start()

    # 评测器
    _evaluator = EndToEndEvaluator(
        orchestrator=_orchestrator,
        recognizer=recognizer,
        api_key=cfg["api_key"],
        base_url=cfg.get("base_url"),
        model=cfg["model"],
        baseline_path=os.getenv("EVAL_BASELINE_PATH", "/app/data/eval/baseline.json"),
    )

    logger.info("CommerceMind 已就绪")
    yield

    await _monitor.stop()
    if _memory is not None:
        await _memory.close()
    logger.info("CommerceMind 已关闭")


# ── FastAPI ───────────────────────────────────────────────────────────────────
app = FastAPI(
    title="CommerceMind 电商售后 Agent",
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/docs",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── 请求/响应模型 ─────────────────────────────────────────────────────────────
class ChatRequest(BaseModel):
    message:     str
    user_id:     str = "anonymous"
    conv_id:     Optional[str] = None


def _resolve_user_id(claimed_user_id: str, authorization: Optional[str]) -> str:
    """本地演示兼容 body user_id；生产模式只相信签名令牌中的 sub。"""
    if os.getenv("AUTH_MODE", "demo").lower() == "demo":
        return claimed_user_id or "anonymous"
    from core.auth import AuthenticationError, verify_token
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "缺少 Bearer 身份令牌")
    secret = os.getenv("AUTH_SECRET", "")
    if not secret:
        raise HTTPException(503, "服务端未配置 AUTH_SECRET")
    try:
        return str(verify_token(authorization[7:].strip(), secret)["sub"])
    except AuthenticationError as ex:
        raise HTTPException(401, str(ex)) from ex


class ChatResponse(BaseModel):
    conv_id:     str
    request_id:  str = ""
    response:    str
    intent:      str
    intent_group: str = "other"
    agent_type:  str
    agent_types: List[str] = Field(default_factory=list)
    primary_agent: str = ""
    supporting_agents: List[str] = Field(default_factory=list)
    tools_used: List[str] = Field(default_factory=list)
    routing_reason: str = ""
    routing_confidence: float = 0.0
    escalated:   bool
    latency_ms:  float
    knowledge_used: bool = False
    entities: Dict[str, List[str]] = Field(default_factory=dict)
    intent_confidence: float = 0.0
    intent_source_scores: Dict[str, float] = Field(default_factory=dict)
    safety_violations: List[str] = Field(default_factory=list)
    pending_actions: List[Dict[str, Any]] = Field(default_factory=list)
    citations: List[Dict[str, Any]] = Field(default_factory=list)


class ToolTraceResponse(BaseModel):
    request_id: str
    found: bool
    trace: Dict[str, Any] = Field(default_factory=dict)


class RecentToolTracesResponse(BaseModel):
    items: List[Dict[str, Any]] = Field(default_factory=list)


# ── 路由 ──────────────────────────────────────────────────────────────────────
@app.get("/health")
async def health():
    if _orchestrator is None:
        raise HTTPException(503, "服务未就绪")
    return {
        "status": "ok",
        "runtime": {
            "infrastructure": "local" if os.getenv("LOCAL_DEMO_MODE", "false").lower() in {"1", "true", "yes"} else "full",
            "llm_enabled": os.getenv("DISABLE_LLM", "false").lower() not in {"1", "true", "yes"},
            "llm_api_format": os.getenv("LLM_API_FORMAT", "anthropic"),
            "model": os.getenv("ANTHROPIC_MODEL", ""),
        },
        "agents": _orchestrator.get_stats(),
    }


@app.get("/skills", tags=["Skills"])
async def skills_summary():
    """查看当前已加载的 Skills，便于确认热加载结果和排查解析错误。"""
    if _skill_manager is None:
        raise HTTPException(503, "Skills 未初始化")
    return _skill_manager.summary()


@app.post("/skills/reload", tags=["Skills"])
async def reload_skills():
    """运行时重新扫描 Skill 目录，不需要重启服务。"""
    if _skill_manager is None:
        raise HTTPException(503, "Skills 未初始化")
    _skill_manager.reload()
    if _orchestrator is not None:
        _orchestrator.set_skill_manager(_skill_manager)
    return _skill_manager.summary()


@app.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest, authorization: Optional[str] = Header(default=None)):
    """
    主对话接口。完整流程：
      记忆读取 → 意图识别 → Agent 路由 → 执行 → 记忆写入
    """
    if _orchestrator is None or _memory is None:
        raise HTTPException(503, "服务未就绪")

    from agents.agent_orchestrator import Request as OrcReq
    if os.getenv("LOCAL_DEMO_MODE", "false").lower() in {"1", "true", "yes"}:
        from memory.local_memory import MsgRole
    else:
        from memory.conversation_memory import MsgRole

    user_id = _resolve_user_id(req.user_id, authorization)
    conv_id = req.conv_id or str(uuid.uuid4())

    # 1. 读取记忆上下文
    mem_ctx = await _memory.get_context(user_id, conv_id, query=req.message)

    # 2. 构建编排请求（含对话历史，用于意图识别上下文）
    history = [
        {"role": m.role.value, "content": m.content}
        for m in mem_ctx.recent_messages[-5:]
    ] if mem_ctx.recent_messages else None

    intent_result = await _orchestrator.recognize_intent(req.message, history=history)
    full_context = mem_ctx.to_prompt_text()

    orch_req = OrcReq(
        message=req.message,
        user_id=user_id,
        conv_id=conv_id,
        context=full_context,
        history=history,
        entities=intent_result.entities,
        intent=intent_result.intent,
        intent_group=intent_result.intent_group,
        urgency=intent_result.urgency,
        intent_confidence=intent_result.confidence,
    )

    # 3. 执行
    result = await _orchestrator.run(orch_req)

    # 4. 高风险回复的确定性审核。Prompt 是软约束，资金/隐私边界必须代码兜底。
    from commerce.guard import CommerceResponseGuard
    guard_result = CommerceResponseGuard().review(result.response, result.tools_used)
    result.response = guard_result.content

    # 5. 写入记忆
    await _memory.add_message(user_id, conv_id, MsgRole.USER, req.message)
    await _memory.add_message(user_id, conv_id, MsgRole.ASSISTANT, result.response)

    # 6. 异步更新用户画像（不阻塞响应）
    if os.getenv("PROFILE_AUTO_UPDATE", "true").lower() in {"1", "true", "yes", "on"}:
        asyncio.create_task(_memory.update_profile(user_id, conv_id))

    citations = []
    citation_ids = set()
    for trace in result.tool_traces:
        for citation in trace.get("knowledge_sources", []):
            key = citation.get("document_id") or (citation.get("policy_id"), citation.get("version"), citation.get("title"))
            if key not in citation_ids:
                citation_ids.add(key)
                citations.append(citation)

    _orchestrator.enrich_trace(
        result.request_id,
        citations=citations,
        guard={"changed": guard_result.changed, "violations": guard_result.violations},
        pending_actions=result.pending_actions,
    )

    return ChatResponse(
        conv_id=conv_id,
        request_id=result.request_id,
        response=result.response,
        intent=result.intent.value if result.intent else "other",
        intent_group=intent_result.intent_group,
        agent_type=result.agent_type.value,
        agent_types=[agent_type.value for agent_type in result.agent_types],
        primary_agent=result.primary_agent.value if result.primary_agent else result.agent_type.value,
        supporting_agents=[agent_type.value for agent_type in result.supporting_agents],
        tools_used=result.tools_used,
        routing_reason=result.routing_reason,
        routing_confidence=result.routing_confidence,
        escalated=result.escalated,
        latency_ms=round(result.latency_ms, 1),
        # 只有实际检索到可引用文档才标记为使用知识库；单纯调用失败不算。
        knowledge_used=bool(citations),
        entities=intent_result.entities,
        intent_confidence=round(intent_result.confidence, 4),
        intent_source_scores=intent_result.source_scores,
        safety_violations=guard_result.violations,
        pending_actions=result.pending_actions,
        citations=citations,
    )


async def _build_knowledge_context(message: str, intent=None, top_k: int = 3) -> tuple[str, bool]:
    """
    为 /chat 主链路构建 RAG 知识上下文。

    这里复用 MCPToolManager 的查询改写、并行召回、重排、fallback 能力。
    """
    if _tool_manager is None:
        return "", False
    if not _should_use_knowledge(message, intent=intent):
        return "", False
    try:
        result = await _tool_manager.search_with_rewrite("knowledge_search", message, top_k=top_k)
        if not result.success or not isinstance(result.data, list) or not result.data:
            return "", False

        parts = ["[知识库检索结果]"]
        used = False
        for i, item in enumerate(result.data[:top_k], start=1):
            if not isinstance(item, dict):
                continue
            title = str(item.get("title", "未命名文档"))
            content = str(item.get("content", "")).strip()
            score = item.get("score", "")
            if not content:
                continue
            used = True
            parts.append(f"{i}. 标题: {title}\n   相关度: {score}\n   内容: {content[:600]}")

        if not used:
            return "", False
        parts.append("请优先依据以上知识库内容回答；如果知识库内容不足，再结合通用客服能力说明。")
        return "\n".join(parts), True
    except Exception as ex:
        logger.warning(f"构建知识库上下文失败: {ex}")
        return "", False


def _should_use_knowledge(message: str, intent=None) -> bool:
    """跳过纯寒暄，业务类问题才检索知识库，避免无关 RAG 干扰回复。"""
    msg = (message or "").strip().lower()
    if not msg:
        return False
    intent_value = getattr(intent, "value", intent)
    if intent_value in {"greeting", "feedback", "escalation", "human_handoff", "other"}:
        return False
    if intent_value in {
        "query", "request", "technical", "billing", "account", "complaint",
        "order_status", "logistics", "refund", "invoice", "payment_issue",
        "account_security", "technical_login", "technical_crash",
    }:
        return True
    greetings = {"你好", "您好", "嗨", "hi", "hello", "hey", "早上好", "晚上好"}
    if msg in greetings:
        return False
    business_keywords = [
        "退款", "订单", "物流", "配送", "发票", "扣款", "支付", "账单", "订阅",
        "登录", "报错", "错误", "崩溃", "会员", "积分", "账户", "密码", "地址",
        "refund", "order", "invoice", "payment", "error", "login",
    ]
    return len(msg) >= 4 or any(kw in msg for kw in business_keywords)


@app.get("/monitor")
async def monitor_summary():
    """实时监控摘要：Agent 成功率、工具统计、告警、优化建议。"""
    if _monitor is None:
        raise HTTPException(503, "服务未就绪")
    return _monitor.summary()


@app.get("/trace/tool/{request_id}", response_model=ToolTraceResponse)
async def get_tool_trace(request_id: str):
    """查看某次请求的工具调用明细。"""
    if _orchestrator is None:
        raise HTTPException(503, "服务未就绪")
    trace = _orchestrator.get_tool_trace(request_id)
    return ToolTraceResponse(
        request_id=request_id,
        found=trace is not None,
        trace=trace or {},
    )


@app.get("/trace/tools", response_model=RecentToolTracesResponse)
async def list_recent_tool_traces(limit: int = 20):
    """查看最近 N 次请求的工具调用明细。"""
    if _orchestrator is None:
        raise HTTPException(503, "服务未就绪")
    return RecentToolTracesResponse(items=_orchestrator.get_recent_tool_traces(limit=limit))


@app.get("/metrics")
async def prometheus_metrics():
    """Prometheus 指标入口。"""
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.post("/search")
async def search(query: str, top_k: int = 5):
    """
    演示检索优化链路：查询改写 → 并行召回 → 重排 → Top-K。
    展示 MCP 工具调用的核心亮点。
    """
    if _tool_manager is None:
        raise HTTPException(503, "服务未就绪")
    result = await _tool_manager.search_with_rewrite("knowledge_search", query, top_k=top_k)
    return {"query": query, "results": result.data, "reranked": result.reranked}


class DocInput(BaseModel):
    """单篇文档输入。"""
    title:   str
    content: str
    metadata: Dict[str, Any] = Field(default_factory=dict)


class BatchDocInput(BaseModel):
    """批量文档导入请求体。"""
    documents: List[DocInput]


class EvalIntentInput(BaseModel):
    """意图识别评测用例。"""
    message: str
    expected_intent: str
    context: Optional[Dict[str, Any]] = None


class EvalDialogInput(BaseModel):
    """对话质量评测用例。question 单轮，turns 多轮。"""
    question: Optional[str] = None
    turns: Optional[List[str]] = None
    user_id: Optional[str] = None
    conv_id: Optional[str] = None


class EvalRunInput(BaseModel):
    """评测请求。为空时使用内置默认用例。"""
    intent_cases: Optional[List[EvalIntentInput]] = None
    dialog_cases: Optional[List[EvalDialogInput]] = None


class RefundPrepareInput(BaseModel):
    user_id: str
    order_id: str
    reason: str
    idempotency_key: Optional[str] = None


class CancelOrderPrepareInput(BaseModel):
    user_id: str
    order_id: str
    reason: str = "用户申请取消"
    idempotency_key: Optional[str] = None


class AddressChangePrepareInput(BaseModel):
    user_id: str
    order_id: str
    new_address: str
    idempotency_key: Optional[str] = None


class ActionConfirmInput(BaseModel):
    user_id: str


def _domain_payload(value: Any) -> Dict[str, Any]:
    """把领域 dataclass 和 Enum 转为 FastAPI 可序列化结构。"""
    from dataclasses import asdict, is_dataclass

    data = asdict(value) if is_dataclass(value) else value

    def convert(item: Any) -> Any:
        if hasattr(item, "value"):
            return item.value
        if isinstance(item, dict):
            return {key: convert(val) for key, val in item.items()}
        if isinstance(item, list):
            return [convert(val) for val in item]
        return item

    return convert(data)


@app.get("/commerce/orders/{order_id}", tags=["Commerce"])
async def commerce_order(order_id: str, user_id: str = "", authorization: Optional[str] = Header(default=None)):
    """查询结构化订单事实，并校验订单归属。"""
    if _commerce_service is None:
        raise HTTPException(503, "电商领域服务未初始化")
    result = await asyncio.to_thread(_commerce_service.get_order, _resolve_user_id(user_id, authorization), order_id)
    if not result.success:
        raise HTTPException(404, result.error)
    return _domain_payload(result)


@app.get("/commerce/orders/{order_id}/payments", tags=["Commerce"])
async def commerce_payments(order_id: str, user_id: str = "", authorization: Optional[str] = Header(default=None)):
    """查询支付流水；疑似重复扣款只作为核验信号。"""
    if _commerce_service is None:
        raise HTTPException(503, "电商领域服务未初始化")
    result = await asyncio.to_thread(_commerce_service.get_payment_records, _resolve_user_id(user_id, authorization), order_id)
    if not result.success:
        raise HTTPException(404, result.error)
    return _domain_payload(result)


@app.post("/commerce/refunds/prepare", tags=["Commerce"])
async def prepare_commerce_refund(body: RefundPrepareInput, authorization: Optional[str] = Header(default=None)):
    """预检退款并创建待确认动作。本接口不会直接创建退款。"""
    if _commerce_service is None:
        raise HTTPException(503, "电商领域服务未初始化")
    result = await asyncio.to_thread(
        _commerce_service.prepare_refund,
        _resolve_user_id(body.user_id, authorization),
        body.order_id,
        body.reason,
        body.idempotency_key,
    )
    return _domain_payload(result)


@app.post("/commerce/orders/cancel/prepare", tags=["Commerce"])
async def prepare_order_cancel(body: CancelOrderPrepareInput, authorization: Optional[str] = Header(default=None)):
    """创建待确认的订单取消动作，不直接修改订单。"""
    if _commerce_service is None:
        raise HTTPException(503, "电商领域服务未初始化")
    result = await asyncio.to_thread(
        _commerce_service.prepare_cancel_order, _resolve_user_id(body.user_id, authorization), body.order_id, body.reason, body.idempotency_key,
    )
    return _domain_payload(result)


@app.post("/commerce/orders/address/prepare", tags=["Commerce"])
async def prepare_order_address_change(body: AddressChangePrepareInput, authorization: Optional[str] = Header(default=None)):
    """创建待确认的地址修改动作，不直接修改地址。"""
    if _commerce_service is None:
        raise HTTPException(503, "电商领域服务未初始化")
    result = await asyncio.to_thread(
        _commerce_service.prepare_address_change, _resolve_user_id(body.user_id, authorization), body.order_id, body.new_address, body.idempotency_key,
    )
    return _domain_payload(result)


@app.post("/actions/{action_id}/confirm", tags=["Commerce"])
async def confirm_commerce_action(action_id: str, body: ActionConfirmInput, authorization: Optional[str] = Header(default=None)):
    """确认并执行待处理动作；重复确认不会重复创建退款。"""
    if _commerce_service is None:
        raise HTTPException(503, "电商领域服务未初始化")
    result = await asyncio.to_thread(_commerce_service.confirm_action, _resolve_user_id(body.user_id, authorization), action_id)
    return _domain_payload(result)


@app.get("/commerce/handoffs", tags=["Commerce"])
async def list_handoffs(user_id: str = "", authorization: Optional[str] = Header(default=None)):
    if _commerce_service is None:
        raise HTTPException(503, "电商领域服务未初始化")
    return {"items": await asyncio.to_thread(_commerce_service.list_handoff_tickets, _resolve_user_id(user_id, authorization))}


@app.get("/commerce/handoffs/{ticket_id}", tags=["Commerce"])
async def get_handoff(ticket_id: str, user_id: str = "", authorization: Optional[str] = Header(default=None)):
    if _commerce_service is None:
        raise HTTPException(503, "电商领域服务未初始化")
    result = await asyncio.to_thread(_commerce_service.get_handoff_ticket, _resolve_user_id(user_id, authorization), ticket_id)
    if result is None:
        raise HTTPException(404, "工单不存在或不属于当前用户")
    return result


@app.post("/knowledge/add", tags=["知识库"])
async def add_knowledge(body: BatchDocInput):
    """
    批量导入文档到知识库。

    文档会自动切片（每片 500 字）并存入 ChromaDB，ChromaDB 内置 Embedding 模型自动向量化。

    示例请求体：
    ```json
    {
      "documents": [
        {"title": "退款政策", "content": "用户在购买后 7 天内可以申请无理由退款..."},
        {"title": "配送说明", "content": "标准配送 3-5 个工作日..."}
      ]
    }
    ```
    """
    tool = _tool_manager._tools.get("knowledge_search") if _tool_manager else None
    if tool is None:
        raise HTTPException(503, "知识库未初始化")
    kb = tool.handler.__self__
    count = await kb.add_documents_async([
        {"title": d.title, "content": d.content, "metadata": d.metadata}
        for d in body.documents
    ])
    total = await kb.doc_count_async()
    return {"message": f"成功导入 {count} 个文档片段", "added_chunks": count, "total_chunks": total}


@app.post("/knowledge/upload", tags=["知识库"])
async def upload_knowledge(file: UploadFile = File(...)):
    """
    上传文件导入知识库。

    支持格式：
    - `.txt` / `.md`：整个文件作为一篇文档，文件名作为标题
    - `.json`：JSON 数组格式 `[{"title": "...", "content": "..."}, ...]`

    文件大小限制：10MB
    """
    tool = _tool_manager._tools.get("knowledge_search") if _tool_manager else None
    if tool is None:
        raise HTTPException(503, "知识库未初始化")
    kb = tool.handler.__self__

    content = await file.read()
    if len(content) > 10 * 1024 * 1024:
        raise HTTPException(413, "文件大小超过 10MB 限制")

    text = content.decode("utf-8", errors="ignore")
    filename = file.filename or "unknown"

    if filename.endswith(".json"):
        import json as _json
        try:
            docs = _json.loads(text)
            if not isinstance(docs, list):
                raise HTTPException(400, "JSON 文件应为数组格式: [{title, content}, ...]")
        except _json.JSONDecodeError as e:
            raise HTTPException(400, f"JSON 解析失败: {e}")
    else:
        # txt / md：整个文件作为一篇文档
        title = filename.rsplit(".", 1)[0] if "." in filename else filename
        docs = [{"title": title, "content": text}]

    count = await kb.add_documents_async(docs)
    total = await kb.doc_count_async()
    return {
        "message": f"文件 {filename} 导入成功",
        "added_chunks": count,
        "total_chunks": total,
    }


@app.get("/knowledge/stats", tags=["知识库"])
async def knowledge_stats():
    """查看知识库统计信息（文档片段总数）。"""
    tool = _tool_manager._tools.get("knowledge_search") if _tool_manager else None
    if tool is None:
        raise HTTPException(503, "知识库未初始化")
    kb = tool.handler.__self__
    return {"total_chunks": await kb.doc_count_async()}


@app.post("/eval/run")
async def run_eval(body: Optional[EvalRunInput] = None):
    """运行内置评测用例，返回评测报告。"""
    if _evaluator is None:
        raise HTTPException(503, "服务未就绪")
    from evaluation.evaluator import DEFAULT_DIALOG_CASES, DEFAULT_INTENT_CASES, IntentTestCase

    if body and body.intent_cases is not None:
        intent_cases = [
            IntentTestCase(
                message=c.message,
                expected_intent=c.expected_intent,
                context=c.context,
            )
            for c in body.intent_cases
        ]
    else:
        intent_cases = DEFAULT_INTENT_CASES

    if body and body.dialog_cases is not None:
        dialog_cases = [
            c.model_dump(exclude_none=True)
            for c in body.dialog_cases
        ]
    else:
        dialog_cases = DEFAULT_DIALOG_CASES

    report = await _evaluator.run(
        intent_cases=intent_cases,
        dialog_cases=dialog_cases,
    )
    return {
        "pass_rate":       report.pass_rate,
        "total":           report.total,
        "passed":          report.passed,
        "avg_scores":      report.avg_scores,
        "regressions":     report.regressions,
        "recommendations": report.recommendations,
        "results": [
            {
                "test_id": r.test_id,
                "passed": r.passed,
                "scores": r.scores,
                "detail": r.detail,
                "metadata": r.metadata,
            }
            for r in report.results
        ],
    }


# ── 交互式 CLI ────────────────────────────────────────────────────────────────
async def _cli():
    print(BANNER)
    print("CommerceMind CLI — 输入 quit 退出\n")

    from agents.agent_orchestrator import AgentOrchestrator, Request
    from memory.conversation_memory import MemoryManager, MsgRole
    from core.skill_loader import SkillManager

    cfg = _anthropic_cfg()
    skill_manager = SkillManager(
        root_dir=os.getenv("COMMERCEMIND_SKILLS_DIR", str(pathlib.Path(_ROOT) / "skills")),
        max_prompt_chars=int(os.getenv("COMMERCEMIND_SKILLS_MAX_PROMPT_CHARS", "5000")),
    )
    skill_manager.load()
    orch = AgentOrchestrator(
        api_key=cfg["api_key"],
        base_url=cfg.get("base_url"),
        model=cfg["model"],
        skill_manager=skill_manager,
    )
    mem  = MemoryManager(
        redis_url=os.getenv("REDIS_URL", "redis://localhost:6379/0"),
        chroma_host=os.getenv("CHROMA_HOST", "localhost"),
        chroma_port=int(os.getenv("CHROMA_PORT", "8000")),
        chroma_path=os.getenv("CHROMA_PERSIST_DIRECTORY", "/tmp/chroma"),
        api_key=cfg["api_key"],
        base_url=cfg.get("base_url"),
        model=cfg["model"],
    )

    user_id, conv_id = "cli_user", str(uuid.uuid4())

    while True:
        try:
            msg = input("你: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n再见 ʕ•ᴥ•ʔ")
            break
        if not msg or msg.lower() in ("quit", "exit", "退出"):
            print("再见 ʕ•ᴥ•ʔ")
            break

        ctx = await mem.get_context(user_id, conv_id, query=msg)
        history = [
            {"role": m.role.value, "content": m.content}
            for m in ctx.recent_messages[-5:]
        ] if ctx.recent_messages else None
        req = Request(message=msg, user_id=user_id, conv_id=conv_id, context=ctx.to_prompt_text(), history=history)
        result = await orch.run(req)

        await mem.add_message(user_id, conv_id, MsgRole.USER, msg)
        await mem.add_message(user_id, conv_id, MsgRole.ASSISTANT, result.response)

        print(f"\nCommerceMind [{result.agent_type.value}]: {result.response}\n")

    await mem.close()


if __name__ == "__main__":
    if "--cli" in sys.argv:
        asyncio.run(_cli())
    else:
        uvicorn.run(
            "api.main:app",
            host=os.getenv("API_HOST", "0.0.0.0"),
            port=int(os.getenv("API_PORT", "8000")),
            reload=os.getenv("APP_ENV") == "development",
        )
