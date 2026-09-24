import asyncio

from core.intent_recognizer import IntentCategory
from evaluation.ablation import AblationCase, AblationVariant, IntentAblationRunner, dataset_sha256, load_cases


class FakeRecognizer:
    def _pattern_recognize(self, message):
        return {"intent": IntentCategory.LOGISTICS if "物流" in message else IntentCategory.OTHER, "confidence": 0.5}

    async def _embedding_recognize(self, message):
        return {"intent": IntentCategory.LOGISTICS, "confidence": 0.8}

    async def _llm_recognize(self, message, history):
        return {"intent": IntentCategory.LOGISTICS, "confidence": 0.95}


def test_offline_ablation_skips_llm_variants_and_computes_metrics():
    cases = [AblationCase("c1", "查询物流", "logistics")]
    report = asyncio.run(IntentAblationRunner(FakeRecognizer(), live_llm=False).run(cases))

    assert report.dataset_size == 1
    assert {item.name for item in report.variants} == {
        "pattern_only", "embedding_only", "embedding_pattern",
    }
    assert all(item.accuracy == 1.0 for item in report.variants)


def test_weighted_fusion_uses_accumulated_evidence():
    outputs = {
        "llm": {"intent": IntentCategory.OTHER, "confidence": 0.4},
        "embedding": {"intent": IntentCategory.LOGISTICS, "confidence": 0.8},
        "pattern": {"intent": IntentCategory.LOGISTICS, "confidence": 0.5},
    }
    variant = AblationVariant(
        "test", ("llm", "embedding", "pattern"),
        {"llm": 0.4, "embedding": 0.4, "pattern": 0.2}, "test",
    )

    intent, confidence = IntentAblationRunner._fuse(outputs, variant)

    assert intent is IntentCategory.LOGISTICS
    assert round(confidence, 2) == 0.42


def test_robustness_dataset_is_valid_and_unique():
    from pathlib import Path

    cases = load_cases(Path("data/eval/intent_robustness_cases.jsonl"))
    assert len(cases) == 20
    assert len({case.case_id for case in cases}) == 20
    assert all(case.tags for case in cases)


def test_frozen_intent_holdout_v1_identity_and_coverage():
    from pathlib import Path

    path = Path("data/eval/intent_holdout_v1.jsonl")
    cases = load_cases(path)

    assert len(cases) == 20
    assert dataset_sha256(path) == "2c68252a0e05c29e2e18e32399528b5a681701f3b1da12fe486e87efb6d88801"
    assert all("holdout" in case.tags for case in cases)
