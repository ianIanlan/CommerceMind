#!/usr/bin/env python3
import argparse
import hashlib
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from evaluation.orchestration_ablation import OrchestrationAblationRunner, load_orchestration_cases, write_orchestration_report

parser = argparse.ArgumentParser()
parser.add_argument("--dataset", type=pathlib.Path, default=ROOT / "data/eval/orchestration_cases.jsonl")
parser.add_argument("--name", default="orchestration_ablation")
args = parser.parse_args()

cases = load_orchestration_cases(args.dataset)
results = OrchestrationAblationRunner().run(cases)
json_path = ROOT / "outputs" / f"{args.name}.json"
markdown_path = ROOT / "outputs" / f"{args.name}.md"
write_orchestration_report(
    results,
    json_path,
    markdown_path,
    dataset_sha256=hashlib.sha256(args.dataset.read_bytes()).hexdigest(),
    dataset_size=len(cases),
    tuned_on_dataset="holdout" not in args.dataset.name.lower(),
)
print(markdown_path)
