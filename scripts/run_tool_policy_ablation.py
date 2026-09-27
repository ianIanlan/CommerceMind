#!/usr/bin/env python3
"""Measure required-tool reliability with the policy enabled or disabled."""
from __future__ import annotations

import argparse
import json
import pathlib

from run_composer_ablation import run


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--policy-mode", choices=("off", "on"), required=True)
    parser.add_argument("--repeats", type=int, default=10)
    parser.add_argument("--output-dir", type=pathlib.Path, default=pathlib.Path("outputs"))
    args = parser.parse_args()
    result = run(args.base_url, f"tool_policy_{args.policy_mode}", args.repeats, 120.0)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    target = args.output_dir / f"tool_policy_ablation_{args.policy_mode}.json"
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(target)
    # Billing evidence is the primary endpoint of this experiment.
    return 0 if result["assertion_rates"]["billing_evidence"] == 1.0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
