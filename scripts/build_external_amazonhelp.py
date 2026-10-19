#!/usr/bin/env python3
"""Build a frozen, auditable intent benchmark from public AmazonHelp dialogues.

Candidate rules are deliberately high precision and only select cases. They are not
an intent model and must not be presented as source-provided annotations.
"""
from __future__ import annotations

import argparse
import glob
import hashlib
import html
import json
import re
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SEED = 20261019

RULES: list[tuple[str, str, tuple[str, ...]]] = [
    ("refund_status", r"\brefund\b.{0,80}\b(still|when|where|waiting|pending|hasn.t|haven.t|not|never)\b|\b(still|waiting|pending|hasn.t|haven.t|not|never)\b.{0,80}\brefund\b", ()),
    ("order_cancel", r"\b(cancel|cancelled|canceled|cancelling)\b.{0,50}\b(order|item|product|purchase)\b|\b(order|item|product|purchase)\b.{0,50}\b(cancel|cancelled|canceled|cancelling)\b", ("prime account", "membership")),
    ("return_exchange", r"\b(return|returned|returning|exchange|replacement|replace)\b", ("refund", "cancel")),
    ("payment_issue", r"\b(double charged|charged twice|unknown charge|unauthori[sz]ed charge|deducted|deduction|cash ?back|payment failed|card charged|charged my card)\b", ("refund",)),
    ("human_handoff", r"\b(manager|supervisor|representative|human agent|call ?back|call me|escalat)\b", ()),
    ("logistics", r"\b(delivery|delivered|deliver|package|shipment|courier|tracking)\b", ("refund", "return", "cancel", "charged", "payment")),
]

# Candidate rules produced a review queue. These IDs were then checked against the
# full dialogue and frozen before the first CommerceMind model run. The labels are
# still project-authored silver labels because there was no second independent annotator.
CURATED_IDS: dict[str, tuple[str, ...]] = {
    "refund_status": (
        "1117727", "2171912", "2628931", "193073", "2310196",
        "417251", "89354", "2042040", "2535341", "2952230",
    ),
    "order_cancel": (
        "275568", "1299544", "2906627", "837121", "1806534",
        "197758", "466653", "2528265", "1525957", "2634843",
    ),
    "return_exchange": (
        "1119595", "1127137", "1536949", "1661464", "1719225",
        "2543901", "2569715", "2787780", "440340", "970300",
    ),
    "payment_issue": (
        "394885", "2899547", "1429170", "2539008", "179581",
        "1573563", "781200", "1506354", "869872", "1031533",
    ),
    "human_handoff": ("1664433", "654274", "281657", "1309085", "786763"),
    "logistics": (
        "1249428", "1366048", "1554844", "2135614", "230213",
        "2686757", "2871508", "486083", "553306", "522547",
    ),
}

# Frozen only after the diagnostic optimization was complete. These source IDs do
# not overlap CURATED_IDS and are reserved for a single post-optimization run.
HOLDOUT_IDS: dict[str, tuple[str, ...]] = {
    "refund_status": ("288998", "1204948", "978370", "990781", "572839"),
    "order_cancel": ("2930623", "2720001", "2878604", "348404", "51603"),
    "return_exchange": ("2867113", "1026011", "1875447", "2367978", "2847321"),
    "payment_issue": ("2179030", "2511468", "1708", "805540", "2342074"),
    "logistics": ("828297", "2972846", "2924561", "1939130", "1235523"),
}


def _plain_conversation(value: str) -> str:
    text = html.unescape(re.sub(r"<[^>]+>", " ", value))
    return " ".join(text.split())


def _first_customer_message(conversation: str) -> str:
    text = _plain_conversation(conversation)
    match = re.search(r"customer\s*[—-]\s*(.*?)(?=agent\s*[—-]|$)", text, re.I)
    return " ".join((match.group(1) if match else "").split())


def _sanitize_message(message: str) -> str:
    """Remove identifiers before text is stored or sent to an external model."""
    message = re.sub(r"https?://\S+|\[link\]", "[LINK]", message, flags=re.I)
    message = re.sub(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b", "[EMAIL]", message)
    message = re.sub(r"(?<![A-Za-z])(?:\d[\s-]?){3,}\d(?![A-Za-z])", "[NUMBER]", message)
    return " ".join(message.split())


def load_unique_rows(source_glob: str) -> list[dict[str, str]]:
    rows: dict[str, dict[str, str]] = {}
    for filename in sorted(glob.glob(source_glob)):
        with open(filename, encoding="utf-8") as handle:
            for line in handle:
                row = json.loads(line)
                rows[str(row["id"])] = row
    if not rows:
        raise ValueError(f"No source rows matched: {source_glob}")
    return list(rows.values())


def assign_intent(message: str) -> str | None:
    lowered = message.lower()
    for intent, pattern, exclusions in RULES:
        if re.search(pattern, lowered, re.I) and not any(term in lowered for term in exclusions):
            return intent
    return None


def build_records(
    rows: list[dict[str, str]],
    selections: dict[str, tuple[str, ...]] = CURATED_IDS,
    split_tag: str = "diagnostic",
) -> list[dict[str, object]]:
    rows_by_id = {str(row["id"]): row for row in rows}
    records: list[dict[str, object]] = []
    for intent, source_ids in selections.items():
        for source_id in source_ids:
            if source_id not in rows_by_id:
                raise ValueError(f"Curated source id missing: {source_id}")
            message = _sanitize_message(_first_customer_message(rows_by_id[source_id]["conversation"]))
            tags = ["external", "amazonhelp", "silver_label"]
            if split_tag != "diagnostic":
                tags.append(split_tag)
            tags.append(intent)
            records.append({
                "case_id": f"amazonhelp::{source_id}",
                "message": message,
                "expected_intent": intent,
                "tags": tags,
            })
    return records


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_glob", help="Glob for ConvoSense annotator JSONL files")
    parser.add_argument("--output", type=Path, default=ROOT / "data/eval/external_amazonhelp_v1.jsonl")
    parser.add_argument(
        "--holdout-output", type=Path,
        default=ROOT / "data/eval/external_amazonhelp_holdout_v1.jsonl",
    )
    args = parser.parse_args()

    source_files = sorted(glob.glob(args.source_glob))
    rows = load_unique_rows(args.source_glob)
    records = build_records(rows)
    holdout_records = build_records(rows, HOLDOUT_IDS, "holdout")
    if {r["case_id"] for r in records} & {r["case_id"] for r in holdout_records}:
        raise ValueError("Diagnostic and holdout source IDs overlap")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        "\n".join(json.dumps(record, ensure_ascii=False) for record in records) + "\n",
        encoding="utf-8",
    )
    manifest = {
        "name": "CommerceMind external AmazonHelp v1",
        "upstream": "venkatrepaka-umich/ConvoSense derived from Customer Support on Twitter",
        "upstream_commit": "82b1b3e39db752a08a70d95bfafd160c334ca86d",
        "license": "CC BY-NC-SA 4.0",
        "selection_seed": SEED,
        "source_file_count": len(source_files),
        "source_file_sha256": {
            Path(name).name + ":" + Path(name).parent.parent.name + ":" + Path(name).parent.name:
            hashlib.sha256(Path(name).read_bytes()).hexdigest()
            for name in source_files
        },
        "label_provenance": "CommerceMind silver labels: high-precision candidate rules followed by one-pass full-dialogue audit; not source-provided gold labels or double-annotated gold data",
        "case_count": len(records),
        "label_distribution": dict(sorted(Counter(str(r["expected_intent"]) for r in records).items())),
        "limitations": [
            "Public Twitter customer-support conversations from 2017, not private Amazon production tickets.",
            "English/Spanish-to-English multi-turn subset; the benchmark uses the first customer message.",
            "Order-like digit sequences, email addresses, phone-like numbers, and links are redacted before evaluation.",
            "Intent labels are CommerceMind silver labels selected by high-precision rules, not official Amazon labels.",
            "CC BY-NC-SA 4.0 restricts this dataset artifact to non-commercial use and requires attribution/share-alike.",
        ],
    }
    args.output.with_suffix(".manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    args.holdout_output.write_text(
        "\n".join(json.dumps(record, ensure_ascii=False) for record in holdout_records) + "\n",
        encoding="utf-8",
    )
    holdout_manifest = {
        **manifest,
        "name": "CommerceMind external AmazonHelp holdout v1",
        "label_provenance": "CommerceMind one-pass full-dialogue silver labels frozen after diagnostic optimization; not source-provided or double-annotated gold labels",
        "case_count": len(holdout_records),
        "label_distribution": dict(sorted(Counter(str(r["expected_intent"]) for r in holdout_records).items())),
        "evaluation_policy": "single post-optimization run; do not tune on these cases",
    }
    args.holdout_output.with_suffix(".manifest.json").write_text(
        json.dumps(holdout_manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"{args.output}: {len(records)} cases from {len(rows)} unique source dialogues")
    print(f"{args.holdout_output}: {len(holdout_records)} frozen holdout cases")


if __name__ == "__main__":
    main()
