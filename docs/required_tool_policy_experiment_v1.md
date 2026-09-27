# CommerceMind 必需工具策略实验 v1

## 问题

在 Composer 对照中，BillingAgent 曾有一次没有查询支付流水便直接回答。Prompt 中要求“先查事实”
仍然是软约束；对于已有订单号的重复扣款、物流等请求，关键事实不应该依赖模型随机选择工具。

## 实现

新增代码级 `required tool policy`：

- `duplicate_payment/payment_issue + order_id`：BillingAgent 预取 `get_payment_records`；
- `refund_status + order_id`：BillingAgent 预取 `get_refund_status`；
- `logistics + order_id`：OrderAgent 预取 `get_order` 与 `get_logistics`；
- `order_status + order_id`：OrderAgent 预取 `get_order`；
- `refund/return_exchange + order_id`：AfterSalesAgent 预取 `check_return_eligibility`；
- TechnicalAgent 有明确错误码时预取 `lookup_error_code`。

策略只允许只读工具，绝不预取 `prepare_refund_request`、取消订单、修改地址等写操作。缺少实体时不
猜测参数。工具结果作为已校验事实注入 Agent，Trace 标记 `policy_required=true`。

## 实验设置

- 日期：2026-09-27；模型：`deepseek-flash`；确定性 Composer。
- 固定复合输入与 Composer 实验相同。
- 关闭与开启策略各独立重启容器、运行10次。
- 主要指标：支付记录工具调用率；同时记录技术工具、流程断言和P50/P95。
- 同一输入首轮后会命中意图缓存，本实验不是冷启动端到端基准。

## 结果

| 指标 | 策略关闭 | 策略开启 |
|---|---:|---:|
| 支付记录工具证据 | 9/10 | 10/10 |
| 技术工具证据 | 10/10 | 9/10* |
| 双领域路由 | 10/10 | 10/10 |
| Guard 无介入 | 10/10 | 10/10 |
| 总延迟 P50 | 10.92 s | 12.00 s |
| 总延迟 P95 | 16.53 s | 86.38 s |
| 总延迟平均值 | 11.85 s | 25.84 s |

`*` 开启组第一次运行中，技术 Agent 在约111秒的远程模型长尾后失败并降级到 GeneralAgent。
错误码工具已在模型调用前由策略执行，但旧降级逻辑覆盖了失败 Agent 的 `tools_used` 与 Trace，导致
指标显示缺失。实验后已修复：模型失败和 General 降级都必须保留已完成的必需工具证据。

## 解释与决策

关闭策略的9/10再次证明模型工具选择不是稳定保证；开启后支付事实达到10/10，且这是由代码路径
保证，而不是依赖本轮抽样恰好命中。样本差异只有1条，不能做统计显著性主张。

延迟不能解释为策略使P95增加。必需工具是本地毫秒级只读调用，开启组的111秒和56秒来自远程
Agent模型长尾。常规样本中两组大多位于约10-13秒。下一轮应加入模型总预算与超时降级，而不是
关闭事实查询来换取表面延迟。

项目默认开启 `COMMERCEMIND_REQUIRED_TOOL_POLICY=true`。面试中应将其描述为：

> 模型决定如何解释和是否调用可选工具；代码策略保证完成任务所必需的只读事实。所有写操作仍由
> 状态机、用户确认和幂等控制，永远不会因“必需工具策略”自动执行。

## 当前边界

- 只重复一个复合请求，不能代表所有意图。
- 必需工具映射仍是显式策略表，需要随着业务契约进行版本管理。
- 预取后模型仍可能重复请求同一工具；后续应按工具名和规范化参数复用预取结果。
- Agent远程调用缺少严格总预算，是当前比工具查询更严重的可靠性问题。
