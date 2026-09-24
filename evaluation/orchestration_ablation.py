"""Offline comparison of single-agent and conditional multi-agent routing."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, List, Sequence

from agents.agent_orchestrator import AgentOrchestrator, AgentType, Request
from core.intent_recognizer import IntentCategory, UrgencyLevel


@dataclass(frozen=True)
class OrchestrationCase:
    case_id: str
    message: str
    intent: str
    expected_agents: List[str]


@dataclass
class OrchestrationVariant:
    name: str
    exact_match: float
    domain_recall: float
    unnecessary_agent_rate: float
    avg_agents: float
    errors: List[Dict]


def load_orchestration_cases(path: Path) -> List[OrchestrationCase]:
    return [OrchestrationCase(**json.loads(line)) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


class OrchestrationAblationRunner:
    def __init__(self):
        self.orchestrator = AgentOrchestrator.__new__(AgentOrchestrator)
        self.orchestrator._pool = {agent: [object()] for agent in AgentType}

    def run(self, cases: Sequence[OrchestrationCase]) -> List[OrchestrationVariant]:
        variants = {"single_agent": [], "conditional_multi_agent": []}
        for case in cases:
            req = Request(
                message=case.message, user_id="eval", conv_id=case.case_id,
                intent=IntentCategory(case.intent), intent_group="eval", urgency=UrgencyLevel.LOW,
                intent_confidence=0.9,
            )
            decision = self.orchestrator._route_decision(req)
            variants["single_agent"].append([decision.primary_agent.value])
            variants["conditional_multi_agent"].append([agent.value for agent in decision.agent_types])
        return [self._metrics(name, predictions, cases) for name, predictions in variants.items()]

    @staticmethod
    def _metrics(name: str, predictions: Sequence[List[str]], cases: Sequence[OrchestrationCase]) -> OrchestrationVariant:
        exact = 0
        recalled = 0
        expected_total = 0
        unnecessary = 0
        predicted_total = 0
        errors = []
        for predicted, case in zip(predictions, cases):
            pred_set, expected = set(predicted), set(case.expected_agents)
            exact += pred_set == expected
            recalled += len(pred_set & expected)
            expected_total += len(expected)
            unnecessary += len(pred_set - expected)
            predicted_total += len(pred_set)
            if pred_set != expected:
                errors.append({"case_id": case.case_id, "expected": sorted(expected), "predicted": sorted(pred_set)})
        total = max(len(cases), 1)
        return OrchestrationVariant(
            name=name,
            exact_match=round(exact / total, 4),
            domain_recall=round(recalled / max(expected_total, 1), 4),
            unnecessary_agent_rate=round(unnecessary / max(predicted_total, 1), 4),
            avg_agents=round(predicted_total / total, 3),
            errors=errors,
        )


def write_orchestration_report(results: List[OrchestrationVariant], json_path: Path, markdown_path: Path) -> None:
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps([asdict(item) for item in results], ensure_ascii=False, indent=2), encoding="utf-8")
    lines = ["# Agent 编排消融实验", "", "| 方案 | 精确匹配 | 领域召回 | 多余 Agent 率 | 平均 Agent 数 |", "|---|---:|---:|---:|---:|"]
    for item in results:
        lines.append(f"| {item.name} | {item.exact_match:.2%} | {item.domain_recall:.2%} | {item.unnecessary_agent_rate:.2%} | {item.avg_agents:.2f} |")
    markdown_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
