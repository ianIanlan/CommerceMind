# CommerceMind Composer 对照实验 v1

## 问题

复合请求已经并行执行 Billing 与 Technical Agent，但 LLM Composer 还会追加一次模型调用。
本实验比较：

- `deterministic`：保留主 Agent 输出，以“补充说明”追加辅助 Agent 输出；
- `llm`：再次调用模型去重并组织两个 Agent 的结果。

## 预注册设置

- 日期：2026-09-27；模型：`deepseek-flash`；Docker 全栈。
- 固定输入：`登录页面报401，而且订单 ORD-10005 被扣了两次款`。
- 每种模式独立重启容器后运行 10 次，每次使用新的会话 ID。
- 指标：双领域覆盖、账单/技术工具证据、内部推理泄漏、Guard 介入、回复长度、行重复率、
  服务端总延迟和 Composer 延迟。
- 原始回答包含运行时业务 ID，只保存在 Git 忽略的 `outputs/`。

## 原始结果

| 指标 | 确定性 Composer | LLM Composer |
|---|---:|---:|
| 运行次数 | 10 | 10 |
| 双领域覆盖 | 100% | 100% |
| 技术工具证据 | 100% | 100% |
| 账单工具证据 | 90% | 100% |
| 非空回答 | 100% | 100% |
| 无内部推理泄漏 | 100% | 100% |
| Guard 未介入（原始规则） | 70% | 60% |
| 总延迟 P50 | 11.44 s | 17.82 s |
| 总延迟 P95 | 63.11 s | 25.77 s |
| 总延迟平均值 | 20.78 s | 19.15 s |
| Composer P50 | 0.1 ms | 6.73 s |
| Composer P95 | 0.1 ms | 7.70 s |
| 平均回复字符数 | 2759 | 2777 |
| 平均重复行比例 | 5.65% | 4.76% |

## 结果解释

### 延迟

确定性方案的 P50 比 LLM 方案低约 35.8%，直接原因是省去约 6.7 秒的 Composer 模型调用。
但确定性组出现一次 98.48 秒的上游 Agent/网关长尾，使 P95 和平均值反而更差。这个异常值不能
删除，因为它说明远程模型长尾远大于本地合并开销；10 次样本也不足以稳定估计 P95。

同一输入在各组第一次运行后会命中意图缓存，因此本实验更接近“热意图路径下的 Agent +
Composer 对照”，不是冷启动端到端基准。两组采用相同方式，但总延迟不能外推到全新表达。

### 流程正确性

两组都能稳定选择 Billing + Technical。确定性组一次 BillingAgent 没有调用支付记录工具；这发生在
Composer 之前，是领域 Agent 的随机工具选择问题，不能归因于确定性合并。样本太小，90% 与
100% 不构成可靠差异。

### Guard 误报

检查原始结果后发现，Guard 把回答中的状态枚举“成功/失败/处理中/已退款”错误替换为
“尚未执行退款”。所以 70% 与 60% 不是有效的安全质量对比，而是确定性规则的误报率。实验后已
收紧规则：仅拦截“已经为您退款”“已完成退款”“退款已成功”等完成式承诺，并新增状态枚举回归
测试。没有修改或删除本次原始输出。

### 回答质量

LLM 合并的重复行比例少约 0.89 个百分点，但两组回复长度接近。字符数和重复行只能反映表面形式，
不能证明完整性、事实性或用户偏好。当前没有完成盲评，因此不能宣称确定性回答质量等同于 LLM。

## 决策

Docker 演示链路继续默认使用确定性 Composer，原因是：它保留两个 Agent 原始结果、没有内部推理
泄漏，并在本轮将中位总延迟降低约 6.38 秒。LLM Composer 保留为可配置对照模式。

这个决定不是“确定性方案质量更好”，而是当前没有证据证明约 6.7 秒的额外模型调用带来足够质量
收益。生产决策前仍需：

1. 使用多个复合问题，而不是重复一个输入；
2. 随机隐藏模式，进行人工双盲偏好与完整性评分；
3. 至少运行 30-50 次以估计 P95，并单独报告网关超时率；
4. 记录 token usage 和单请求成本；
5. 将“是否调用必要工具”作为 Agent 指标，而不是 Composer 指标。

## 复现

分别以两种环境变量启动服务，然后运行：

```bash
COMMERCEMIND_COMPOSER_MODE=deterministic docker compose up -d --force-recreate commercemind
.runtime-venv/bin/python scripts/run_composer_ablation.py --mode deterministic --repeats 10

COMMERCEMIND_COMPOSER_MODE=llm docker compose up -d --force-recreate commercemind
.runtime-venv/bin/python scripts/run_composer_ablation.py --mode llm --repeats 10
```
