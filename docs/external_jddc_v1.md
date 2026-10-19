# CommerceMind 外部评测：JDDC v1

## 为什么选择 JDDC

JDDC 来自京东真实中文电商客服场景，覆盖任务型对话、问答和闲聊，并提供意图信息。它与 CommerceMind 的中文售后定位在语言、领域和多轮结构上都比银行数据更匹配。

## 当前状态

**等待官方授权数据导入，尚无 JDDC 实验结果。**

JDDC 2.1 官方要求填写申请表，并使用机构邮箱发送给数据提供方。仓库不会从来源不明的镜像复制数据，也不会将原始对话提交到 GitHub。

## 评测协议

1. 从获批数据中划分开发集和冻结测试集，按会话划分，避免同一会话跨集合泄漏。
2. 先查看官方意图定义并完成 `JDDC intent → CommerceMind intent` 映射；无法可靠映射的类别标为 `other` 或排除，并记录原因。
3. 两名标注者独立抽检映射样本，报告一致率及分歧处理方式。
4. 在第一次运行模型之前保存数据 SHA-256、源版本、筛选规则、类别分布和映射文件哈希。
5. 开发集可用于调参，冻结测试集只运行最终候选版本，不根据其错误修改系统。
6. 报告 Accuracy、Macro-F1、各类别召回率、95% 置信区间、延迟和完整错误分布。

## 导入格式

导入器接受 CSV/TSV，默认字段为：

- `conversation_id`：会话标识；
- `query`：用户原始话语；
- `intent`：JDDC 源意图；
- `split`：数据划分，默认仅导入 `test`。

如果获批文件字段不同，可通过命令行参数指定，不修改原数据：

```bash
python scripts/import_jddc.py /authorized/path/jddc.tsv \
  --mapping data/eval/jddc_intent_map.json \
  --delimiter tab \
  --conversation-field session_id \
  --message-field query \
  --intent-field intent \
  --split-field split \
  --split test

python scripts/run_ablation.py --live-llm \
  --dataset data/eval/external_jddc_v1.jsonl \
  --name external_jddc_v1
```

生成的 JDDC JSONL 已被 `.gitignore` 排除；只应提交脱敏后的聚合报告和不含原文的 manifest。
