import hashlib
import json
import re
from collections import Counter
from pathlib import Path

from evaluation.ablation import load_cases
from scripts.build_external_amazonhelp import _sanitize_message, build_records


ROOT = Path(__file__).resolve().parent.parent
DATASET = ROOT / "data/eval/external_amazonhelp_v1.jsonl"
HOLDOUT = ROOT / "data/eval/external_amazonhelp_holdout_v1.jsonl"


def test_external_amazonhelp_v1_is_frozen_and_has_expected_distribution():
    cases = load_cases(DATASET)
    labels = Counter(case.expected_intent for case in cases)
    assert len(cases) == 55
    assert labels == {
        "refund_status": 10,
        "order_cancel": 10,
        "return_exchange": 10,
        "payment_issue": 10,
        "human_handoff": 5,
        "logistics": 10,
    }
    assert hashlib.sha256(DATASET.read_bytes()).hexdigest() == "7ccdfee8d38355ff3061df15a6c6c2afdaf0d141182ad3edf447b7e462f29ea1"


def test_external_amazonhelp_messages_are_redacted():
    for dataset in (DATASET, HOLDOUT):
        for line in dataset.read_text(encoding="utf-8").splitlines():
            message = json.loads(line)["message"]
            assert not re.search(r"https?://|\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b", message)
            assert not re.search(r"(?<![A-Za-z])(?:\d[\s-]?){3,}\d(?![A-Za-z])", message)


def test_external_amazonhelp_holdout_is_frozen_and_disjoint():
    diagnostic = load_cases(DATASET)
    holdout = load_cases(HOLDOUT)
    assert len(holdout) == 25
    assert not ({case.case_id for case in diagnostic} & {case.case_id for case in holdout})
    assert hashlib.sha256(HOLDOUT.read_bytes()).hexdigest() == "53f21f5c6549e691b1549542a55eed0efafe61892edc584154ae3bf50e793c96"


def test_sanitizer_removes_identifiers_without_removing_intent():
    sanitized = _sanitize_message(
        "Order 406-1234567-7654321 charged twice; email user@example.com https://example.com"
    )
    assert "charged twice" in sanitized
    assert "[NUMBER]" in sanitized
    assert "[EMAIL]" in sanitized
    assert "[LINK]" in sanitized
    assert "1234567" not in sanitized


def test_builder_requires_every_curated_source_id():
    try:
        build_records([])
    except ValueError as exc:
        assert "Curated source id missing" in str(exc)
    else:
        raise AssertionError("missing curated source rows must be rejected")


def test_manifest_discloses_silver_labels_and_license():
    manifest = json.loads(DATASET.with_suffix(".manifest.json").read_text(encoding="utf-8"))
    assert manifest["license"] == "CC BY-NC-SA 4.0"
    assert manifest["case_count"] == 55
    assert "silver labels" in manifest["label_provenance"]
