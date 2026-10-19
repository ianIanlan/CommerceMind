# Evaluation datasets

These JSONL files are the versioned inputs for CommerceMind's experiments. Internal files are synthetic regression cases; the AmazonHelp file contains redacted public customer-support utterances with project-authored silver labels.

## Files

| File | Cases | Purpose |
|---|---:|---|
| `benchmark_v1.jsonl` | 160 | Unified normalized benchmark across intent, routing, RAG, and deterministic policy safety |
| `external_amazonhelp_v1.jsonl` | 55 | Redacted public AmazonHelp diagnostic set under CC BY-NC-SA 4.0 |
| `external_amazonhelp_holdout_v1.jsonl` | 25 | Zero-overlap AmazonHelp holdout frozen after diagnostic optimization |
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
- `external_amazonhelp_v1.jsonl` comes from public AmazonHelp conversations, not synthetic prompts or private production tickets.
- Identifiers, long digit sequences, emails, phone-like values, and links are redacted before model calls.
- Its intent labels were selected with high-precision candidate rules and audited once against full dialogues. They are silver labels, not source-provided or independently double-annotated gold labels.
- The same v1 set was used to diagnose and measure the optimization, so its before/after result is not an untouched holdout estimate.
- `external_amazonhelp_holdout_v1.jsonl` was frozen after optimization and run once. Do not tune on its two errors; create a new version for future final evaluation.
- The external artifact is separately licensed under CC BY-NC-SA 4.0; see `AMAZONHELP_ATTRIBUTION.md` and `DATA_LICENSES.md`.

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
# AmazonHelp external diagnostic:
.runtime-venv/bin/python scripts/run_ablation.py --live-llm \
  --dataset data/eval/external_amazonhelp_v1.jsonl --name external_amazonhelp_v1
.runtime-venv/bin/python scripts/run_ablation.py --live-llm \
  --dataset data/eval/external_amazonhelp_holdout_v1.jsonl --name external_amazonhelp_holdout_v1
```

Pass `--live-llm` only with a newly issued local API key. Never commit `.env` or generated outputs.
