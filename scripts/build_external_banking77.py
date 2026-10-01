#!/usr/bin/env python3
"""Build a frozen external intent benchmark from Banking77's official test split."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import random
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
UPSTREAM_COMMIT = "57ec275d8078af65b7731c2a98be812d844a6d6b"
SEED = 20261001
PER_CATEGORY = 15
CATEGORY_MAP = {
    "transaction_charged_twice": "duplicate_payment",
    "request_refund": "refund",
    "Refund_not_showing_up": "refund_status",
    "declined_card_payment": "payment_issue",
    "card_payment_not_recognised": "account_security",
    "compromised_card": "account_security",
    "passcode_forgotten": "technical_login",
    "card_delivery_estimate": "logistics",
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source_csv", type=Path, help="Banking77 official banking_data/test.csv")
    parser.add_argument("--output", type=Path, default=ROOT / "data/eval/external_banking77_v1.jsonl")
    args = parser.parse_args()

    raw = args.source_csv.read_bytes()
    rows_by_category = {category: [] for category in CATEGORY_MAP}
    with args.source_csv.open(encoding="utf-8", newline="") as handle:
        for source_index, row in enumerate(csv.DictReader(handle), start=1):
            category = row["category"]
            if category in rows_by_category:
                rows_by_category[category].append((source_index, row["text"]))

    rng = random.Random(SEED)
    records = []
    for category, expected in CATEGORY_MAP.items():
        candidates = rows_by_category[category]
        if len(candidates) < PER_CATEGORY:
            raise ValueError(f"{category} only has {len(candidates)} rows")
        selected = sorted(rng.sample(candidates, PER_CATEGORY))
        for source_index, message in selected:
            records.append({
                "case_id": f"banking77::{category}::{source_index:04d}",
                "message": message,
                "expected_intent": expected,
                "tags": ["external", "banking77", category],
            })

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        "\n".join(json.dumps(record, ensure_ascii=False) for record in records) + "\n",
        encoding="utf-8",
    )
    manifest = {
        "name": "CommerceMind external Banking77 v1",
        "source": "https://github.com/PolyAI-LDN/task-specific-datasets/tree/master/banking_data",
        "source_split": "test",
        "source_commit": UPSTREAM_COMMIT,
        "source_csv_sha256": hashlib.sha256(raw).hexdigest(),
        "license": "CC BY 4.0",
        "citation": "Casanueva et al., Efficient Intent Detection with Dual Sentence Encoders, NLP4ConvAI 2020",
        "selection_seed": SEED,
        "per_source_category": PER_CATEGORY,
        "category_map": CATEGORY_MAP,
        "case_count": len(records),
        "limitations": [
            "Banking-domain English queries are used as a cross-domain transfer test, not as e-commerce production traffic.",
            "Source labels are deterministically mapped to the closest CommerceMind intent without changing source text.",
            "The external test set must not be used to tune prompts, patterns, weights, or thresholds.",
        ],
    }
    manifest_path = args.output.with_suffix(".manifest.json")
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"{args.output}: {len(records)} cases")
    print(manifest_path)


if __name__ == "__main__":
    main()
