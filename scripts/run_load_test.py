#!/usr/bin/env python3
"""Async HTTP load test with latency, throughput, errors, degradation, and LLM usage."""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import statistics
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Optional

import httpx


MODEL_MESSAGES = (
    "订单 ORD-10005 为什么扣了两次款",
    "查询订单 ORD-10002 的物流进度",
    "登录页面报 401，应该怎么排查",
    "订单 ORD-10001 现在是什么状态",
)


@dataclass
class LoadResult:
    concurrency: int
    requests: int
    successes: int
    errors: int
    error_rate: float
    duration_s: float
    throughput_rps: float
    p50_ms: float
    p95_ms: float
    max_ms: float
    degraded_rate: float
    model_calls: int
    input_tokens: Optional[int]
    output_tokens: Optional[int]
    cost_usd: Optional[float]
    usage_coverage: float


def percentile(values: list[float], value: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = (len(ordered) - 1) * value / 100
    lower, upper = math.floor(rank), math.ceil(rank)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] * (upper - rank) + ordered[upper] * (rank - lower)


async def run_level(base_url: str, profile: str, concurrency: int, request_count: int, timeout: float) -> LoadResult:
    semaphore = asyncio.Semaphore(concurrency)
    latencies: list[float] = []
    payloads: list[dict[str, Any]] = []
    errors = 0

    async with httpx.AsyncClient(timeout=timeout, limits=httpx.Limits(max_connections=concurrency + 5)) as client:
        async def one(index: int) -> None:
            nonlocal errors
            message = "请马上转人工客服" if profile == "deterministic" else MODEL_MESSAGES[index % len(MODEL_MESSAGES)]
            body = {"message": message, "user_id": "demo-user", "conv_id": f"load-{profile}-{concurrency}-{uuid.uuid4().hex}"}
            async with semaphore:
                started = time.perf_counter()
                try:
                    response = await client.post(f"{base_url.rstrip('/')}/chat", json=body)
                    elapsed = (time.perf_counter() - started) * 1000
                    response.raise_for_status()
                    latencies.append(elapsed)
                    payloads.append(response.json())
                except Exception:
                    errors += 1

        wall_started = time.perf_counter()
        await asyncio.gather(*(one(index) for index in range(request_count)))
        duration = time.perf_counter() - wall_started

    usages = [item.get("model_usage") or {} for item in payloads]
    complete = [usage for usage in usages if usage.get("usage_complete")]
    costs = [usage.get("cost_usd") for usage in complete]
    cost_available = len(costs) == len(payloads) and all(cost is not None for cost in costs)
    successes = len(payloads)
    return LoadResult(
        concurrency=concurrency, requests=request_count, successes=successes, errors=errors,
        error_rate=round(errors / request_count, 4), duration_s=round(duration, 3),
        throughput_rps=round(successes / duration, 3) if duration else 0.0,
        p50_ms=round(percentile(latencies, 50), 1), p95_ms=round(percentile(latencies, 95), 1),
        max_ms=round(max(latencies), 1) if latencies else 0.0,
        degraded_rate=round(sum(bool(item.get("degraded_agents")) for item in payloads) / max(successes, 1), 4),
        model_calls=sum(int(usage.get("calls") or 0) for usage in usages),
        input_tokens=sum(int(usage.get("input_tokens") or 0) for usage in complete) if complete else None,
        output_tokens=sum(int(usage.get("output_tokens") or 0) for usage in complete) if complete else None,
        cost_usd=round(sum(float(cost) for cost in costs), 8) if cost_available else None,
        usage_coverage=round(len(complete) / max(successes, 1), 4),
    )


def write_report(profile: str, results: list[LoadResult], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    data = {"profile": profile, "results": [asdict(item) for item in results]}
    (output_dir / f"load_test_{profile}.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [
        f"# CommerceMind 并发压测：{profile}", "",
        "| 并发 | 请求 | P50 | P95 | 吞吐(req/s) | 错误率 | 降级率 | 模型调用 | Token覆盖 | 成本(USD) |",
        "|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for item in results:
        cost = f"{item.cost_usd:.6f}" if item.cost_usd is not None else "N/A"
        lines.append(
            f"| {item.concurrency} | {item.requests} | {item.p50_ms:.1f} ms | {item.p95_ms:.1f} ms | "
            f"{item.throughput_rps:.3f} | {item.error_rate:.2%} | {item.degraded_rate:.2%} | "
            f"{item.model_calls} | {item.usage_coverage:.2%} | {cost} |"
        )
    lines += [
        "", "## 口径", "",
        "- 延迟为客户端观测到的完整 HTTP 往返时间；吞吐为成功请求数除以该档总墙钟时间。",
        "- deterministic 使用明确转人工快速路径，用于测量应用、Redis、PostgreSQL 和 HTTP 基线，不调用生成模型。" if profile == "deterministic" else "- model 轮换支付、物流、技术和订单请求，包含真实意图识别、Agent 与工具链。",
        "- 成本只在网关为每次调用返回 token usage 且配置真实单价时计算；N/A 表示证据不足，不代表成本为零。",
        "- 这是单机 Docker 开发环境的短时阶梯压测，不外推为生产容量。",
    ]
    (output_dir / f"load_test_{profile}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--profile", choices=["deterministic", "model"], default="deterministic")
    parser.add_argument("--concurrency", default="1,5,10,20")
    parser.add_argument("--requests", type=int, default=20, help="requests per concurrency level")
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--output-dir", type=Path, default=Path("outputs"))
    args = parser.parse_args()
    levels = [int(value) for value in args.concurrency.split(",")]
    async with httpx.AsyncClient(timeout=5) as client:
        health = await client.get(f"{args.base_url.rstrip('/')}/health")
        health.raise_for_status()
    results = []
    for level in levels:
        result = await run_level(args.base_url, args.profile, level, args.requests, args.timeout)
        results.append(result)
        print(asdict(result), flush=True)
    write_report(args.profile, results, args.output_dir)


if __name__ == "__main__":
    asyncio.run(main())
