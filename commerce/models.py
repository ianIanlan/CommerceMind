"""电商领域的数据契约。领域层不依赖 FastAPI 或 LLM。"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class OrderStatus(str, Enum):
    PENDING_PAYMENT = "pending_payment"
    PAID = "paid"
    SHIPPED = "shipped"
    DELIVERED = "delivered"
    CANCELLED = "cancelled"
    REFUNDED = "refunded"


class RefundStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    PROCESSING = "processing"
    COMPLETED = "completed"


class ActionStatus(str, Enum):
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    EXECUTING = "executing"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class EligibilityResult:
    eligible: bool
    reason_code: str
    reason: str
    order_id: str
    refundable_amount: float = 0.0
    requires_manual_review: bool = False
    evidence: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ActionResult:
    action_id: str
    action_type: str
    status: ActionStatus
    confirmation_required: bool
    message: str
    resource_id: Optional[str] = None
    data: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ToolOutcome:
    success: bool
    data: Any
    error_code: Optional[str] = None
    error: Optional[str] = None
    evidence: List[Dict[str, Any]] = field(default_factory=list)
