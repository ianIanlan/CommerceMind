import hashlib
import json
from collections import Counter
from pathlib import Path

from evaluation.ablation import load_cases


ROOT = Path(__file__).resolve().parent.parent


def test_external_banking77_v1_is_frozen_and_balanced_by_source_category():
    dataset = ROOT / "data/eval/external_banking77_v1.jsonl"
    cases = load_cases(dataset)
    categories = Counter(case.tags[-1] for case in cases)
    assert len(cases) == 120
    assert len(categories) == 8
    assert set(categories.values()) == {15}
    assert hashlib.sha256(dataset.read_bytes()).hexdigest() == "6c7892610e64df0a8cb2fe28f4b00fbeddbecf9c89d15d65b589377c74fac3eb"


def test_external_manifest_pins_source_and_license():
    manifest = json.loads((ROOT / "data/eval/external_banking77_v1.manifest.json").read_text(encoding="utf-8"))
    assert manifest["source_split"] == "test"
    assert manifest["license"] == "CC BY 4.0"
    assert manifest["source_csv_sha256"] == "d12d6e3bc4c3103966ae786dc435913c0c563dfa328f5a3646d0e62cfeeb474d"
    assert manifest["case_count"] == 120
