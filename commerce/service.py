"""电商领域规则、查询与高风险动作状态机。"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from commerce.models import ActionResult, ActionStatus, EligibilityResult, ToolOutcome
from commerce.payment_gateway import PaymentGateway
from commerce.store import CommerceStore, utc_now


class CommerceService:
    def __init__(self, store: CommerceStore, payment_gateway: Optional[PaymentGateway] = None):
        self.store = store
        self.payment_gateway = payment_gateway

    def _owned_order(self, user_id: str, order_id: str) -> Optional[Dict[str, Any]]:
        return self.store.fetch_one(
            """SELECT o.*, p.name AS product_name, p.category, p.is_virtual
               FROM orders o JOIN products p ON p.product_id=o.product_id
               WHERE o.order_id=? AND o.user_id=?""",
            (order_id, user_id),
        )

    def get_order(self, user_id: str, order_id: str, request_id: str = "") -> ToolOutcome:
        order = self._owned_order(user_id, order_id)
        if not order:
            return ToolOutcome(False, None, "ORDER_NOT_FOUND", "订单不存在或不属于当前用户")
        self.store.audit(user_id, "ORDER_READ", order_id, {"status": order["status"]}, request_id)
        return ToolOutcome(True, order, evidence=[{"type": "order", "id": order_id}])

    def get_logistics(self, user_id: str, order_id: str, request_id: str = "") -> ToolOutcome:
        if not self._owned_order(user_id, order_id):
            return ToolOutcome(False, None, "ORDER_NOT_FOUND", "订单不存在或不属于当前用户")
        shipment = self.store.fetch_one("SELECT * FROM shipments WHERE order_id=?", (order_id,))
        if not shipment:
            return ToolOutcome(False, None, "SHIPMENT_NOT_FOUND", "订单暂无物流信息")
        shipment["events"] = json.loads(shipment.pop("events_json"))
        self.store.audit(user_id, "SHIPMENT_READ", order_id, {"shipment_id": shipment["shipment_id"]}, request_id)
        return ToolOutcome(True, shipment, evidence=[{"type": "shipment", "id": shipment["shipment_id"]}])

    def get_payment_records(self, user_id: str, order_id: str, request_id: str = "") -> ToolOutcome:
        if not self._owned_order(user_id, order_id):
            return ToolOutcome(False, None, "ORDER_NOT_FOUND", "订单不存在或不属于当前用户")
        payments = self.store.fetch_all("SELECT * FROM payments WHERE order_id=? ORDER BY paid_at", (order_id,))
        duplicate_candidate = False
        succeeded = [p for p in payments if p["status"] == "succeeded"]
        if len(succeeded) >= 2:
            signatures = [(p["amount"], p["channel"]) for p in succeeded]
            duplicate_candidate = len(signatures) != len(set(signatures))
        data = {
            "order_id": order_id,
            "payments": payments,
            "duplicate_candidate": duplicate_candidate,
            "conclusion": "仅表示存在同金额同渠道的成功流水，仍需支付系统或人工核验",
        }
        self.store.audit(user_id, "PAYMENTS_READ", order_id, {"count": len(payments)}, request_id)
        return ToolOutcome(True, data, evidence=[{"type": "payment", "id": p["payment_id"]} for p in payments])

    def get_refund_status(self, user_id: str, order_id: str, request_id: str = "") -> ToolOutcome:
        if not self._owned_order(user_id, order_id):
            return ToolOutcome(False, None, "ORDER_NOT_FOUND", "订单不存在或不属于当前用户")
        refunds = self.store.fetch_all(
            "SELECT * FROM refund_requests WHERE order_id=? AND user_id=? ORDER BY created_at DESC",
            (order_id, user_id),
        )
        self.store.audit(user_id, "REFUNDS_READ", order_id, {"count": len(refunds)}, request_id)
        return ToolOutcome(True, {"order_id": order_id, "refunds": refunds})

    def create_handoff_ticket(
        self, user_id: str, conv_id: str, request_id: str, reason: str, intent: str,
        urgency: str, summary: Dict[str, Any],
    ) -> Dict[str, Any]:
        existing = self.store.fetch_one("SELECT * FROM handoff_tickets WHERE request_id=?", (request_id,))
        if existing:
            return self._ticket_public(existing)
        ticket_id = f"tk_{uuid.uuid4().hex[:12]}"
        now = utc_now()
        self.store.execute(
            """INSERT INTO handoff_tickets
               (ticket_id,user_id,conv_id,request_id,reason,intent,urgency,summary_json,status,created_at,updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (ticket_id, user_id, conv_id, request_id, reason[:300], intent, urgency,
             json.dumps(summary, ensure_ascii=False), "open", now, now),
        )
        self.store.audit(user_id, "HANDOFF_TICKET_CREATED", ticket_id, {"request_id": request_id, "intent": intent}, request_id)
        return self.get_handoff_ticket(user_id, ticket_id) or {}

    def get_handoff_ticket(self, user_id: str, ticket_id: str) -> Optional[Dict[str, Any]]:
        row = self.store.fetch_one(
            "SELECT * FROM handoff_tickets WHERE ticket_id=? AND user_id=?", (ticket_id, user_id)
        )
        return self._ticket_public(row) if row else None

    def list_handoff_tickets(self, user_id: str) -> List[Dict[str, Any]]:
        return [self._ticket_public(row) for row in self.store.fetch_all(
            "SELECT * FROM handoff_tickets WHERE user_id=? ORDER BY created_at DESC", (user_id,)
        )]

    @staticmethod
    def _ticket_public(row: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "ticket_id": row["ticket_id"], "request_id": row["request_id"], "reason": row["reason"],
            "intent": row["intent"], "urgency": row["urgency"], "status": row["status"],
            "summary": json.loads(row.get("summary_json") or "{}"), "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def check_return_eligibility(self, user_id: str, order_id: str) -> EligibilityResult:
        order = self._owned_order(user_id, order_id)
        if not order:
            return EligibilityResult(False, "ORDER_NOT_FOUND", "订单不存在或不属于当前用户", order_id)
        if bool(order["is_virtual"]):
            return EligibilityResult(
                False, "VIRTUAL_PRODUCT_MANUAL_REVIEW", "虚拟商品不适用普通七天无理由退款，需要人工核验具体政策",
                order_id, requires_manual_review=True, evidence={"product_id": order["product_id"]},
            )
        if order["status"] not in {"paid", "shipped", "delivered"}:
            return EligibilityResult(False, "ORDER_STATUS_NOT_ELIGIBLE", "当前订单状态不支持发起普通退款", order_id)
        if order["status"] == "delivered" and order.get("delivered_at"):
            delivered = datetime.fromisoformat(order["delivered_at"])
            age_days = (datetime.now(timezone.utc) - delivered).total_seconds() / 86400
            if age_days > 7:
                return EligibilityResult(
                    False, "RETURN_WINDOW_EXPIRED", "订单已超过七天普通退货窗口，需要人工核验",
                    order_id, requires_manual_review=True, evidence={"delivered_at": order["delivered_at"]},
                )
        return EligibilityResult(
            True, "ELIGIBLE", "订单通过基础退款资格预检，执行前仍需用户确认",
            order_id, refundable_amount=float(order["amount"]),
            evidence={"order_status": order["status"], "product_id": order["product_id"]},
        )

    def prepare_refund(
        self,
        user_id: str,
        order_id: str,
        reason: str,
        idempotency_key: Optional[str] = None,
        request_id: str = "",
    ) -> ActionResult:
        eligibility = self.check_return_eligibility(user_id, order_id)
        if not eligibility.eligible:
            return ActionResult(
                action_id="", action_type="create_refund", status=ActionStatus.FAILED,
                confirmation_required=False, message=eligibility.reason,
                data={"reason_code": eligibility.reason_code, "manual_review": eligibility.requires_manual_review},
            )
        normalized_reason = (reason or "用户申请退款").strip()[:300]
        key = idempotency_key or hashlib.sha256(
            f"refund:{user_id}:{order_id}:{normalized_reason}".encode("utf-8")
        ).hexdigest()
        existing = self.store.fetch_one("SELECT * FROM pending_actions WHERE idempotency_key=?", (key,))
        if existing:
            return self._action_from_row(existing, "已存在相同退款动作，未重复创建")

        action_id = f"act_{uuid.uuid4().hex[:12]}"
        payload = {
            "amount": eligibility.refundable_amount,
            "reason": normalized_reason,
            "eligibility_reason": eligibility.reason_code,
        }
        now = utc_now()
        try:
            self.store.execute(
                """INSERT INTO pending_actions
                   (action_id,action_type,user_id,order_id,payload_json,status,idempotency_key,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?)""",
                (action_id, "create_refund", user_id, order_id, json.dumps(payload, ensure_ascii=False),
                 ActionStatus.AWAITING_CONFIRMATION.value, key, now, now),
            )
        except Exception:
            existing = self.store.fetch_one("SELECT * FROM pending_actions WHERE idempotency_key=?", (key,))
            if existing:
                return self._action_from_row(existing, "已存在相同退款动作，未重复创建")
            raise
        self.store.audit(user_id, "REFUND_PREPARED", action_id, {"order_id": order_id, **payload}, request_id)
        return ActionResult(
            action_id, "create_refund", ActionStatus.AWAITING_CONFIRMATION, True,
            f"请确认是否为订单 {order_id} 发起 ¥{eligibility.refundable_amount:.2f} 退款申请。",
            data={"order_id": order_id, **payload},
        )

    def prepare_cancel_order(
        self, user_id: str, order_id: str, reason: str = "用户申请取消", idempotency_key: Optional[str] = None,
        request_id: str = "",
    ) -> ActionResult:
        order = self._owned_order(user_id, order_id)
        if not order:
            return ActionResult("", "cancel_order", ActionStatus.FAILED, False, "订单不存在或不属于当前用户")
        if order["status"] not in {"pending_payment", "paid"}:
            return ActionResult("", "cancel_order", ActionStatus.FAILED, False, "订单已进入发货或完成阶段，不能直接取消，需要人工处理")
        return self._prepare_order_action(
            "cancel_order", user_id, order_id, {"reason": (reason or "用户申请取消").strip()[:300]},
            idempotency_key, request_id, f"请确认是否取消订单 {order_id}。",
        )

    def prepare_address_change(
        self, user_id: str, order_id: str, new_address: str, idempotency_key: Optional[str] = None,
        request_id: str = "",
    ) -> ActionResult:
        order = self._owned_order(user_id, order_id)
        if not order:
            return ActionResult("", "change_address", ActionStatus.FAILED, False, "订单不存在或不属于当前用户")
        if order["status"] not in {"pending_payment", "paid"}:
            return ActionResult("", "change_address", ActionStatus.FAILED, False, "订单已经发货，不能直接修改地址，需要联系人工客服")
        address = (new_address or "").strip()
        if len(address) < 6 or len(address) > 200:
            return ActionResult("", "change_address", ActionStatus.FAILED, False, "请提供有效的新收货地址")
        return self._prepare_order_action(
            "change_address", user_id, order_id, {"new_address": address}, idempotency_key, request_id,
            f"请确认是否修改订单 {order_id} 的收货地址。",
        )

    def _prepare_order_action(
        self, action_type: str, user_id: str, order_id: str, payload: Dict[str, Any],
        idempotency_key: Optional[str], request_id: str, message: str,
    ) -> ActionResult:
        canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        key = idempotency_key or hashlib.sha256(
            f"{action_type}:{user_id}:{order_id}:{canonical}".encode("utf-8")
        ).hexdigest()
        existing = self.store.fetch_one("SELECT * FROM pending_actions WHERE idempotency_key=?", (key,))
        if existing:
            return self._action_from_row(existing, "已存在相同待确认动作，未重复创建")
        action_id = f"act_{uuid.uuid4().hex[:12]}"
        now = utc_now()
        try:
            self.store.execute(
                """INSERT INTO pending_actions
                   (action_id,action_type,user_id,order_id,payload_json,status,idempotency_key,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?)""",
                (action_id, action_type, user_id, order_id, canonical,
                 ActionStatus.AWAITING_CONFIRMATION.value, key, now, now),
            )
        except Exception:
            existing = self.store.fetch_one("SELECT * FROM pending_actions WHERE idempotency_key=?", (key,))
            if existing:
                return self._action_from_row(existing, "已存在相同待确认动作，未重复创建")
            raise
        self.store.audit(user_id, f"{action_type.upper()}_PREPARED", action_id, {"order_id": order_id}, request_id)
        return ActionResult(
            action_id, action_type, ActionStatus.AWAITING_CONFIRMATION, True, message,
            data={"order_id": order_id, **payload},
        )

    def confirm_action(self, user_id: str, action_id: str, request_id: str = "") -> ActionResult:
        def _execute(conn: sqlite3.Connection) -> ActionResult:
            row = conn.execute(
                "SELECT * FROM pending_actions WHERE action_id=? AND user_id=?", (action_id, user_id)
            ).fetchone()
            if not row:
                return ActionResult(action_id, "unknown", ActionStatus.FAILED, False, "动作不存在或无权确认")
            action = dict(row)
            if action["status"] == ActionStatus.SUCCEEDED.value:
                return self._action_from_row(action, "动作已经执行成功，未重复执行")
            if action["status"] != ActionStatus.AWAITING_CONFIRMATION.value:
                return self._action_from_row(action, f"当前状态 {action['status']} 不允许确认")
            payload = json.loads(action["payload_json"])
            conn.execute(
                "UPDATE pending_actions SET status=?,updated_at=? WHERE action_id=?",
                (ActionStatus.EXECUTING.value, utc_now(), action_id),
            )
            resource_id: Optional[str] = None
            result_data: Dict[str, Any] = {"order_id": action["order_id"]}
            if action["action_type"] == "create_refund":
                payment = conn.execute(
                    "SELECT * FROM payments WHERE order_id=? AND status='succeeded' ORDER BY paid_at DESC LIMIT 1",
                    (action["order_id"],),
                ).fetchone()
                provider_status = "pending"
                if self.payment_gateway and payment and dict(payment).get("channel") == "stripe":
                    gateway_result = self.payment_gateway.refund(
                        dict(payment)["payment_id"],
                        float(payload["amount"]),
                        action_id,
                        {"order_id": action["order_id"], "user_id": user_id},
                    )
                    if not gateway_result.success:
                        raise ValueError(
                            f"支付渠道退款失败 [{gateway_result.error_code or 'UNKNOWN'}]："
                            f"{gateway_result.error or '请稍后重试'}"
                        )
                    resource_id = gateway_result.refund_id
                    provider_status = gateway_result.status
                else:
                    resource_id = f"rf_{uuid.uuid4().hex[:12]}"
                conn.execute(
                    """INSERT INTO refund_requests(refund_id,order_id,user_id,amount,reason,status,created_at)
                       VALUES(?,?,?,?,?,?,?)""",
                    (resource_id, action["order_id"], user_id, float(payload["amount"]), payload["reason"], provider_status, utc_now()),
                )
                event_type = "REFUND_CREATED"
                message = (
                    f"支付渠道退款已创建，当前状态为 {provider_status}。"
                    if self.payment_gateway and payment and dict(payment).get("channel") == "stripe"
                    else "退款申请已创建，当前状态为待审核。"
                )
                result_data.update({"refund_id": resource_id, "refund_status": provider_status})
            elif action["action_type"] == "cancel_order":
                updated = conn.execute(
                    "UPDATE orders SET status='cancelled' WHERE order_id=? AND user_id=? AND status IN ('pending_payment','paid')",
                    (action["order_id"], user_id),
                ).rowcount
                if not updated:
                    raise ValueError("订单状态已变化，当前不能取消")
                resource_id = action["order_id"]
                event_type, message = "ORDER_CANCELLED", "订单已取消。"
                result_data["order_status"] = "cancelled"
            elif action["action_type"] == "change_address":
                updated = conn.execute(
                    "UPDATE orders SET address=? WHERE order_id=? AND user_id=? AND status IN ('pending_payment','paid')",
                    (payload["new_address"], action["order_id"], user_id),
                ).rowcount
                if not updated:
                    raise ValueError("订单状态已变化，当前不能修改地址")
                resource_id = action["order_id"]
                event_type, message = "ORDER_ADDRESS_CHANGED", "收货地址已修改。"
                result_data["address_changed"] = True
            else:
                raise ValueError(f"不支持的动作类型: {action['action_type']}")
            conn.execute(
                "UPDATE pending_actions SET status=?,resource_id=?,updated_at=? WHERE action_id=?",
                (ActionStatus.SUCCEEDED.value, resource_id, utc_now(), action_id),
            )
            conn.execute(
                "INSERT INTO audit_events(request_id,user_id,event_type,target_id,detail_json,created_at) VALUES(?,?,?,?,?,?)",
                (request_id, user_id, event_type, resource_id,
                 json.dumps({"action_id": action_id, "order_id": action["order_id"]}, ensure_ascii=False), utc_now()),
            )
            return ActionResult(
                action_id, action["action_type"], ActionStatus.SUCCEEDED, False,
                message, resource_id=resource_id, data=result_data,
            )

        try:
            return self.store.transaction(_execute)
        except ValueError as ex:
            return ActionResult(action_id, "unknown", ActionStatus.FAILED, False, str(ex))
        except Exception:
            existing = self.store.fetch_one(
                "SELECT * FROM refund_requests WHERE order_id=? AND user_id=? ORDER BY created_at DESC",
                (self.store.fetch_one("SELECT order_id FROM pending_actions WHERE action_id=?", (action_id,)) or {}).get("order_id", ""), user_id,
            )
            if existing:
                return ActionResult(
                    action_id, "create_refund", ActionStatus.SUCCEEDED, False,
                    "该订单已存在退款申请，未重复创建。", resource_id=existing.get("refund_id"),
                )
            return ActionResult(
                action_id, "unknown", ActionStatus.FAILED, False,
                "动作执行失败，业务状态未提交，请稍后重试或转人工处理。",
            )

    @staticmethod
    def _action_from_row(row: Dict[str, Any], message: str) -> ActionResult:
        payload = json.loads(row.get("payload_json") or "{}")
        status = ActionStatus(row["status"])
        return ActionResult(
            row["action_id"], row["action_type"], status,
            status == ActionStatus.AWAITING_CONFIRMATION, message,
            resource_id=row.get("resource_id"), data={"order_id": row.get("order_id"), **payload},
        )
