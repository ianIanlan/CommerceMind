# CommerceMind 外部冻结评测：Banking77 v1

## 数据来源

- 上游：PolyAI Banking77 官方 `test` split；不是本项目编写或生成的表达。
- 固定提交：`57ec275d8078af65b7731c2a98be812d844a6d6b`。
- 原始 CSV SHA-256：`d12d6e3bc4c3103966ae786dc435913c0c563dfa328f5a3646d0e62cfeeb474d`。
- 授权：CC BY 4.0；引用 Casanueva et al., NLP4ConvAI 2020。
- 抽样：8 个源类别各 15 条，固定随机种子 20261001，共 120 条；不翻译、不改写。

## 结果

当前部署关闭了效果较差的字符 Embedding，因此主结果采用与运行配置一致的 `LLM + Pattern`。

| 方案 | Accuracy (95% CI) | Macro-F1 | P50/P95 | 错误 |
|---|---:|---:|---:|---:|
| pattern_only | 11.67% [7.08%, 18.63%] | 6.20% | 0.1/0.1 ms | 106 |
| embedding_only | 1.67% [0.46%, 5.87%] | 0.59% | 3.9/4.6 ms | 118 |
| llm_only | 75.83% [67.45%, 82.61%] | 48.05% | 2278.6/6655.4 ms | 29 |
| llm_pattern | 75.83% [67.45%, 82.61%] | 40.98% | 2278.7/6655.5 ms | 29 |

LLM 共发起 133 次请求；10.83% 的样本发生重试，LLM 失败率为 5.00%。

## 主要错误

- 11 条：`account_security` → `payment_issue`。
- 7 条：`technical_login` → `account`。
- 2 条：`refund` → `order_cancel`。
- 2 条：`refund_status` → `refund`。
- 1 条：`refund` → `return_exchange`。
- 1 条：`account_security` → `query`。
- 1 条：`account_security` → `greeting`。
- 1 条：`technical_login` → `query`。
- 1 条：`logistics` → `request`。
- 1 条：`logistics` → `query`。
- 1 条：`logistics` → `other`。

按 Banking77 源类别统计：

- `card_payment_not_recognised`：11/15 错误。
- `passcode_forgotten`：8/15 错误。
- `request_refund`：3/15 错误。
- `card_delivery_estimate`：3/15 错误。
- `Refund_not_showing_up`：2/15 错误。
- `compromised_card`：2/15 错误。

## 结论

- 外部 Accuracy 75.83%，比内部合成意图集 92.86% 低 17.03 个百分点，证明内部结果存在明显乐观偏差。
- LLM 能识别全部 15 条重复扣款和全部 15 条支付失败表达，但在相邻意图边界上明显失分。
- `card_payment_not_recognised` 多数被判为 `payment_issue`，说明当前 `account_security` 与支付异常的边界定义不充分。
- `passcode_forgotten` 经常退化为宽泛 `account`，说明细粒度登录意图的跨语言稳定性不足。
- Pattern 与字符 Embedding 几乎不能处理英文数据；它们是中文本地兜底，不具备跨语言保证。

## 防泄漏约束

该数据集从本次起冻结，不用于修改 Prompt、关键词、模板、权重或阈值。后续优化必须在单独的开发数据上完成，并只在新的外部 `v2` 上做一次最终验证。

## 证据边界

- Banking77 是项目外部公开客服查询，但官方论文没有将其声明为原始生产聊天日志；它属于英文银行领域，不等于中文电商生产流量。
- 源标签到 CommerceMind 标签的映射由项目方完成，尚未经过第二位标注者独立复核。
- `card_payment_not_recognised` 等类别天然跨越支付与安全领域，映射误差会影响最终分数。
- 本实验只评估单轮意图，不评价工具调用、回复质量或交易成功率。

## 复现

```bash
python scripts/build_external_banking77.py /path/to/task-specific-datasets/banking_data/test.csv
.runtime-venv/bin/python scripts/run_ablation.py --live-llm \
  --dataset data/eval/external_banking77_v1.jsonl --name external_banking77_v1
.runtime-venv/bin/python scripts/summarize_external_benchmark.py
```

来源：[Banking77 数据卡](https://huggingface.co/datasets/PolyAI/banking77) · [官方数据仓库](https://github.com/PolyAI-LDN/task-specific-datasets) · [原论文](https://aclanthology.org/2020.nlp4convai-1.5/)
