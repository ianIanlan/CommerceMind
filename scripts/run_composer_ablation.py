#!/usr/bin/env python3
"""Benchmark one Composer mode against a live CommerceMind deployment."""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import statistics
import time
import uuid
from typing import Any, Dict, Iterable, List

import httpx


MESSAGE = "登录页面报401，而且订单 ORD-10005 被扣了两次款"
REQUIRED_AGENTS = {"billing", "technical"}
BILLING_TOOLS = {"get_payment_records"}
TECHNICAL_TOOLS = {"lookup_error_code", "build_diagnostic_plan"}


def percentile(values: Iterable[float], percentile_value: float) -> float:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return 0.0
    rank = (len(ordered) - 1) * percentile_value
    lower = int(rank)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = rank - lower
    return ordered[lower] * (1 - fraction) + ordered[upper] * fraction


def repetition_ratio(text: str) -> float:
    """Return the fraction of repeated non-empty normalized lines."""
    lines = [re.sub(r"\s+", "", line) for line in text.splitlines() if line.strip()]
    if not lines:
        return 0.0
    return round(1.0 - len(set(lines)) / len(lines), 4)


def process_assertions(payload: Dict[str, Any]) -> Dict[str, bool]:
    agents = {payload.get("primary_agent"), *payload.get("supporting_agents", [])}
    tools = set(payload.get("tools_used", []))
    response = str(payload.get("response", "")).strip()
    return {
        "both_domains": REQUIRED_AGENTS.issubset(agents),
        "billing_evidence": bool(tools & BILLING_TOOLS),
        "technical_evidence": bool(tools & TECHNICAL_TOOLS),
        "nonempty_response": len(response) >= 40,
        "no_internal_leak": not any(marker in response for marker in (
            "候选结果", "主 Agent", "不要提及 Agent", "我们需要回答用户",
        )),
        "no_safety_violation": not payload.get("safety_violations"),
    }


def summarize(mode: str, samples: List[Dict[str, Any]]) -> Dict[str, Any]:
    totals = [sample["server_total_ms"] for sample in samples]
    composers = [sample["composer_ms"] for sample in samples]
    agent_walls = [sample["agents_parallel_wall_ms"] for sample in samples]
    repetitions = [sample["repetition_ratio"] for sample in samples]
    assertion_names = list(samples[0]["assertions"]) if samples else []
    return {
        "mode": mode,
        "runs": len(samples),
        "process_success_rate": round(sum(all(s["assertions"].values()) for s in samples) / len(samples), 4),
        "assertion_rates": {
            name: round(sum(s["assertions"][name] for s in samples) / len(samples), 4)
            for name in assertion_names
        },
        "latency_ms": {
            "total_p50": round(percentile(totals, 0.50), 1),
            "total_p95": round(percentile(totals, 0.95), 1),
            "total_mean": round(statistics.fmean(totals), 1),
            "composer_p50": round(percentile(composers, 0.50), 1),
            "composer_p95": round(percentile(composers, 0.95), 1),
            "agent_wall_p50": round(percentile(agent_walls, 0.50), 1),
        },
        "response": {
            "mean_chars": round(statistics.fmean(s["response_chars"] for s in samples), 1),
            "mean_repetition_ratio": round(statistics.fmean(repetitions), 4),
            "max_repetition_ratio": round(max(repetitions), 4),
        },
        "samples": samples,
    }


def run(base_url: str, mode: str, repeats: int, timeout: float) -> Dict[str, Any]:
    samples = []
    with httpx.Client(timeout=timeout) as client:
        for index in range(repeats):
            started = time.monotonic()
            response = client.post(
                f"{base_url.rstrip('/')}/chat",
                json={
                    "message": MESSAGE,
                    "user_id": "demo-user",
                    "conv_id": f"composer-{mode}-{uuid.uuid4().hex[:10]}",
                },
            )
            client_wall_ms = (time.monotonic() - started) * 1000
            response.raise_for_status()
            payload = response.json()
            timings = payload.get("stage_timings_ms", {})
            text = str(payload.get("response", ""))
            samples.append({
                "run": index + 1,
                "request_id": payload.get("request_id"),
                "server_total_ms": float(timings.get("total", payload.get("latency_ms", 0.0))),
                "client_wall_ms": round(client_wall_ms, 1),
                "intent_ms": float(timings.get("intent_recognition", 0.0)),
                "agents_parallel_wall_ms": float(timings.get("agents_parallel_wall", 0.0)),
                "composer_ms": float(timings.get("composer", 0.0)),
                "response_chars": len(text),
                "repetition_ratio": repetition_ratio(text),
                "assertions": process_assertions(payload),
                "primary_agent": payload.get("primary_agent"),
                "supporting_agents": payload.get("supporting_agents", []),
                "tools_used": payload.get("tools_used", []),
                "safety_violations": payload.get("safety_violations", []),
                "response": text,
            })
            print(f"{mode} {index + 1}/{repeats}: {samples[-1]['server_total_ms']:.1f} ms", flush=True)
    return summarize(mode, samples)


def write_report(result: Dict[str, Any], output_dir: pathlib.Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    mode = result["mode"]
    (output_dir / f"composer_ablation_{mode}.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    latency = result["latency_ms"]
    response = result["response"]
    lines = [
        f"# Composer ablation: {mode}", "",
        f"- Runs: {result['runs']}",
        f"- Process success: {result['process_success_rate']:.1%}",
        f"- Total P50/P95: {latency['total_p50']:.1f}/{latency['total_p95']:.1f} ms",
        f"- Composer P50/P95: {latency['composer_p50']:.1f}/{latency['composer_p95']:.1f} ms",
        f"- Mean response chars: {response['mean_chars']:.1f}",
        f"- Mean repetition ratio: {response['mean_repetition_ratio']:.1%}", "",
        "Raw responses are stored only in the ignored outputs directory.",
    ]
    (output_dir / f"composer_ablation_{mode}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--mode", choices=("deterministic", "llm"), required=True)
    parser.add_argument("--repeats", type=int, default=10)
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--output-dir", type=pathlib.Path, default=pathlib.Path("outputs"))
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error("--repeats must be positive")
    result = run(args.base_url, args.mode, args.repeats, args.timeout)
    write_report(result, args.output_dir)
    return 0 if result["process_success_rate"] == 1.0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
