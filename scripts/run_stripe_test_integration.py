#!/usr/bin/env python3
"""Run a real Stripe Test Mode payment -> CommerceMind refund round trip."""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys

import httpx
from dotenv import load_dotenv

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from commerce.payment_gateway import StripePaymentGateway
from commerce.service import CommerceService
from commerce.store import CommerceStore


def create_test_payment_intent(secret_key: str, base_url: str) -> str:
    response = httpx.post(
        f"{base_url.rstrip('/')}/v1/payment_intents",
        headers={"Authorization": f"Bearer {secret_key}"},
        data={
            "amount": "39900",
            "currency": "cny",
            "payment_method": "pm_card_visa",
            "payment_method_types[]": "card",
            "confirm": "true",
            "metadata[source]": "commercemind_integration_test",
            "metadata[order_id]": "ORD-10001",
        },
        timeout=20.0,
    )
    payload = response.json()
    if response.is_error:
        error = payload.get("error", {}) if isinstance(payload, dict) else {}
        raise RuntimeError(str(error.get("message") or "Stripe test payment creation failed"))
    if payload.get("status") != "succeeded":
        raise RuntimeError(f"Stripe test payment did not succeed: {payload.get('status')}")
    return str(payload["id"])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--execute",
        action="store_true",
        help="required safety switch: creates and refunds a Stripe Test Mode payment",
    )
    args = parser.parse_args()
    load_dotenv(ROOT / ".env.local")
    load_dotenv(ROOT / ".env")
    secret_key = os.getenv("STRIPE_SECRET_KEY", "").strip()
    base_url = os.getenv("STRIPE_BASE_URL", "https://api.stripe.com")
    if not secret_key.startswith("sk_test_"):
        raise SystemExit("STRIPE_SECRET_KEY must be a Stripe Test Mode key (sk_test_...)")
    if not args.execute:
        raise SystemExit("Refusing to create a test payment without --execute")

    payment_intent = create_test_payment_intent(secret_key, base_url)
    store = CommerceStore(":memory:")
    store.seed_demo_data()
    store.execute(
        "UPDATE payments SET payment_id=?, channel='stripe' WHERE order_id=?",
        (payment_intent, "ORD-10001"),
    )
    service = CommerceService(store, StripePaymentGateway(secret_key, base_url=base_url))
    action = service.prepare_refund(
        "demo-user",
        "ORD-10001",
        "Stripe Test Mode integration verification",
        "stripe-live-integration-v1",
        "stripe-integration-test",
    )
    result = service.confirm_action("demo-user", action.action_id, "stripe-integration-test")
    repeated = service.confirm_action("demo-user", action.action_id, "stripe-integration-repeat")
    output = {
        "payment_intent": payment_intent,
        "action_id": action.action_id,
        "refund_id": result.resource_id,
        "refund_status": result.data.get("refund_status"),
        "action_status": result.status.value,
        "idempotent_repeat_same_refund": repeated.resource_id == result.resource_id,
        "audit_events": len(store.fetch_all("SELECT * FROM audit_events")),
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0 if result.status.value == "succeeded" and output["idempotent_repeat_same_refund"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
