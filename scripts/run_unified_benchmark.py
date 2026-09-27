#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import argparse
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
from evaluation.unified_benchmark import UnifiedBenchmarkRunner, load_benchmark, write_report


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--live-llm", action="store_true", help="对 70 条意图用例调用真实模型")
    args = parser.parse_args()
    dataset = ROOT / "data/eval/benchmark_v1.jsonl"
    rows = load_benchmark(dataset)
    recognizer = IntentRecognizer(
        api_key=os.getenv("ANTHROPIC_API_KEY", "offline-key"),
        base_url=os.getenv("ANTHROPIC_BASE_URL") or None,
        model=os.getenv("ANTHROPIC_MODEL", "offline"),
    )
    sections = await UnifiedBenchmarkRunner(recognizer, live_llm=args.live_llm).run(rows)
    if args.live_llm:
        json_path = ROOT / "outputs/unified_benchmark_v1_live.json"
        markdown_path = ROOT / "docs/unified_benchmark_v1.md"
    else:
        json_path = ROOT / "outputs/unified_benchmark_v1_offline.json"
        markdown_path = ROOT / "outputs/unified_benchmark_v1_offline.md"
    write_report(rows, sections, dataset, json_path, markdown_path)
    print(markdown_path)


if __name__ == "__main__":
    asyncio.run(main())
