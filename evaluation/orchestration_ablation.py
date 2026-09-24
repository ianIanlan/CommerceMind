"""Offline comparison of single-agent and conditional multi-agent routing."""
from __future__ import annotations

import json
import math
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
    exact_match_ci95: List[float]
    micro_precision: float
    micro_f1: float
    single_domain_exact: float
    multi_domain_exact: float
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
            exact_match_ci95=[round(value, 4) for value in OrchestrationAblationRunner._wilson(exact, len(cases))],
            micro_precision=round(recalled / max(predicted_total, 1), 4),
            micro_f1=round(2 * recalled / max(expected_total + predicted_total, 1), 4),
            single_domain_exact=OrchestrationAblationRunner._group_exact(predictions, cases, multi=False),
            multi_domain_exact=OrchestrationAblationRunner._group_exact(predictions, cases, multi=True),
            errors=errors,
        )

    @staticmethod
    def _group_exact(
        predictions: Sequence[List[str]], cases: Sequence[OrchestrationCase], multi: bool
    ) -> float:
        selected = [
            (prediction, case)
            for prediction, case in zip(predictions, cases)
            if (len(case.expected_agents) > 1) is multi
        ]
        if not selected:
            return 0.0
        return round(
            sum(set(prediction) == set(case.expected_agents) for prediction, case in selected) / len(selected),
            4,
        )

    @staticmethod
    def _wilson(successes: int, total: int, z: float = 1.96) -> tuple[float, float]:
        if total == 0:
            return 0.0, 0.0
        proportion = successes / total
        denominator = 1 + z * z / total
        centre = (proportion + z * z / (2 * total)) / denominator
        margin = z * math.sqrt(
            proportion * (1 - proportion) / total + z * z / (4 * total * total)
        ) / denominator
        return max(0.0, centre - margin), min(1.0, centre + margin)


def write_orchestration_report(results: List[OrchestrationVariant], json_path: Path, markdown_path: Path) -> None:
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps([asdict(item) for item in results], ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [
        "# Agent 编排消融实验", "",
        "本实验只评价路由覆盖，不评价最终回答质量。置信区间为 exact match 的 Wilson 95% CI。", "",
        "该数据集是用于开发和回归的合成集，路由规则已根据其中的错误样本调整；100% 不能解释为未知流量上的泛化准确率。", "",
        "| 方案 | 精确匹配 (95% CI) | Micro-F1 | 领域召回 | 多余 Agent 率 | 单域/多域精确匹配 | 平均 Agent 数 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for item in results:
        lines.append(
            f"| {item.name} | {item.exact_match:.2%} "
            f"[{item.exact_match_ci95[0]:.2%}, {item.exact_match_ci95[1]:.2%}] | "
            f"{item.micro_f1:.2%} | {item.domain_recall:.2%} | {item.unnecessary_agent_rate:.2%} | "
            f"{item.single_domain_exact:.2%}/{item.multi_domain_exact:.2%} | {item.avg_agents:.2f} |"
        )
    markdown_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
