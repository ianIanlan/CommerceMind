# CommerceMind AmazonHelp 外部诊断实验 v1

## 数据与边界

- 55 条公开 AmazonHelp 客服对话中的真实用户首轮表达，不是项目合成 Prompt。
- 六类：`refund_status` 10、`order_cancel` 10、`return_exchange` 10、`payment_issue` 10、`logistics` 10、`human_handoff` 5。
- 原始对话来自 Customer Support on Twitter 的 AmazonHelp 子集；使用 ConvoSense 的清洗版本。
- 写入评测集和发送模型前，移除订单类连续数字、邮箱、电话类数字和链接。
- 标签由高精度规则筛选候选，再依据完整对话做一次审阅；属于 silver label，不是官方或双人 gold label。
- 数据采用 CC BY-NC-SA 4.0，不能将数据资产用于商业用途。

## 严格结果

数据 SHA-256：`7ccdfee8d38355ff3061df15a6c6c2afdaf0d141182ad3edf447b7e462f29ea1`

| 指标 | 优化前 | 优化后 |
|---|---:|---:|
| Production fusion Accuracy | 45.45% | **89.09%** |
| Accuracy 95% CI | 33.03%–58.48% | **78.17%–94.90%** |
| Production fusion Macro-F1 | 0.2404 | **0.5578** |
| Pattern-only Accuracy | 10.91% | **70.91%** |
| LLM-only Accuracy | 45.45% | **87.27%** |
| LLM failure rate | 23.64% | **3.64%** |
| Retry sample rate | 38.18% | **7.27%** |
| Production P50 | 3,689 ms | **2,812 ms** |
| Production P95 | 7,882 ms | 9,779 ms |

优化包含三项：

1. 给 LLM 增加中英双语细粒度意图定义，明确区分 `refund` 与 `refund_status`，并要求业务诉求优先于情绪。
2. 增加英文售后高信息量模式，用作确定性证据和模型失败兜底。
3. 将无效 JSON 的第二次请求改为严格单行 JSON 契约，并提高输出预算，降低截断和协议失败。

P95 变差是输出预算和少量重试带来的尾延迟代价，未隐藏该退化。当前部署仍应设置总时间预算和降级路径。

## 剩余六个严格错误

错误集中在多意图边界：取消后追问退款、退货同时要求退款、重复扣款与一般支付异常、要求回电同时修改地址，以及物流问题使用纯投诉表达。这里保留单标签严格计分，没有把“语义上也合理”的预测改成正确答案。

## 零重叠 holdout

诊断优化完成后，从其余来源对话中另外冻结25条、5类均衡的 holdout，与55条诊断集没有 source ID 重叠，并只运行一次。

- 数据 SHA-256：`53f21f5c6549e691b1549542a55eed0efafe61892edc584154ae3bf50e793c96`
- Production fusion Accuracy：**92.00%**（23/25）
- 95% CI：**75.03%–97.78%**
- Macro-F1：**0.6786**
- LLM failure rate：**4.00%**
- 重试样本比例：**8.00%**
- P50/P95：**2,823 / 7,713 ms**

两个严格错误都是多意图竞争：订单被取消的同时分别出现账户锁定和人工升级信号。该 holdout 不再用于调参；后续优化必须另建新版本验证。

## 解释限制

55条结果是同一诊断集上的优化前后对比，优化过程中已经看到 v1 错误，因此 **89.09% 不是未见数据泛化准确率**。25条 holdout 提供了未参与优化的补充证据，但样本仍小且只有一次标签审阅，因此 **92.00% 也不等同于线上生产准确率**。更强结论需要扩大 holdout，并由两名标注者独立标注。

此外，Macro-F1 按期望标签与所有预测标签的并集计算；模型预测到数据集中没有作为金标出现的合理细分类别时也会受到惩罚，因此不能脱离标签分布单独解读。

## 复现

```bash
python scripts/build_external_amazonhelp.py \
  '/path/to/ConvoSense/annotator_packages_overlap/annotator_*/batch_*/data.jsonl'

.runtime-venv/bin/python scripts/run_ablation.py --live-llm \
  --dataset data/eval/external_amazonhelp_v1.jsonl \
  --name external_amazonhelp_v1

.runtime-venv/bin/python scripts/run_ablation.py --live-llm \
  --dataset data/eval/external_amazonhelp_holdout_v1.jsonl \
  --name external_amazonhelp_holdout_v1
```
