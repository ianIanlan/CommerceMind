# CommerceMind 统一评测报告 v1

- 数据集：160 条；SHA-256：`291b67df77a40544224462dae6ded73aa1c8987f5426db5e9bca3fd1d363e8cc`
- 四个任务使用不同指标，不计算会掩盖问题的跨任务总分。

| 评测域 | 用例 | 通过 | 主指标 | 结果 |
|---|---:|---:|---|---:|
| intent_production_fusion | 70 | 65 | accuracy | 92.86% |
| conditional_multi_agent_routing | 32 | 28 | exact_match | 87.50% |
| rag_expanded_filtered | 22 | 18 | case_success_rate | 81.82% |
| tool_and_transaction_policy | 36 | 36 | pass_rate | 100.00% |

## 指标细节

- 意图生产融合：Macro-F1 90.04%，P95 6668.914 ms。
- 编排：Micro-F1 95.83%，领域召回 92.00%，多余 Agent 率 0.00%。
- RAG：Hit@1 73.68%，Hit@3 78.95%，MRR 0.763，拒答准确率 100.00%。

## 证据边界

- 数据均为仓库内合成数据，不含生产用户对话。
- development/robustness 用例参与过规则迭代；holdout 用例被冻结，但尚未双人独立标注。
- 意图结果来自实时 LLM + Embedding + Pattern 生产融合；受当前第三方模型版本与网络状态影响。
- Policy 用例验证确定性权限和状态机断言，不评价自然语言回答质量。

## 错误样本

- `intent_production_fusion` {'case_id': 'intent::crash_02', 'message': '页面报 500 错误', 'expected': 'technical_crash', 'predicted': 'technical', 'confidence': 0.8075, 'sources': {'llm': {'intent': 'technical', 'confidence': 0.95}, 'embedding': {'intent': 'technical_crash', 'confidence': 0.6261}, 'pattern': {'intent': 'technical_crash', 'confidence': 0.5}}}
- `intent_production_fusion` {'case_id': 'intent::robust_order_01', 'message': 'ORD-10001 到底出库没', 'expected': 'order_status', 'predicted': 'damaged_item', 'confidence': 0.1725, 'sources': {'llm': {'intent': 'other', 'confidence': 0.0}, 'embedding': {'intent': 'damaged_item', 'confidence': 0.1725}, 'pattern': {'intent': 'other', 'confidence': 0.0}}}
- `intent_production_fusion` {'case_id': 'intent::robust_damage_01', 'message': '箱子里少了个配件，不是我要退款，是想补发', 'expected': 'damaged_item', 'predicted': 'refund', 'confidence': 0.5, 'sources': {'llm': {'intent': 'other', 'confidence': 0.0}, 'embedding': {'intent': 'refund', 'confidence': 0.3331}, 'pattern': {'intent': 'refund', 'confidence': 0.5}}}
- `intent_production_fusion` {'case_id': 'intent::robust_damage_02', 'message': '发来的颜色不对，不是物流慢的问题', 'expected': 'damaged_item', 'predicted': 'logistics', 'confidence': 0.5, 'sources': {'llm': {'intent': 'other', 'confidence': 0.0}, 'embedding': {'intent': 'billing', 'confidence': 0.2981}, 'pattern': {'intent': 'logistics', 'confidence': 0.5}}}
- `intent_production_fusion` {'case_id': 'intent::holdout_refund_01', 'message': '收到以后不太喜欢，想把货退回去拿回钱', 'expected': 'refund', 'predicted': 'return_exchange', 'confidence': 0.8075, 'sources': {'llm': {'intent': 'return_exchange', 'confidence': 0.95}, 'embedding': {'intent': 'technical_login', 'confidence': 0.1254}, 'pattern': {'intent': 'other', 'confidence': 0.0}}}
- `conditional_multi_agent_routing` {'case_id': 'routing::holdout-multi-logistics-charge', 'expected': ['billing', 'order'], 'predicted': ['order']}
- `conditional_multi_agent_routing` {'case_id': 'routing::holdout-multi-crash-address', 'expected': ['order', 'technical'], 'predicted': ['order']}
- `conditional_multi_agent_routing` {'case_id': 'routing::holdout-multi-payment-login', 'expected': ['billing', 'technical'], 'predicted': ['billing']}
- `conditional_multi_agent_routing` {'case_id': 'routing::holdout-multi-cancel-refund', 'expected': ['after_sales', 'order'], 'predicted': ['order']}
- `rag_expanded_filtered` {'case_id': 'rag::refund-01', 'query': '普通商品签收以后几天能退', 'expected': 'refund-general', 'retrieved': []}
- `rag_expanded_filtered` {'case_id': 'rag::security-03', 'query': '客服会问我要验证码吗', 'expected': 'account-security', 'retrieved': []}
- `rag_expanded_filtered` {'case_id': 'rag::member-02', 'query': '一百积分能抵多少钱', 'expected': 'membership', 'retrieved': []}
- `rag_expanded_filtered` {'case_id': 'rag::member-03', 'query': '生日月份积分会翻倍吗', 'expected': 'membership', 'retrieved': []}
