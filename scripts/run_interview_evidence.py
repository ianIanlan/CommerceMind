#!/usr/bin/env python3
"""Run interview scenarios against a live CommerceMind API and write evidence."""
from __future__ import annotations

import argparse
import json
import pathlib
import time
from dataclasses import asdict, dataclass
from typing import Any, Dict, List

import httpx


@dataclass
class EvidenceResult:
    scenario: str
    passed: bool
    latency_ms: float
    evidence: Dict[str, Any]
    failures: List[str]


class EvidenceRunner:
    def __init__(self, base_url: str, timeout: float = 90.0):
        self.base_url = base_url.rstrip("/")
        self.client = httpx.Client(timeout=timeout)

    def _chat(self, scenario: str, message: str, checks) -> EvidenceResult:
        started = time.monotonic()
        response = self.client.post(
            f"{self.base_url}/chat",
            json={"message": message, "user_id": "demo-user", "conv_id": f"evidence-{scenario}"},
        )
        response.raise_for_status()
        payload = response.json()
        failures = checks(payload)
        evidence = {
            "request_id": payload.get("request_id"),
            "intent": payload.get("intent"),
            "primary_agent": payload.get("primary_agent"),
            "supporting_agents": payload.get("supporting_agents", []),
            "tools_used": payload.get("tools_used", []),
            "escalated": payload.get("escalated"),
            "pending_action_count": len(payload.get("pending_actions", [])),
            "safety_violations": payload.get("safety_violations", []),
        }
        return EvidenceResult(
            scenario, not failures, round((time.monotonic() - started) * 1000, 1), evidence, failures
        )

    def run(self) -> List[EvidenceResult]:
        results = [
            self._chat("logistics", "查询订单 ORD-10002 的物流进度", lambda p: self._require(
                p, intents={"logistics"}, primary="order", any_tools={"get_order", "get_logistics"}
            )),
            self._chat("duplicate_payment", "订单 ORD-10005 为什么扣了两次款", lambda p: self._require(
                p, intents={"duplicate_payment", "payment_issue"}, primary="billing",
                any_tools={"get_payment_records"},
            )),
            self._chat("multi_domain", "登录页面报401，而且订单 ORD-10005 被扣了两次款", lambda p: self._require(
                p, intents={"technical_login", "duplicate_payment"}, required_agents={"technical", "billing"}
            )),
            self._chat("human_handoff", "这个问题一直没人解决，请马上转人工客服", lambda p: self._require(
                p, intents={"human_handoff", "escalation"}, primary="escalation", escalated=True
            )),
            self._ownership_isolation(),
            self._refund_state_machine(),
        ]
        return results

    @staticmethod
    def _require(
        payload: Dict[str, Any], *, intents=None, primary=None, required_agents=None,
        any_tools=None, escalated=None,
    ) -> List[str]:
        failures = []
        if intents and payload.get("intent") not in intents:
            failures.append(f"intent={payload.get('intent')} not in {sorted(intents)}")
        if primary and payload.get("primary_agent") != primary:
            failures.append(f"primary_agent={payload.get('primary_agent')} expected={primary}")
        agents = {payload.get("primary_agent"), *payload.get("supporting_agents", [])}
        if required_agents and not set(required_agents).issubset(agents):
            failures.append(f"agents={sorted(item for item in agents if item)} missing={sorted(set(required_agents)-agents)}")
        if any_tools and not (set(payload.get("tools_used", [])) & set(any_tools)):
            failures.append(f"tools_used={payload.get('tools_used', [])} expected one of {sorted(any_tools)}")
        if escalated is not None and bool(payload.get("escalated")) is not escalated:
            failures.append(f"escalated={payload.get('escalated')} expected={escalated}")
        return failures

    def _ownership_isolation(self) -> EvidenceResult:
        started = time.monotonic()
        response = self.client.get(
            f"{self.base_url}/commerce/orders/ORD-OTHER", params={"user_id": "demo-user"}
        )
        passed = response.status_code == 404
        return EvidenceResult(
            "ownership_isolation", passed, round((time.monotonic() - started) * 1000, 1),
            {"http_status": response.status_code, "cross_user_order_returned": response.status_code == 200},
            [] if passed else [f"expected HTTP 404, got {response.status_code}"],
        )

    def _refund_state_machine(self) -> EvidenceResult:
        started = time.monotonic()
        prepared = self.client.post(
            f"{self.base_url}/commerce/refunds/prepare",
            json={
                "user_id": "demo-user", "order_id": "ORD-10001",
                "reason": "interview evidence verification", "idempotency_key": "interview-evidence-refund-v1",
            },
        )
        prepared.raise_for_status()
        first = prepared.json()
        failures = []
        if first.get("status") not in {"awaiting_confirmation", "succeeded"}:
            failures.append(f"unexpected prepare status={first.get('status')}")
        action_id = first.get("action_id")
        if not action_id:
            failures.append("prepare did not return action_id")
            return EvidenceResult("refund_state_machine", False, 0.0, {}, failures)
        confirmed = self.client.post(
            f"{self.base_url}/actions/{action_id}/confirm", json={"user_id": "demo-user"}
        )
        repeated = self.client.post(
            f"{self.base_url}/actions/{action_id}/confirm", json={"user_id": "demo-user"}
        )
        confirmed.raise_for_status()
        repeated.raise_for_status()
        confirmed_data, repeated_data = confirmed.json(), repeated.json()
        if confirmed_data.get("status") != "succeeded":
            failures.append(f"confirm status={confirmed_data.get('status')}")
        if confirmed_data.get("resource_id") != repeated_data.get("resource_id"):
            failures.append("repeated confirmation returned a different resource_id")
        return EvidenceResult(
            "refund_state_machine", not failures, round((time.monotonic() - started) * 1000, 1),
            {
                "action_id": action_id,
                "prepare_status": first.get("status"),
                "confirm_status": confirmed_data.get("status"),
                "resource_id": confirmed_data.get("resource_id"),
                "idempotent_repeat": confirmed_data.get("resource_id") == repeated_data.get("resource_id"),
            },
            failures,
        )


def write_report(results: List[EvidenceResult], output_dir: pathlib.Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    payload = [asdict(item) for item in results]
    (output_dir / "interview_evidence.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    passed = sum(item.passed for item in results)
    lines = [
        "# CommerceMind 面试场景证据", "", f"通过：{passed}/{len(results)}", "",
        "| 场景 | 结果 | 延迟(ms) | 结构化证据 |", "|---|---:|---:|---|",
    ]
    for item in results:
        evidence = json.dumps(item.evidence, ensure_ascii=False, separators=(",", ":"))
        lines.append(f"| {item.scenario} | {'PASS' if item.passed else 'FAIL'} | {item.latency_ms:.1f} | `{evidence}` |")
        for failure in item.failures:
            lines.append(f"\n- `{item.scenario}`: {failure}")
    (output_dir / "interview_evidence.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--output-dir", type=pathlib.Path, default=pathlib.Path("outputs"))
    args = parser.parse_args()
    results = EvidenceRunner(args.base_url).run()
    write_report(results, args.output_dir)
    print(args.output_dir / "interview_evidence.md")
    return 0 if all(item.passed for item in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
