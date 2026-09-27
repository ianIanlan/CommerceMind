import asyncio
from collections import deque

from agents.agent_orchestrator import (
    AgentResponse,
    AgentType,
    AgentOrchestrator,
    Request,
    RoutingDecision,
)
from core.intent_recognizer import IntentCategory, UrgencyLevel
from core.intent_recognizer import IntentRecognizer


def _request():
    return Request(
        message="登录报 401，而且订单被扣了两次",
        user_id="u1",
        conv_id="c1",
        intent=IntentCategory.DUPLICATE_PAYMENT,
        intent_group="billing",
        urgency=UrgencyLevel.MEDIUM,
        intent_confidence=0.9,
    )


def test_single_agent_result_exposes_stage_timings():
    orchestrator = AgentOrchestrator.__new__(AgentOrchestrator)
    orchestrator._recent_tool_traces = deque(maxlen=10)
    orchestrator._route_decision = lambda req: RoutingDecision(AgentType.BILLING, confidence=0.9)

    async def execute(req, agent_type):
        return AgentResponse(agent_type, "账单结果", True, latency_ms=12.5)

    orchestrator._execute = execute
    result = asyncio.run(orchestrator.run(_request()))

    assert result.stage_timings_ms["routing"] >= 0
    assert result.stage_timings_ms["agents_parallel_wall"] >= 0
    assert result.stage_timings_ms["composer"] == 0.0
    assert result.stage_timings_ms["agent_individual"] == {"billing": 12.5}
    assert orchestrator.get_tool_trace(result.request_id)["stage_timings_ms"] == result.stage_timings_ms


def test_parallel_result_separates_agent_wall_time_and_composer():
    orchestrator = AgentOrchestrator.__new__(AgentOrchestrator)
    orchestrator._recent_tool_traces = deque(maxlen=10)

    async def execute(req, agent_type):
        await asyncio.sleep(0)
        return AgentResponse(agent_type, f"{agent_type.value} 结果", True, latency_ms=7.0)

    class Composer:
        async def compose(self, req, responses):
            await asyncio.sleep(0)
            return "合并结果"

    orchestrator._execute = execute
    orchestrator._composer = Composer()
    decision = RoutingDecision(
        AgentType.BILLING,
        supporting_agents=[AgentType.TECHNICAL],
        confidence=0.8,
    )

    result = asyncio.run(orchestrator.run_parallel(_request(), decision, routing_ms=1.2))

    assert result.stage_timings_ms["routing"] == 1.2
    assert result.stage_timings_ms["agents_parallel_wall"] >= 0
    assert result.stage_timings_ms["composer"] >= 0
    assert result.stage_timings_ms["agent_individual"] == {"billing": 7.0, "technical": 7.0}


def test_explicit_handoff_uses_pattern_fast_path(monkeypatch):
    monkeypatch.setenv("INTENT_PATTERN_FAST_PATH", "true")
    recognizer = IntentRecognizer(api_key="test-key")

    async def forbidden(*args, **kwargs):
        raise AssertionError("explicit handoff must not call the LLM")

    recognizer._llm_recognize = forbidden
    result = asyncio.run(recognizer.recognize("请马上转人工客服"))

    assert result.intent is IntentCategory.HUMAN_HANDOFF
    assert result.source_scores["fast_path"] == 1.0
