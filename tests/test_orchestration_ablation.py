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
