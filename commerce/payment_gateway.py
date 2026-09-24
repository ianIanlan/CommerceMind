"""Payment-provider boundary used by high-risk commerce actions."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional, Protocol

import httpx


@dataclass(frozen=True)
class GatewayRefund:
    success: bool
    refund_id: Optional[str]
    status: str
    error_code: Optional[str] = None
    error: Optional[str] = None


class PaymentGateway(Protocol):
    def refund(
        self,
        payment_reference: str,
        amount: float,
        idempotency_key: str,
        metadata: Optional[Dict[str, str]] = None,
    ) -> GatewayRefund: ...


class StripePaymentGateway:
    """Minimal Stripe Test/Live adapter using the official HTTP contract.

    The caller supplies a PaymentIntent ID (``pi_...``). Amounts are converted
    to the smallest currency unit and the CommerceMind action ID is forwarded
    as Stripe's idempotency key.
    """

    def __init__(
        self,
        secret_key: str,
        base_url: str = "https://api.stripe.com",
        timeout_seconds: float = 15.0,
        client: Optional[httpx.Client] = None,
    ):
        if not secret_key or not secret_key.startswith("sk_"):
            raise ValueError("Stripe secret key is missing or malformed")
        self.secret_key = secret_key
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.client = client

    def refund(
        self,
        payment_reference: str,
        amount: float,
        idempotency_key: str,
        metadata: Optional[Dict[str, str]] = None,
    ) -> GatewayRefund:
        if not payment_reference.startswith("pi_"):
            return GatewayRefund(False, None, "failed", "INVALID_PAYMENT_REFERENCE", "需要 Stripe PaymentIntent ID")
        amount_minor = round(amount * 100)
        if amount_minor <= 0:
            return GatewayRefund(False, None, "failed", "INVALID_AMOUNT", "退款金额必须大于 0")

        form: Dict[str, Any] = {"payment_intent": payment_reference, "amount": str(amount_minor)}
        for key, value in (metadata or {}).items():
            form[f"metadata[{key}]"] = value
        headers = {
            "Authorization": f"Bearer {self.secret_key}",
            "Idempotency-Key": idempotency_key,
        }
        try:
            if self.client is not None:
                response = self.client.post(
                    f"{self.base_url}/v1/refunds", data=form, headers=headers, timeout=self.timeout_seconds
                )
            else:
                response = httpx.post(
                    f"{self.base_url}/v1/refunds", data=form, headers=headers, timeout=self.timeout_seconds
                )
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            return GatewayRefund(False, None, "failed", "PROVIDER_UNAVAILABLE", str(exc)[:300])

        if response.is_error:
            provider_error = payload.get("error", {}) if isinstance(payload, dict) else {}
            return GatewayRefund(
                False,
                None,
                "failed",
                str(provider_error.get("code") or provider_error.get("type") or "STRIPE_ERROR"),
                str(provider_error.get("message") or "Stripe refund failed")[:300],
            )
        return GatewayRefund(True, str(payload.get("id")), str(payload.get("status") or "pending"))
