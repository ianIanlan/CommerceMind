#!/usr/bin/env python3
"""Run offline or live intent ablation and write JSON/Markdown reports."""
import argparse
import asyncio
import os
import pathlib
import sys

from dotenv import load_dotenv

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

load_dotenv(ROOT / ".env.local")
load_dotenv(ROOT / ".env")

from core.intent_recognizer import IntentRecognizer
from evaluation.ablation import IntentAblationRunner, dataset_sha256, load_cases, write_report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--live-llm", action="store_true", help="调用真实 LLM；默认只跑本地双路")
    parser.add_argument("--dataset", type=pathlib.Path, default=ROOT / "data/eval/intent_cases.jsonl")
    parser.add_argument("--output-dir", type=pathlib.Path, default=ROOT / "outputs")
    parser.add_argument("--name", default="intent_ablation", help="报告文件名前缀")
    parser.add_argument("--embedding-backend", choices=["char", "fastembed", "remote"])
    parser.add_argument("--embedding-model")
    args = parser.parse_args()

    if args.embedding_backend:
        os.environ["EMBEDDING_BACKEND"] = args.embedding_backend
    if args.embedding_model:
        os.environ["EMBEDDING_MODEL"] = args.embedding_model

    recognizer = IntentRecognizer(
        api_key=os.getenv("ANTHROPIC_API_KEY", "offline-key"),
        base_url=os.getenv("ANTHROPIC_BASE_URL") or None,
        model=os.getenv("ANTHROPIC_MODEL", "deepseek-flash"),
    )
    report = asyncio.run(
        IntentAblationRunner(recognizer, live_llm=args.live_llm).run(
            load_cases(args.dataset), dataset_sha256=dataset_sha256(args.dataset)
        )
    )
    suffix = "live" if args.live_llm else "offline"
    json_path = args.output_dir / f"{args.name}_{suffix}.json"
    markdown_path = args.output_dir / f"{args.name}_{suffix}.md"
    write_report(report, json_path, markdown_path)
    print(markdown_path)


if __name__ == "__main__":
    main()
