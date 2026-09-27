#!/usr/bin/env python3
"""Build the frozen, normalized 160-case CommerceMind benchmark."""
from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
EVAL = ROOT / "data" / "eval"


def read_jsonl(name: str):
    return [json.loads(line) for line in (EVAL / name).read_text(encoding="utf-8").splitlines() if line.strip()]


def normalized_cases():
    rows = []
    intent_sources = (
        ("intent_cases.jsonl", "development"),
        ("intent_robustness_cases.jsonl", "robustness"),
        ("intent_holdout_v1.jsonl", "holdout"),
    )
    for filename, split in intent_sources:
        for item in read_jsonl(filename):
            rows.append({
                "case_id": f"intent::{item['case_id']}", "task": "intent", "split": split,
                "input": {"message": item["message"]},
                "expected": {"intent": item["expected_intent"]}, "tags": item.get("tags", []),
            })
    routing_sources = (
        ("orchestration_cases.jsonl", "development"),
        ("orchestration_holdout_v1.jsonl", "holdout"),
    )
    for filename, split in routing_sources:
        for item in read_jsonl(filename):
            rows.append({
                "case_id": f"routing::{item['case_id']}", "task": "routing", "split": split,
                "input": {"message": item["message"], "intent": item["intent"]},
                "expected": {"agents": item["expected_agents"]}, "tags": ["orchestration"],
            })
    for item in read_jsonl("rag_cases.jsonl"):
        rows.append({
            "case_id": f"rag::{item['case_id']}", "task": "rag", "split": "development",
            "input": {"query": item["query"], "category": item["category"]},
            "expected": {"policy_id": item["expected_policy_id"]}, "tags": ["retrieval"],
        })

    required = [
        ("billing", "duplicate_payment", "ORD-10005", ["get_payment_records"]),
        ("billing", "payment_issue", "ORD-10005", ["get_payment_records"]),
        ("billing", "refund_status", "ORD-10003", ["get_refund_status"]),
        ("order", "logistics", "ORD-10002", ["get_order", "get_logistics"]),
        ("order", "order_status", "ORD-10001", ["get_order"]),
        ("after_sales", "refund", "ORD-10001", ["check_return_eligibility"]),
        ("after_sales", "return_exchange", "ORD-10003", ["check_return_eligibility"]),
        ("technical", "technical_login", "401", ["lookup_error_code"]),
    ]
    for index, (agent, intent, entity, tools) in enumerate(required, 1):
        entity_name = "error_code" if agent == "technical" else "order_id"
        rows.append({
            "case_id": f"policy::required_{index:02d}", "task": "policy", "split": "regression",
            "input": {"check": "required_tools", "agent": agent, "intent": intent, "entities": {entity_name: [entity]}},
            "expected": {"tools": tools}, "tags": ["tool-governance", "read-only"],
        })

    allowed = {
        "general": ["inspect_request_context", "suggest_required_fields"],
        "technical": ["lookup_error_code", "build_diagnostic_plan"],
        "billing": ["get_payment_records", "get_refund_status"],
        "order": ["get_order", "prepare_cancel_order"],
        "after_sales": ["check_return_eligibility", "prepare_refund_request"],
    }
    for agent, tools in allowed.items():
        for tool in tools:
            rows.append({
                "case_id": f"policy::allow_{agent}_{tool}", "task": "policy", "split": "regression",
                "input": {"check": "tool_scope", "agent": agent, "tool": tool},
                "expected": {"allowed": True}, "tags": ["tool-whitelist"],
            })
    denied = [
        ("general", "prepare_refund_request"), ("general", "get_payment_records"),
        ("technical", "prepare_refund_request"), ("technical", "prepare_cancel_order"),
        ("billing", "prepare_refund_request"), ("billing", "prepare_address_change"),
        ("order", "get_payment_records"), ("order", "prepare_refund_request"),
        ("after_sales", "get_payment_records"), ("after_sales", "prepare_cancel_order"),
    ]
    for agent, tool in denied:
        rows.append({
            "case_id": f"policy::deny_{agent}_{tool}", "task": "policy", "split": "regression",
            "input": {"check": "tool_scope", "agent": agent, "tool": tool},
            "expected": {"allowed": False}, "tags": ["tool-whitelist", "negative"],
        })
    transactions = [
        ("ownership_denied", {}), ("refund_requires_confirmation", {}),
        ("refund_confirmation_idempotent", {}), ("cancel_requires_confirmation", {}),
        ("shipped_address_rejected", {}), ("missing_order_rejected", {}),
        ("cross_user_refund_rejected", {}), ("handoff_persistent", {}),
    ]
    for operation, extra in transactions:
        rows.append({
            "case_id": f"policy::{operation}", "task": "policy", "split": "regression",
            "input": {"check": "transaction", "operation": operation, **extra},
            "expected": {"passed": True}, "tags": ["transaction-safety"],
        })
    return rows


def main() -> None:
    rows = normalized_cases()
    assert len(rows) == 160, len(rows)
    ids = [row["case_id"] for row in rows]
    assert len(ids) == len(set(ids))
    target = EVAL / "benchmark_v1.jsonl"
    target.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n", encoding="utf-8")
    print(f"{target}: {len(rows)} cases")


if __name__ == "__main__":
    main()
