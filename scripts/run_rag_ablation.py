#!/usr/bin/env python3
import argparse
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from evaluation.rag_ablation import RagAblationRunner, load_rag_cases, write_rag_report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=pathlib.Path, default=ROOT / "data/eval/rag_cases.jsonl")
    parser.add_argument("--output-dir", type=pathlib.Path, default=ROOT / "outputs")
    args = parser.parse_args()
    report = RagAblationRunner().run(load_rag_cases(args.dataset))
    write_rag_report(report, args.output_dir / "rag_ablation.json", args.output_dir / "rag_ablation.md")
    print(args.output_dir / "rag_ablation.md")


if __name__ == "__main__":
    main()
