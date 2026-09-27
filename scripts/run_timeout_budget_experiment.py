#!/usr/bin/env python3
"""Force a short Agent budget and verify deterministic fact-based degradation."""
from __future__ import annotations

import argparse
import json
import pathlib
import uuid

import httpx


MESSAGE = "登录页面报401，而且订单 ORD-10005 被扣了两次款"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--output", type=pathlib.Path, default=pathlib.Path("outputs/timeout_budget_experiment.json"))
    args = parser.parse_args()
    samples = []
    with httpx.Client(timeout=120) as client:
        for phase in ("warmup", "measured"):
            response = client.post(
                f"{args.base_url.rstrip('/')}/chat",
                json={"message": MESSAGE, "user_id": "demo-user", "conv_id": f"timeout-{uuid.uuid4().hex[:8]}"},
            )
            response.raise_for_status()
            payload = response.json()
            samples.append({
                "phase": phase,
                "request_id": payload.get("request_id"),
                "degraded_agents": payload.get("degraded_agents", []),
                "tools_used": payload.get("tools_used", []),
                "stage_timings_ms": payload.get("stage_timings_ms", {}),
                "response": payload.get("response", ""),
            })
    measured = samples[-1]
    required_agents = {"billing", "technical"}
    required_tools = {"get_payment_records", "lookup_error_code"}
    checks = {
        "both_agents_degraded": required_agents.issubset(set(measured["degraded_agents"])),
        "required_facts_preserved": required_tools.issubset(set(measured["tools_used"])),
        "agent_wall_under_2s": float(measured["stage_timings_ms"].get("agents_parallel_wall", 999999)) < 2000,
        "response_nonempty": len(str(measured["response"])) >= 40,
    }
    result = {"checks": checks, "samples": samples}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"checks": checks, "measured_timings": measured["stage_timings_ms"]}, ensure_ascii=False))
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
