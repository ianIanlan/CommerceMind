"""Deterministic RAG retrieval ablation with citation-oriented metrics."""
from __future__ import annotations

import json
import statistics
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from mcp.local_knowledge_base import LocalKnowledgeBase


@dataclass(frozen=True)
class RagCase:
    case_id: str
    query: str
    category: str
    expected_policy_id: Optional[str]


@dataclass
class RagVariantResult:
    name: str
    description: str
    hit_at_1: float
    hit_at_3: float
    mrr: float
    abstention_accuracy: float
    wrong_domain_rate: float
    avg_latency_ms: float
    p95_latency_ms: float
    errors: List[Dict[str, Any]]


@dataclass
class RagAblationReport:
    dataset_size: int
    answerable_cases: int
    unanswerable_cases: int
    variants: List[RagVariantResult]


EXPANSIONS = {
    "退钱": "退款 退货 售后",
    "钱没回来": "退款 原路退回 到账",
    "包裹": "订单 物流 配送 快递",
    "运费": "配送 运费 订单",
    "扣了两遍": "重复扣款 支付流水",
    "登不上": "登录失败 认证 密码",
    "小票": "发票 账单",
    "成长值": "会员 积分",
}


class RagAblationRunner:
    VARIANTS = (
        ("no_rag", "不检索知识库"),
        ("lexical", "基础词法检索，不做领域过滤"),
        ("filtered", "词法检索 + 领域过滤"),
        ("expanded_filtered", "确定性查询扩展 + 领域过滤"),
    )

    def __init__(self, knowledge_base: Optional[LocalKnowledgeBase] = None):
        self.kb = knowledge_base or LocalKnowledgeBase()

    def run(self, cases: Sequence[RagCase], top_k: int = 3) -> RagAblationReport:
        return RagAblationReport(
            dataset_size=len(cases),
            answerable_cases=sum(case.expected_policy_id is not None for case in cases),
            unanswerable_cases=sum(case.expected_policy_id is None for case in cases),
            variants=[self._evaluate(name, description, cases, top_k) for name, description in self.VARIANTS],
        )

    def _evaluate(self, name: str, description: str, cases: Sequence[RagCase], top_k: int) -> RagVariantResult:
        ranks: List[int] = []
        abstentions: List[bool] = []
        wrong_domains = 0
        returned = 0
        latencies: List[float] = []
        errors: List[Dict[str, Any]] = []
        for case in cases:
            started = time.monotonic()
            results = self._retrieve(name, case, top_k)
            latencies.append((time.monotonic() - started) * 1000)
            ids = [str(item.get("policy_id", "")) for item in results]
            if case.expected_policy_id is None:
                abstentions.append(not results)
                if results:
                    errors.append({"case_id": case.case_id, "query": case.query, "error": "应拒答但召回", "retrieved": ids})
            else:
                rank = ids.index(case.expected_policy_id) + 1 if case.expected_policy_id in ids else 0
                ranks.append(rank)
                if not rank:
                    errors.append({"case_id": case.case_id, "query": case.query, "expected": case.expected_policy_id, "retrieved": ids})
            for item in results:
                returned += 1
                if case.category and item.get("category") not in {case.category, "general"}:
                    wrong_domains += 1
        answerable = max(len(ranks), 1)
        return RagVariantResult(
            name=name,
            description=description,
            hit_at_1=round(sum(rank == 1 for rank in ranks) / answerable, 4),
            hit_at_3=round(sum(0 < rank <= 3 for rank in ranks) / answerable, 4),
            mrr=round(sum(1 / rank for rank in ranks if rank) / answerable, 4),
            abstention_accuracy=round(sum(abstentions) / len(abstentions), 4) if abstentions else 0.0,
            wrong_domain_rate=round(wrong_domains / returned, 4) if returned else 0.0,
            avg_latency_ms=round(statistics.mean(latencies), 3) if latencies else 0.0,
            p95_latency_ms=round(self._percentile(latencies, 95), 3),
            errors=errors,
        )

    def _retrieve(self, name: str, case: RagCase, top_k: int) -> List[Dict[str, Any]]:
        if name == "no_rag":
            return []
        query = case.query
        if name == "expanded_filtered":
            query = " ".join([query] + [value for key, value in EXPANSIONS.items() if key in query])
        category = case.category if name in {"filtered", "expanded_filtered"} else ""
        return self.kb.search(query, top_k=top_k, category=category)

    @staticmethod
    def _percentile(values: Sequence[float], percentile: float) -> float:
        if not values:
            return 0.0
        ordered = sorted(values)
        index = min(len(ordered) - 1, round((len(ordered) - 1) * percentile / 100))
        return ordered[index]


def load_rag_cases(path: Path) -> List[RagCase]:
    cases = [RagCase(**json.loads(line)) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    ids = [case.case_id for case in cases]
    if len(ids) != len(set(ids)):
        raise ValueError("RAG 数据集包含重复 case_id")
    return cases


def write_rag_report(report: RagAblationReport, json_path: Path, markdown_path: Path) -> None:
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(asdict(report), ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [
        "# RAG 检索消融实验", "",
        f"数据集：{report.dataset_size} 条；可回答 {report.answerable_cases} 条；应拒答 {report.unanswerable_cases} 条。", "",
        "| 方案 | Hit@1 | Hit@3 | MRR | 拒答准确率 | 错域率 | P95 延迟(ms) |", "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for item in report.variants:
        lines.append(f"| {item.description} | {item.hit_at_1:.2%} | {item.hit_at_3:.2%} | {item.mrr:.3f} | {item.abstention_accuracy:.2%} | {item.wrong_domain_rate:.2%} | {item.p95_latency_ms:.3f} |")
    lines.extend(["", "## 错误样本", ""])
    for item in report.variants:
        lines.append(f"### {item.name}")
        lines.extend([f"- `{error['case_id']}` {error}" for error in item.errors] or ["- 无"])
        lines.append("")
    markdown_path.write_text("\n".join(lines), encoding="utf-8")
