"""Reproducible intent-recognition ablation experiments."""
from __future__ import annotations

import asyncio
import json
import statistics
import time
import math
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from core.intent_recognizer import IntentCategory, IntentRecognizer


@dataclass(frozen=True)
class AblationVariant:
    name: str
    sources: Tuple[str, ...]
    weights: Dict[str, float]
    description: str


DEFAULT_VARIANTS = (
    AblationVariant("pattern_only", ("pattern",), {"pattern": 1.0}, "仅关键词/规则"),
    AblationVariant("embedding_only", ("embedding",), {"embedding": 1.0}, "仅本地向量相似度"),
    AblationVariant("llm_only", ("llm",), {"llm": 1.0}, "仅 LLM 语义识别"),
    AblationVariant(
        "embedding_pattern", ("embedding", "pattern"),
        {"embedding": 0.67, "pattern": 0.33}, "无 LLM 的本地双路降级",
    ),
    AblationVariant(
        "llm_pattern", ("llm", "pattern"),
        {"llm": 0.85, "pattern": 0.15}, "LLM + 确定性关键词",
    ),
    AblationVariant(
        "weighted_full", ("llm", "embedding", "pattern"),
        {"llm": 0.7, "embedding": 0.2, "pattern": 0.1}, "朴素三路加权",
    ),
    AblationVariant(
        "production_fusion", ("llm", "embedding", "pattern"),
        {"llm": 0.7, "embedding": 0.2, "pattern": 0.1}, "生产融合（阈值、细粒度优先和降级）",
    ),
)


@dataclass
class AblationCase:
    case_id: str
    message: str
    expected_intent: str
    tags: List[str] = field(default_factory=list)


@dataclass
class VariantResult:
    name: str
    description: str
    total: int
    correct: int
    accuracy: float
    accuracy_ci95: List[float]
    macro_f1: float
    avg_latency_ms: float
    p50_latency_ms: float
    p95_latency_ms: float
    confusion_matrix: Dict[str, Dict[str, int]]
    errors: List[Dict[str, Any]]


@dataclass
class AblationReport:
    dataset_size: int
    live_llm: bool
    variants: List[VariantResult]
    source_latency_ms: Dict[str, float]
    source_metrics: Dict[str, Dict[str, float]]
    label_distribution: Dict[str, int]


class IntentAblationRunner:
    def __init__(self, recognizer: IntentRecognizer, live_llm: bool = True):
        self.recognizer = recognizer
        self.live_llm = live_llm

    async def run(
        self,
        cases: Sequence[AblationCase],
        variants: Sequence[AblationVariant] = DEFAULT_VARIANTS,
    ) -> AblationReport:
        source_outputs: List[Dict[str, Dict[str, Any]]] = []
        source_latencies: Dict[str, List[float]] = {"llm": [], "embedding": [], "pattern": []}

        for case in cases:
            outputs: Dict[str, Dict[str, Any]] = {}
            started = time.monotonic()
            outputs["pattern"] = self.recognizer._pattern_recognize(case.message)
            source_latencies["pattern"].append((time.monotonic() - started) * 1000)

            started = time.monotonic()
            outputs["embedding"] = await self.recognizer._embedding_recognize(case.message)
            source_latencies["embedding"].append((time.monotonic() - started) * 1000)

            if self.live_llm:
                started = time.monotonic()
                outputs["llm"] = await self.recognizer._llm_recognize(case.message, None)
                source_latencies["llm"].append((time.monotonic() - started) * 1000)
            else:
                outputs["llm"] = {
                    "intent": IntentCategory.OTHER,
                    "confidence": 0.0,
                    "failed": True,
                    "reasoning": "offline ablation",
                }
            source_outputs.append(outputs)

        results = [
            self._evaluate_variant(variant, cases, source_outputs, source_latencies)
            for variant in variants
            if self.live_llm or "llm" not in variant.sources
        ]
        return AblationReport(
            dataset_size=len(cases),
            live_llm=self.live_llm,
            variants=results,
            source_latency_ms={
                source: round(statistics.mean(values), 3) if values else 0.0
                for source, values in source_latencies.items()
                if self.live_llm or source != "llm"
            },
            source_metrics={
                source: {
                    "avg_latency_ms": round(statistics.mean(values), 3) if values else 0.0,
                    "p50_latency_ms": round(self._percentile(values, 50), 3),
                    "p95_latency_ms": round(self._percentile(values, 95), 3),
                    "failure_rate": round(
                        sum(1 for item in source_outputs if item[source].get("failed")) / len(source_outputs), 4
                    ) if source_outputs else 0.0,
                }
                for source, values in source_latencies.items()
                if self.live_llm or source != "llm"
            },
            label_distribution={
                label: sum(case.expected_intent == label for case in cases)
                for label in sorted({case.expected_intent for case in cases})
            },
        )

    def _evaluate_variant(
        self,
        variant: AblationVariant,
        cases: Sequence[AblationCase],
        outputs: Sequence[Dict[str, Dict[str, Any]]],
        latencies: Dict[str, List[float]],
    ) -> VariantResult:
        predictions: List[str] = []
        errors: List[Dict[str, Any]] = []
        for case, case_outputs in zip(cases, outputs):
            if variant.name == "production_fusion":
                predicted, confidence, _ = self.recognizer._vote(
                    case_outputs["llm"], case_outputs["embedding"], case_outputs["pattern"]
                )
            else:
                predicted, confidence = self._fuse(case_outputs, variant)
            predicted_value = predicted.value
            predictions.append(predicted_value)
            if predicted_value != case.expected_intent:
                errors.append({
                    "case_id": case.case_id,
                    "message": case.message,
                    "expected": case.expected_intent,
                    "predicted": predicted_value,
                    "confidence": round(confidence, 4),
                    "sources": {
                        source: {
                            "intent": case_outputs[source].get("intent", IntentCategory.OTHER).value,
                            "confidence": round(float(case_outputs[source].get("confidence", 0.0) or 0.0), 4),
                        }
                        for source in variant.sources
                    },
                })
        expected = [case.expected_intent for case in cases]
        correct = sum(pred == truth for pred, truth in zip(predictions, expected))
        case_latencies = [
            sum(latencies[source][idx] if idx < len(latencies[source]) else 0.0 for source in variant.sources)
            for idx in range(len(cases))
        ]
        accuracy = correct / len(cases) if cases else 0.0
        return VariantResult(
            name=variant.name,
            description=variant.description,
            total=len(cases),
            correct=correct,
            accuracy=round(accuracy, 4),
            accuracy_ci95=[round(value, 4) for value in self._wilson_interval(correct, len(cases))],
            macro_f1=round(self._macro_f1(predictions, expected), 4),
            avg_latency_ms=round(statistics.mean(case_latencies), 3) if case_latencies else 0.0,
            p50_latency_ms=round(self._percentile(case_latencies, 50), 3),
            p95_latency_ms=round(self._percentile(case_latencies, 95), 3),
            confusion_matrix=self._confusion_matrix(predictions, expected),
            errors=errors,
        )

    @staticmethod
    def _fuse(outputs: Dict[str, Dict[str, Any]], variant: AblationVariant) -> Tuple[IntentCategory, float]:
        scores: Dict[IntentCategory, float] = {}
        for source in variant.sources:
            output = outputs[source]
            intent = output.get("intent", IntentCategory.OTHER)
            confidence = float(output.get("confidence", 0.0) or 0.0)
            scores[intent] = scores.get(intent, 0.0) + variant.weights[source] * confidence
        if not scores:
            return IntentCategory.OTHER, 0.0
        intent = max(scores, key=scores.get)
        return intent, scores[intent]

    @staticmethod
    def _macro_f1(predictions: Sequence[str], expected: Sequence[str]) -> float:
        labels = sorted(set(predictions) | set(expected))
        if not labels:
            return 0.0
        values = []
        for label in labels:
            tp = sum(p == label and e == label for p, e in zip(predictions, expected))
            fp = sum(p == label and e != label for p, e in zip(predictions, expected))
            fn = sum(p != label and e == label for p, e in zip(predictions, expected))
            precision = tp / (tp + fp) if tp + fp else 0.0
            recall = tp / (tp + fn) if tp + fn else 0.0
            values.append(2 * precision * recall / (precision + recall) if precision + recall else 0.0)
        return statistics.mean(values)

    @staticmethod
    def _confusion_matrix(predictions: Sequence[str], expected: Sequence[str]) -> Dict[str, Dict[str, int]]:
        labels = sorted(set(predictions) | set(expected))
        return {
            truth: {pred: sum(e == truth and p == pred for p, e in zip(predictions, expected)) for pred in labels}
            for truth in labels
        }

    @staticmethod
    def _wilson_interval(successes: int, total: int, z: float = 1.96) -> Tuple[float, float]:
        if total == 0:
            return 0.0, 0.0
        proportion = successes / total
        denominator = 1 + z * z / total
        centre = (proportion + z * z / (2 * total)) / denominator
        margin = z * math.sqrt(proportion * (1 - proportion) / total + z * z / (4 * total * total)) / denominator
        return max(0.0, centre - margin), min(1.0, centre + margin)

    @staticmethod
    def _percentile(values: Sequence[float], percentile: float) -> float:
        if not values:
            return 0.0
        ordered = sorted(values)
        rank = (len(ordered) - 1) * percentile / 100
        lower = int(rank)
        upper = min(lower + 1, len(ordered) - 1)
        weight = rank - lower
        return ordered[lower] * (1 - weight) + ordered[upper] * weight


def load_cases(path: Path) -> List[AblationCase]:
    cases = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            cases.append(AblationCase(**json.loads(line)))
    ids = [case.case_id for case in cases]
    if len(ids) != len(set(ids)):
        raise ValueError("消融数据集包含重复 case_id")
    valid_intents = {item.value for item in IntentCategory}
    invalid = sorted({case.expected_intent for case in cases} - valid_intents)
    if invalid:
        raise ValueError(f"消融数据集包含未知意图: {', '.join(invalid)}")
    return cases


def write_report(report: AblationReport, json_path: Path, markdown_path: Path) -> None:
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(asdict(report), ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [
        "# CommerceMind 意图识别消融实验",
        "",
        f"- 数据集规模：{report.dataset_size}",
        f"- LLM 实时参与：{'是' if report.live_llm else '否'}",
        "",
        "| 变体 | Accuracy (95% CI) | Macro-F1 | P50/P95 延迟(ms) | 错误数 |",
        "|---|---:|---:|---:|---:|",
    ]
    for item in report.variants:
        lines.append(
            f"| {item.name} | {item.accuracy:.4f} [{item.accuracy_ci95[0]:.4f}, {item.accuracy_ci95[1]:.4f}] | "
            f"{item.macro_f1:.4f} | {item.p50_latency_ms:.3f}/{item.p95_latency_ms:.3f} | {len(item.errors)} |"
        )
    lines.extend(["", "## 单路运行指标", ""])
    for source, metrics in report.source_metrics.items():
        lines.append(
            f"- {source}: avg={metrics['avg_latency_ms']:.3f} ms, "
            f"P50={metrics['p50_latency_ms']:.3f} ms, P95={metrics['p95_latency_ms']:.3f} ms, "
            f"failure_rate={metrics['failure_rate']:.2%}"
        )
    lines.extend(["", "## 标签分布", ""])
    lines.append(", ".join(f"{label}={count}" for label, count in report.label_distribution.items()))
    lines.extend(["", "## 错误样本", ""])
    for item in report.variants:
        lines.append(f"### {item.name}")
        if not item.errors:
            lines.append("无。")
        for error in item.errors:
            lines.append(
                f"- `{error['case_id']}` 期望 `{error['expected']}`，预测 "
                f"`{error['predicted']}`：{error['message']}"
            )
        lines.append("")
    markdown_path.write_text("\n".join(lines), encoding="utf-8")
