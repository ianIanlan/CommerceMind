from pathlib import Path

from evaluation.orchestration_ablation import OrchestrationAblationRunner, load_orchestration_cases


def test_conditional_multi_agent_improves_domain_coverage():
    root = Path(__file__).resolve().parent.parent
    results = OrchestrationAblationRunner().run(load_orchestration_cases(root / "data/eval/orchestration_cases.jsonl"))
    single = next(item for item in results if item.name == "single_agent")
    conditional = next(item for item in results if item.name == "conditional_multi_agent")

    assert conditional.domain_recall > single.domain_recall
    assert conditional.avg_agents > single.avg_agents
    assert conditional.unnecessary_agent_rate <= 0.05
    assert conditional.multi_domain_exact > single.multi_domain_exact


def test_frozen_orchestration_holdout_v1_is_not_silently_changed():
    import hashlib

    path = Path("data/eval/orchestration_holdout_v1.jsonl")
    cases = load_orchestration_cases(path)

    assert len(cases) == 12
    assert hashlib.sha256(path.read_bytes()).hexdigest() == "8b17d9d34b4a6a99fbd5c1713caef28ecfac57d1f19a7ecf1c846aae77bdad0d"
