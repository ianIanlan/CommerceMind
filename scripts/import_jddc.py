#!/usr/bin/env python3
"""Normalize an authorized JDDC export into CommerceMind's intent-eval schema.

The resulting JSONL contains licensed dialogue text and is intentionally ignored by git.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
VALID_INTENTS = {
    "query", "complaint", "request", "greeting", "escalation", "technical",
    "billing", "account", "feedback", "order_status", "logistics", "refund",
    "invoice", "payment_issue", "account_security", "technical_login",
    "technical_crash", "human_handoff", "product_query", "order_cancel",
    "address_change", "return_exchange", "damaged_item", "duplicate_payment",
    "refund_status", "other",
}


def normalize_rows(
    source: Path,
    mapping: dict[str, str],
    *,
    delimiter: str = "\t",
    conversation_field: str = "conversation_id",
    message_field: str = "query",
    intent_field: str = "intent",
    split_field: str = "split",
    selected_split: str = "test",
) -> list[dict[str, object]]:
    unknown_targets = sorted(set(mapping.values()) - VALID_INTENTS)
    if unknown_targets:
        raise ValueError(f"Unsupported CommerceMind intents: {unknown_targets}")

    records: list[dict[str, object]] = []
    with source.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, delimiter=delimiter)
        required = {conversation_field, message_field, intent_field}
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"Missing source fields: {sorted(missing)}")
        for row_number, row in enumerate(reader, start=2):
            if split_field in row and selected_split and row[split_field] != selected_split:
                continue
            source_intent = (row[intent_field] or "").strip()
            if source_intent not in mapping:
                continue
            message = (row[message_field] or "").strip()
            if not message:
                continue
            conversation_id = (row[conversation_field] or str(row_number)).strip()
            records.append({
                "case_id": f"jddc::{conversation_id}::{row_number}",
                "message": message,
                "expected_intent": mapping[source_intent],
                "tags": ["external", "jddc", source_intent],
            })
    if not records:
        raise ValueError("No rows were imported; verify fields, split, and intent mapping")
    return records


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="Authorized JDDC CSV/TSV export")
    parser.add_argument("--mapping", type=Path, required=True, help="JSON source-to-CommerceMind intent map")
    parser.add_argument("--output", type=Path, default=ROOT / "data/eval/external_jddc_v1.jsonl")
    parser.add_argument("--delimiter", choices=["comma", "tab"], default="tab")
    parser.add_argument("--conversation-field", default="conversation_id")
    parser.add_argument("--message-field", default="query")
    parser.add_argument("--intent-field", default="intent")
    parser.add_argument("--split-field", default="split")
    parser.add_argument("--split", default="test")
    args = parser.parse_args()

    mapping = json.loads(args.mapping.read_text(encoding="utf-8"))
    records = normalize_rows(
        args.source, mapping,
        delimiter="\t" if args.delimiter == "tab" else ",",
        conversation_field=args.conversation_field,
        message_field=args.message_field,
        intent_field=args.intent_field,
        split_field=args.split_field,
        selected_split=args.split,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        "\n".join(json.dumps(item, ensure_ascii=False) for item in records) + "\n",
        encoding="utf-8",
    )
    manifest = {
        "name": "CommerceMind external JDDC v1",
        "source": "authorized local JDDC export",
        "source_sha256": hashlib.sha256(args.source.read_bytes()).hexdigest(),
        "mapping_sha256": hashlib.sha256(args.mapping.read_bytes()).hexdigest(),
        "selected_split": args.split,
        "case_count": len(records),
        "label_distribution": dict(sorted(Counter(str(item["expected_intent"]) for item in records).items())),
        "redistribution": "dataset text excluded from git; follow JDDC provider terms",
    }
    manifest_path = args.output.with_suffix(".manifest.json")
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"{args.output}: {len(records)} cases")
    print(manifest_path)


if __name__ == "__main__":
    main()
