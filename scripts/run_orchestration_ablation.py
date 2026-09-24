#!/usr/bin/env python3
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from evaluation.orchestration_ablation import OrchestrationAblationRunner, load_orchestration_cases, write_orchestration_report

cases = load_orchestration_cases(ROOT / "data/eval/orchestration_cases.jsonl")
results = OrchestrationAblationRunner().run(cases)
write_orchestration_report(results, ROOT / "outputs/orchestration_ablation.json", ROOT / "outputs/orchestration_ablation.md")
print(ROOT / "outputs/orchestration_ablation.md")
