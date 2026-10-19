# Evaluation datasets

These JSONL files are the versioned inputs for CommerceMind's offline experiments. They contain synthetic customer-service utterances and policy questions, not production conversations.

## Files

| File | Cases | Purpose |
|---|---:|---|
| `benchmark_v1.jsonl` | 160 | Unified normalized benchmark across intent, routing, RAG, and deterministic policy safety |
| `external_jddc_v1.jsonl` | 申请并导入后生成 | 冻结的 JDDC 中文真实电商客服评测集；数据文件不提交仓库 |
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
- 外部主评测采用 JDDC。JDDC 2.1 需向数据提供方申请，仓库不会用合成数据冒充授权语料。
- 获批后使用 `scripts/import_jddc.py` 从授权副本生成 `external_jddc_v1.jsonl`；映射必须在首次评测前冻结。
- JDDC 外部测试集只能用于最终评估，禁止根据测试错误调整 Prompt、关键词、权重或阈值。

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
# JDDC 获批、完成标签映射后：
.runtime-venv/bin/python scripts/run_ablation.py --live-llm \
  --dataset data/eval/external_jddc_v1.jsonl --name external_jddc_v1
```

Pass `--live-llm` only with a newly issued local API key. Never commit `.env` or generated outputs.
