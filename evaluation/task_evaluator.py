"""Task-level deterministic evaluation for business state transitions."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, List

from commerce.models import ActionStatus
from commerce.payment_gateway import GatewayRefund
from commerce.service import CommerceService
from commerce.store import CommerceStore


@dataclass
class TaskResult:
    name: str
    passed: bool
    detail: str


class CommerceTaskEvaluator:
    def run(self) -> List[TaskResult]:
        checks: List[tuple[str, Callable[[CommerceService, CommerceStore], None]]] = [
            ("order_ownership_isolation", self._ownership),
            ("refund_requires_confirmation", self._refund_confirmation),
            ("cancel_requires_confirmation", self._cancel_confirmation),
            ("shipped_address_change_rejected", self._address_boundary),
            ("handoff_is_persistent", self._handoff),
            ("provider_failure_never_reports_success", self._provider_failure),
        ]
        results = []
        for name, check in checks:
            store = CommerceStore(":memory:")
            store.seed_demo_data()
            service = CommerceService(store)
            try:
                check(service, store)
                results.append(TaskResult(name, True, "all process assertions passed"))
            except AssertionError as ex:
                results.append(TaskResult(name, False, str(ex) or "assertion failed"))
        return results

    @staticmethod
    def _ownership(service: CommerceService, _store: CommerceStore) -> None:
        assert not service.get_order("demo-user", "ORD-OTHER").success, "cross-user order read was allowed"

    @staticmethod
    def _refund_confirmation(service: CommerceService, store: CommerceStore) -> None:
        action = service.prepare_refund("demo-user", "ORD-10001", "评测", "eval-refund")
        assert action.status is ActionStatus.AWAITING_CONFIRMATION, "refund skipped confirmation"
        assert not store.fetch_all("SELECT * FROM refund_requests"), "refund executed during prepare"
        assert service.confirm_action("demo-user", action.action_id).status is ActionStatus.SUCCEEDED
        assert len(store.fetch_all("SELECT * FROM refund_requests")) == 1

    @staticmethod
    def _cancel_confirmation(service: CommerceService, _store: CommerceStore) -> None:
        action = service.prepare_cancel_order("demo-user", "ORD-10005", "评测", "eval-cancel")
        assert service.get_order("demo-user", "ORD-10005").data["status"] == "paid"
        service.confirm_action("demo-user", action.action_id)
        assert service.get_order("demo-user", "ORD-10005").data["status"] == "cancelled"

    @staticmethod
    def _address_boundary(service: CommerceService, _store: CommerceStore) -> None:
        action = service.prepare_address_change("demo-user", "ORD-10002", "上海市测试区测试路 1 号")
        assert action.status is ActionStatus.FAILED, "shipped order address mutation was allowed"

    @staticmethod
    def _handoff(service: CommerceService, _store: CommerceStore) -> None:
        ticket = service.create_handoff_ticket("demo-user", "c1", "r1", "人工", "human_handoff", "HIGH", {})
        assert service.get_handoff_ticket("demo-user", ticket["ticket_id"])["status"] == "open"

    @staticmethod
    def _provider_failure(_service: CommerceService, _store: CommerceStore) -> None:
        class FailingGateway:
            def refund(self, payment_reference, amount, idempotency_key, metadata=None):
                return GatewayRefund(False, None, "failed", "provider_down", "simulated outage")

        store = CommerceStore(":memory:")
        store.seed_demo_data()
        store.execute(
            "UPDATE payments SET payment_id=?, channel='stripe' WHERE order_id=?",
            ("pi_simulated", "ORD-10001"),
        )
        service = CommerceService(store, payment_gateway=FailingGateway())
        action = service.prepare_refund("demo-user", "ORD-10001", "provider failure test", "provider-failure")
        result = service.confirm_action("demo-user", action.action_id)
        assert result.status is ActionStatus.FAILED, "provider failure was reported as success"
        assert not store.fetch_all("SELECT * FROM refund_requests"), "failed provider call created a local refund"


def write_task_report(results: List[TaskResult], json_path: Path, markdown_path: Path) -> None:
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps([asdict(item) for item in results], ensure_ascii=False, indent=2), encoding="utf-8")
    passed = sum(item.passed for item in results)
    lines = ["# 电商任务级评测", "", f"通过：{passed}/{len(results)}", "", "| 任务 | 结果 | 过程断言 |", "|---|---|---|"]
    lines.extend(f"| {item.name} | {'PASS' if item.passed else 'FAIL'} | {item.detail} |" for item in results)
    markdown_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
