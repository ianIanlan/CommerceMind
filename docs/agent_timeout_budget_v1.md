# CommerceMind Agent 时间预算与确定性降级 v1

## 背景

必需工具策略实验出现过 56 秒和 111 秒的远程模型长尾。本地数据库工具通常只有毫秒级，真正风险
是领域 Agent 没有总时间预算：一个模型调用长时间不返回，会拖住完整客服请求；失败后再调用
GeneralAgent 还可能继续放大延迟。

## 设计

每个领域 Agent 使用 `COMMERCEMIND_AGENT_TIMEOUT_S` 总预算，Docker 默认20秒。预算覆盖该
Agent 的完整模型工具循环，而不只是单次HTTP请求。

超时后系统：

1. 取消尚未完成的模型协程；
2. 不再调用 GeneralAgent 重试模型；
3. 保留代码策略已取得的只读工具事实与 Trace；
4. 根据支付流水、错误码等事实生成保守的确定性回答；
5. 在 API 和 Trace 中返回 `degraded_agents` 与 `agent_timeout` 原因；
6. 不执行退款、取消订单或修改地址等写操作。

## 故障注入验证

将预算强制设置为1秒，对“登录401 + 订单重复扣款”请求运行一次预热和一次测量。测量结果：

| 检查 | 结果 |
|---|---:|
| Billing 与 Technical 均标记降级 | PASS |
| 保留 `get_payment_records` 与 `lookup_error_code` | PASS |
| Agent 并行墙钟低于2秒 | PASS |
| 返回非空、保守的事实回答 | PASS |
| Agent并行墙钟 | 1.023 s |
| 服务端总耗时（热意图缓存） | 1.045 s |
| Composer | 0.1 ms |

实验同时发现并修复两项缺口：

- API模型已声明 `degraded_agents`，但返回对象最初忘记赋值；
- `报401` 中中文与数字之间不存在正则 `\b` 词边界，导致错误码实体漏提取，TechnicalAgent无法
  预取错误码事实。现改为数字负向前后界，并增加回归测试。

## 正确解释

1秒是故障注入值，不是推荐生产配置。它用于稳定触发降级并证明预算生效。项目默认20秒，需要用
真实P95与业务SLA继续调整。

本次约1.045秒总耗时命中了意图缓存，因此不能解释为所有新请求都能在1秒内返回。冷意图识别仍
可能调用远程模型；后续需要给意图识别设置独立预算。

确定性降级的目标不是生成最优语言，而是满足：有事实、不虚构、不执行高风险动作、在预算内返回。

## 复现

```bash
COMMERCEMIND_AGENT_TIMEOUT_S=1 docker compose up -d --build --force-recreate commercemind
.runtime-venv/bin/python scripts/run_timeout_budget_experiment.py
```

实验后恢复默认：

```bash
docker compose up -d --force-recreate commercemind
```
