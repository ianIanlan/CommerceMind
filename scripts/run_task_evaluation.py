#!/usr/bin/env python3
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from evaluation.task_evaluator import CommerceTaskEvaluator, write_task_report

results = CommerceTaskEvaluator().run()
write_task_report(results, ROOT / "outputs/task_evaluation.json", ROOT / "outputs/task_evaluation.md")
print(ROOT / "outputs/task_evaluation.md")
raise SystemExit(0 if all(item.passed for item in results) else 1)
