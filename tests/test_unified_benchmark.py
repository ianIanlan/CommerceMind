from pathlib import Path

from evaluation.unified_benchmark import load_benchmark


ROOT = Path(__file__).resolve().parent.parent


def test_benchmark_v1_has_stable_size_and_task_distribution():
    rows = load_benchmark(ROOT / "data/eval/benchmark_v1.jsonl")
    distribution = {task: sum(row["task"] == task for row in rows) for task in {row["task"] for row in rows}}
    assert len(rows) == 160
    assert distribution == {"intent": 70, "routing": 32, "rag": 22, "policy": 36}


def test_benchmark_v1_declares_split_and_expected_contract():
    rows = load_benchmark(ROOT / "data/eval/benchmark_v1.jsonl")
    assert all(row.get("split") and isinstance(row.get("expected"), dict) for row in rows)
    assert any(row["split"] == "holdout" for row in rows)
