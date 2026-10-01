# Evaluation datasets

These JSONL files are the versioned inputs for CommerceMind's offline experiments. They contain synthetic customer-service utterances and policy questions, not production conversations.

## Files

| File | Cases | Purpose |
|---|---:|---|
| `benchmark_v1.jsonl` | 160 | Unified normalized benchmark across intent, routing, RAG, and deterministic policy safety |
| `external_banking77_v1.jsonl` | 120 | Frozen external cross-domain intent test sampled from Banking77's official test split |
| `intent_cases.jsonl` | 30 | Balanced, direct intent-recognition baseline |
| `intent_robustness_cases.jsonl` | 20 | Colloquial, implicit, negated, and multi-question utterances |
| `orchestration_cases.jsonl` | 20 | Single-domain and multi-domain Agent-routing decisions |
| `intent_holdout_v1.jsonl` | 20 | Frozen intent challenge set; do not tune rules against it |
| `orchestration_holdout_v1.jsonl` | 12 | Frozen routing challenge set; do not tune rules against it |
| `rag_cases.jsonl` | 22 | Retrieval, domain filtering, and abstention |

## Label status

- Labels were authored for this repository and manually reviewed once by the project owner.
- The data is synthetic and has not been double-annotated by independent reviewers.
- Results on these files demonstrate regression behavior only; they do not estimate production accuracy.
- Case IDs must remain stable. Add new cases instead of silently changing labels after observing errors.
- Files named `holdout_v1` were added after the initial rules and must remain frozen. Any future tuning must be evaluated on a new `holdout_v2`, not by editing v1 labels.
- `benchmark_v1.jsonl` normalizes the existing 124 intent/routing/RAG cases and adds 36 deterministic tool/transaction-policy cases. Rebuild it with `scripts/build_benchmark_v1.py`; do not hand-edit generated rows.
- `external_banking77_v1.jsonl` contains original English Banking77 queries under CC BY 4.0. It is a banking-domain transfer test, not e-commerce production traffic. See `BANKING77_ATTRIBUTION.md` and its manifest.
- The external set is frozen: do not tune prompts, patterns, weights, or thresholds against its errors.

## Leakage controls

- Experiment code may use the training-like examples embedded in the recognizer. Therefore reports must disclose that this is an in-repository regression set, not a blind test set.
- A future external test set should be labeled before model evaluation and kept separate from prompt examples and threshold tuning.
- Raw model outputs are written to the ignored `outputs/` directory because they may contain prompts or provider metadata.

## Reproduction

```bash
.runtime-venv/bin/python scripts/run_ablation.py
.runtime-venv/bin/python scripts/run_ablation.py --dataset data/eval/intent_robustness_cases.jsonl --name intent_robustness
.runtime-venv/bin/python scripts/run_ablation.py --dataset data/eval/intent_holdout_v1.jsonl --name intent_holdout_v1
.runtime-venv/bin/python scripts/run_rag_ablation.py
.runtime-venv/bin/python scripts/run_orchestration_ablation.py
.runtime-venv/bin/python scripts/run_orchestration_ablation.py --dataset data/eval/orchestration_holdout_v1.jsonl --name orchestration_holdout_v1
.runtime-venv/bin/python scripts/run_task_evaluation.py
.runtime-venv/bin/python scripts/run_unified_benchmark.py
# Adds real LLM calls for the 70 intent cases:
.runtime-venv/bin/python scripts/run_unified_benchmark.py --live-llm
```

Pass `--live-llm` only with a newly issued local API key. Never commit `.env` or generated outputs.
