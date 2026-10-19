#!/usr/bin/env python3
"""Calibrate a selective semantic-embedding fast path and validate it once.

The calibration split chooses thresholds. The validation split is never used by
the search, which prevents choosing a threshold from validation errors.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.intent_recognizer import IntentRecognizer
from evaluation.ablation import load_cases


@dataclass(frozen=True)
class RouteMetrics:
    total: int
    routed: int
    correct: int
    coverage: float
    precision: float
    estimated_llm_call_reduction: float


async def embed_cases(recognizer: IntentRecognizer, dataset: Path) -> list[dict[str, Any]]:
    rows = []
    for case in load_cases(dataset):
        result = await recognizer._embedding_recognize(case.message)
        rows.append({
            "case_id": case.case_id,
            "expected": case.expected_intent,
            "predicted": result["intent"].value,
            "confidence": round(float(result.get("confidence", 0.0)), 6),
            "margin": round(float(result.get("margin", 0.0)), 6),
            "backend": result.get("backend"),
        })
    return rows


def score(rows: list[dict[str, Any]], threshold: float, margin: float) -> RouteMetrics:
    routed = [r for r in rows if r["confidence"] >= threshold and r["margin"] >= margin]
    correct = sum(r["predicted"] == r["expected"] for r in routed)
    coverage = len(routed) / len(rows) if rows else 0.0
    precision = correct / len(routed) if routed else 0.0
    return RouteMetrics(len(rows), len(routed), correct, coverage, precision, coverage)


def calibrate(rows: list[dict[str, Any]], minimum_precision: float) -> tuple[float, float, RouteMetrics]:
    candidates: list[tuple[float, float, RouteMetrics]] = []
    for threshold_i in range(60, 96, 2):
        for margin_i in range(2, 31, 2):
            threshold, margin = threshold_i / 100, margin_i / 100
            metrics = score(rows, threshold, margin)
            if metrics.routed >= 3 and metrics.precision >= minimum_precision:
                candidates.append((threshold, margin, metrics))
    if not candidates:
        return 1.0, 1.0, score(rows, 1.0, 1.0)
    return max(candidates, key=lambda item: (item[2].coverage, item[2].precision, item[0], item[1]))


def render(report: dict[str, Any]) -> str:
    cal, val = report["calibration_metrics"], report["validation_metrics"]
    return "\n".join([
        "# Semantic Embedding selective-router experiment",
        "",
        f"- Model: `{report['model']}`",
        f"- Calibrated threshold/margin: `{report['threshold']:.2f}` / `{report['margin']:.2f}`",
        f"- Required calibration precision: `{report['minimum_precision']:.2%}`",
        "",
        "| Split | Cases | Routed | Coverage / estimated LLM reduction | Routed precision |",
        "|---|---:|---:|---:|---:|",
        f"| calibration | {cal['total']} | {cal['routed']} | {cal['coverage']:.2%} | {cal['precision']:.2%} |",
        f"| validation | {val['total']} | {val['routed']} | {val['coverage']:.2%} | {val['precision']:.2%} |",
        "",
        "Thresholds were selected only on calibration. Validation errors were not used for tuning.",
        "A routed request skips the LLM; all other requests retain the normal LLM path.",
        "",
    ])


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--calibration", type=Path, required=True)
    parser.add_argument("--validation", type=Path, required=True)
    parser.add_argument("--model", default="sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2")
    parser.add_argument("--minimum-precision", type=float, default=0.95)
    parser.add_argument("--output", type=Path, default=Path("outputs/embedding_cost_router.json"))
    args = parser.parse_args()

    os.environ["EMBEDDING_BACKEND"] = "fastembed"
    os.environ["EMBEDDING_MODEL"] = args.model
    os.environ["INTENT_EMBEDDING_ENABLED"] = "true"
    recognizer = IntentRecognizer(api_key="offline-experiment")
    calibration_rows = await embed_cases(recognizer, args.calibration)
    threshold, margin, calibration_metrics = calibrate(calibration_rows, args.minimum_precision)
    validation_rows = await embed_cases(recognizer, args.validation)
    validation_metrics = score(validation_rows, threshold, margin)
    report = {
        "model": args.model,
        "calibration_dataset": str(args.calibration),
        "validation_dataset": str(args.validation),
        "minimum_precision": args.minimum_precision,
        "threshold": threshold,
        "margin": margin,
        "calibration_metrics": asdict(calibration_metrics),
        "validation_metrics": asdict(validation_metrics),
        "validation_routed_errors": [
            row for row in validation_rows
            if row["confidence"] >= threshold and row["margin"] >= margin
            and row["predicted"] != row["expected"]
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args.output.with_suffix(".md").write_text(render(report), encoding="utf-8")
    print(args.output.with_suffix(".md"))


if __name__ == "__main__":
    asyncio.run(main())
