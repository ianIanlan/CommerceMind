from pathlib import Path

from evaluation.rag_ablation import RagAblationRunner, load_rag_cases


def test_rag_dataset_is_valid_and_ablation_runs():
    root = Path(__file__).resolve().parent.parent
    cases = load_rag_cases(root / "data/eval/rag_cases.jsonl")
    report = RagAblationRunner().run(cases)

    assert report.dataset_size == 22
    assert report.answerable_cases == 19
    assert report.unanswerable_cases == 3
    assert {item.name for item in report.variants} == {"no_rag", "lexical", "filtered", "expanded_filtered"}


def test_domain_filter_never_returns_wrong_domain():
    root = Path(__file__).resolve().parent.parent
    report = RagAblationRunner().run(load_rag_cases(root / "data/eval/rag_cases.jsonl"))
    filtered = next(item for item in report.variants if item.name == "filtered")

    assert filtered.wrong_domain_rate == 0.0
