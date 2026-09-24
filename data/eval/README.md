# Evaluation datasets

These JSONL files are the versioned inputs for CommerceMind's offline experiments. They contain synthetic customer-service utterances and policy questions, not production conversations.

## Files

| File | Cases | Purpose |
|---|---:|---|
| `intent_cases.jsonl` | 30 | Balanced, direct intent-recognition baseline |
| `intent_robustness_cases.jsonl` | 20 | Colloquial, implicit, negated, and multi-question utterances |
| `orchestration_cases.jsonl` | 20 | Single-domain and multi-domain Agent-routing decisions |
| `rag_cases.jsonl` | 22 | Retrieval, domain filtering, and abstention |

## Label status

- Labels were authored for this repository and manually reviewed once by the project owner.
- The data is synthetic and has not been double-annotated by independent reviewers.
- Results on these files demonstrate regression behavior only; they do not estimate production accuracy.
- Case IDs must remain stable. Add new cases instead of silently changing labels after observing errors.

## Leakage controls

- Experiment code may use the training-like examples embedded in the recognizer. Therefore reports must disclose that this is an in-repository regression set, not a blind test set.
- A future external test set should be labeled before model evaluation and kept separate from prompt examples and threshold tuning.
- Raw model outputs are written to the ignored `outputs/` directory because they may contain prompts or provider metadata.

## Reproduction

```bash
.runtime-venv/bin/python scripts/run_ablation.py
.runtime-venv/bin/python scripts/run_ablation.py --dataset data/eval/intent_robustness_cases.jsonl --name intent_robustness
.runtime-venv/bin/python scripts/run_rag_ablation.py
.runtime-venv/bin/python scripts/run_orchestration_ablation.py
.runtime-venv/bin/python scripts/run_task_evaluation.py
```

Pass `--live-llm` only with a newly issued local API key. Never commit `.env` or generated outputs.
