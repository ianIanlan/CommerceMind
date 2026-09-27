"""Unified offline benchmark for intent, routing, retrieval, and policy safety."""
from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from agents.agent_orchestrator import (
    AfterSalesAgent, AgentType, BillingAgent, GeneralAgent, OrderAgent, Request, TechnicalAgent,
)
from agents.tools import build_after_sales_tools, build_commerce_billing_tools, build_order_tools
from commerce.models import ActionStatus
from commerce.service import CommerceService
from commerce.store import CommerceStore
from core.intent_recognizer import IntentCategory, IntentRecognizer, UrgencyLevel
from evaluation.ablation import AblationCase, IntentAblationRunner
from evaluation.orchestration_ablation import OrchestrationAblationRunner, OrchestrationCase
from evaluation.rag_ablation import RagAblationRunner, RagCase


class _NoLLM:
    async def create(self, **_: Any):
        raise AssertionError("offline policy evaluation must not call an LLM")


@dataclass
class SectionResult:
    name: str
    total: int
    passed: int
    primary_metric: str
    value: float
    details: dict[str, Any]


def load_benchmark(path: Path) -> list[dict[str, Any]]:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    ids = [row["case_id"] for row in rows]
    if len(ids) != len(set(ids)):
        raise ValueError("benchmark contains duplicate case_id")
    return rows


class UnifiedBenchmarkRunner:
    def __init__(self, recognizer: IntentRecognizer, live_llm: bool = False):
        self.recognizer = recognizer
        self.live_llm = live_llm

    async def run(self, rows: list[dict[str, Any]]) -> list[SectionResult]:
        return [
            await self._intent([row for row in rows if row["task"] == "intent"]),
            self._routing([row for row in rows if row["task"] == "routing"]),
            self._rag([row for row in rows if row["task"] == "rag"]),
            self._policy([row for row in rows if row["task"] == "policy"]),
        ]

    async def _intent(self, rows: list[dict[str, Any]]) -> SectionResult:
        cases = [AblationCase(row["case_id"], row["input"]["message"], row["expected"]["intent"], row["tags"]) for row in rows]
        report = await IntentAblationRunner(self.recognizer, live_llm=self.live_llm).run(cases)
        selected = "production_fusion" if self.live_llm else "embedding_pattern"
        result = next(item for item in report.variants if item.name == selected)
        name = "intent_production_fusion" if self.live_llm else "intent_local_fallback"
        return SectionResult(name, len(rows), result.correct, "accuracy", result.accuracy, {
            "macro_f1": result.macro_f1, "p95_latency_ms": result.p95_latency_ms,
            "embedding_backends": report.embedding_backends, "errors": result.errors,
        })

    @staticmethod
    def _routing(rows: list[dict[str, Any]]) -> SectionResult:
        cases = [OrchestrationCase(row["case_id"], row["input"]["message"], row["input"]["intent"], row["expected"]["agents"]) for row in rows]
        result = next(item for item in OrchestrationAblationRunner().run(cases) if item.name == "conditional_multi_agent")
        passed = len(rows) - len(result.errors)
        return SectionResult("conditional_multi_agent_routing", len(rows), passed, "exact_match", result.exact_match, {
            "micro_f1": result.micro_f1, "domain_recall": result.domain_recall,
            "unnecessary_agent_rate": result.unnecessary_agent_rate, "errors": result.errors,
        })

    @staticmethod
    def _rag(rows: list[dict[str, Any]]) -> SectionResult:
        cases = [RagCase(row["case_id"], row["input"]["query"], row["input"]["category"], row["expected"]["policy_id"]) for row in rows]
        report = RagAblationRunner().run(cases)
        result = next(item for item in report.variants if item.name == "expanded_filtered")
        passed = len(rows) - len(result.errors)
        return SectionResult("rag_expanded_filtered", len(rows), passed, "case_success_rate", round(passed / len(rows), 4), {
            "hit_at_1": result.hit_at_1, "hit_at_3": result.hit_at_3, "mrr": result.mrr,
            "abstention_accuracy": result.abstention_accuracy, "wrong_domain_rate": result.wrong_domain_rate,
            "p95_latency_ms": result.p95_latency_ms, "errors": result.errors,
        })

    def _policy(self, rows: list[dict[str, Any]]) -> SectionResult:
        failures = []
        for row in rows:
            try:
                self._check_policy(row)
            except (AssertionError, KeyError, ValueError) as exc:
                failures.append({"case_id": row["case_id"], "error": str(exc) or type(exc).__name__})
        passed = len(rows) - len(failures)
        return SectionResult("tool_and_transaction_policy", len(rows), passed, "pass_rate", round(passed / len(rows), 4), {"errors": failures})

    def _agent(self, name: str, service: CommerceService):
        cls = {
            "general": GeneralAgent, "technical": TechnicalAgent, "billing": BillingAgent,
            "order": OrderAgent, "after_sales": AfterSalesAgent,
        }[name]
        agent = cls(SimpleNamespace(messages=_NoLLM()), "offline")
        domain_builders = {
            "billing": build_commerce_billing_tools,
            "order": build_order_tools,
            "after_sales": build_after_sales_tools,
        }
        if name in domain_builders:
            agent.set_domain_tools(domain_builders[name](service))
        return agent

    def _check_policy(self, row: dict[str, Any]) -> None:
        data, expected = row["input"], row["expected"]
        store = CommerceStore(":memory:")
        store.seed_demo_data()
        service = CommerceService(store)
        if data["check"] in {"required_tools", "tool_scope"}:
            agent = self._agent(data["agent"], service)
            if data["check"] == "tool_scope":
                assert (data["tool"] in agent.get_tools()) is expected["allowed"]
                return
            req = Request(
                message="offline policy check", user_id="demo-user", conv_id=row["case_id"],
                intent=IntentCategory(data["intent"]), urgency=UrgencyLevel.LOW,
                intent_group="eval", entities=data["entities"], intent_confidence=1.0,
            )
            assert [name for name, _ in agent._required_tool_calls(req)] == expected["tools"]
            return
        op = data["operation"]
        if op == "ownership_denied":
            assert not service.get_order("demo-user", "ORD-OTHER").success
        elif op == "refund_requires_confirmation":
            action = service.prepare_refund("demo-user", "ORD-10001", "评测", row["case_id"])
            assert action.status is ActionStatus.AWAITING_CONFIRMATION
            assert not store.fetch_all("SELECT * FROM refund_requests")
        elif op == "refund_confirmation_idempotent":
            action = service.prepare_refund("demo-user", "ORD-10001", "评测", row["case_id"])
            first = service.confirm_action("demo-user", action.action_id)
            second = service.confirm_action("demo-user", action.action_id)
            assert first.status is ActionStatus.SUCCEEDED and first.resource_id == second.resource_id
            assert len(store.fetch_all("SELECT * FROM refund_requests")) == 1
        elif op == "cancel_requires_confirmation":
            action = service.prepare_cancel_order("demo-user", "ORD-10005", "评测", row["case_id"])
            assert action.status is ActionStatus.AWAITING_CONFIRMATION
            assert service.get_order("demo-user", "ORD-10005").data["status"] == "paid"
        elif op == "shipped_address_rejected":
            assert service.prepare_address_change("demo-user", "ORD-10002", "新地址").status is ActionStatus.FAILED
        elif op == "missing_order_rejected":
            assert service.prepare_refund("demo-user", "ORD-NOT-FOUND", "评测", row["case_id"]).status is ActionStatus.FAILED
        elif op == "cross_user_refund_rejected":
            assert service.prepare_refund("demo-user", "ORD-OTHER", "评测", row["case_id"]).status is ActionStatus.FAILED
        elif op == "handoff_persistent":
            ticket = service.create_handoff_ticket("demo-user", "c1", row["case_id"], "人工", "human_handoff", "HIGH", {})
            assert service.get_handoff_ticket("demo-user", ticket["ticket_id"])["status"] == "open"
        else:
            raise ValueError(f"unknown transaction operation: {op}")


def write_report(rows: list[dict[str, Any]], sections: list[SectionResult], dataset_path: Path, json_path: Path, markdown_path: Path) -> None:
    digest = hashlib.sha256(dataset_path.read_bytes()).hexdigest()
    payload = {"dataset": str(dataset_path), "dataset_sha256": digest, "dataset_size": len(rows), "sections": [asdict(item) for item in sections]}
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [
        "# CommerceMind 统一评测报告 v1", "",
        f"- 数据集：{len(rows)} 条；SHA-256：`{digest}`",
        "- 四个任务使用不同指标，不计算会掩盖问题的跨任务总分。", "",
        "| 评测域 | 用例 | 通过 | 主指标 | 结果 |", "|---|---:|---:|---|---:|",
    ]
    for item in sections:
        lines.append(f"| {item.name} | {item.total} | {item.passed} | {item.primary_metric} | {item.value:.2%} |")
    lines += [
        "", "## 指标细节", "",
        f"- 意图{'生产融合' if sections[0].name == 'intent_production_fusion' else '本地降级'}：Macro-F1 {sections[0].details['macro_f1']:.2%}，P95 {sections[0].details['p95_latency_ms']:.3f} ms。",
        f"- 编排：Micro-F1 {sections[1].details['micro_f1']:.2%}，领域召回 {sections[1].details['domain_recall']:.2%}，多余 Agent 率 {sections[1].details['unnecessary_agent_rate']:.2%}。",
        f"- RAG：Hit@1 {sections[2].details['hit_at_1']:.2%}，Hit@3 {sections[2].details['hit_at_3']:.2%}，MRR {sections[2].details['mrr']:.3f}，拒答准确率 {sections[2].details['abstention_accuracy']:.2%}。",
        "", "## 证据边界", "",
        "- 数据均为仓库内合成数据，不含生产用户对话。", "- development/robustness 用例参与过规则迭代；holdout 用例被冻结，但尚未双人独立标注。",
        (
            "- 意图结果来自实时 LLM + Embedding + Pattern 生产融合；受当前第三方模型版本与网络状态影响。"
            if sections[0].name == "intent_production_fusion"
            else "- 意图结果是无 LLM 时的本地降级能力，不代表线上三路融合的真实生产准确率。"
        ),
        "- Policy 用例验证确定性权限和状态机断言，不评价自然语言回答质量。",
    ]
    errors = [(item.name, error) for item in sections for error in item.details.get("errors", [])]
    lines += ["", "## 错误样本", ""] + ([f"- `{name}` {error}" for name, error in errors] if errors else ["- 无"])
    markdown_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
