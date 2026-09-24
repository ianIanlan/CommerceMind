# CommerceMind 端到端面试证据 v1

## 目的

这组测试不是用单元测试替代真实系统，而是通过运行中的 Docker 全栈调用公开 API，证明
“意图识别 → 路由 → Agent → 工具 → 业务状态机 → 安全约束”能够连成完整链路。

测试日期：2026-09-24  
运行环境：Docker Compose（API、前端、PostgreSQL、Redis、ChromaDB、Prometheus）  
生成模型：`deepseek-flash`（Responses 兼容接口）

## 固定场景结果

| 场景 | 验证目标 | 结果 | 单次端到端延迟 |
|---|---|---:|---:|
| 物流查询 | `logistics → order`，调用订单与物流工具 | PASS | 8.72 s |
| 重复扣款 | `duplicate_payment → billing`，调用支付记录工具 | PASS | 11.20 s |
| 登录失败且重复扣款 | billing 主 Agent + technical 辅助 Agent | PASS | 25.78 s |
| 转人工 | escalation Agent 创建工单并返回升级标志 | PASS | 1.98 s |
| 越权订单查询 | 其他用户订单必须返回 HTTP 404 | PASS | 9.0 ms |
| 退款状态机 | prepare/confirm 成功，重复确认保持同一退款 ID | PASS | 37.9 ms |

总计：**6/6 通过**。

原始的逐请求结构化证据由脚本写入 `outputs/interview_evidence.json` 和
`outputs/interview_evidence.md`。该目录不提交 Git，避免把运行时请求 ID、业务 ID 或潜在敏感
信息放进公开仓库。

## 能证明什么

- LLM 不只是生成自然语言：路由结果、Agent 选择和工具调用都能从 API 结构化字段中核验。
- 多领域请求确实同时进入 billing 与 technical 两个角色，而不是前端拼出的演示效果。
- 数据所有权校验和退款幂等由确定性代码执行，不依赖模型“自觉遵守”。
- 高风险写操作经过 `prepare → confirm`，重复确认不会重复创建退款。

## 暴露的问题

- 多领域场景耗时 **25.78 秒**，单领域 LLM 场景也需要 8–11 秒。功能正确，但交互性能还不适合
  生产环境。下一步应分别记录意图识别、各 Agent 推理、工具调用和汇总阶段耗时，再减少串行
  LLM 轮次、限制工具循环，并验证真正的并行执行。
- 转人工场景触发了 `SENSITIVE_DATA_REQUEST` 回复护栏。升级本身成功，但需要用标注集核查这是
  合理拦截还是误报，不能仅凭一次结果调整规则。

## 复现

先启动完整服务，再运行：

```bash
docker compose up -d --build
.runtime-venv/bin/python scripts/run_interview_evidence.py --base-url http://localhost:8000
```

脚本任一断言失败都会返回非零退出码，因此可以接入 CI。

## 证据边界

- 这是 6 个固定场景的一次真实集成运行，不是负载测试，也不能给出统计显著性结论。
- 当前使用演示订单数据；尚未验证生产身份系统、真实物流渠道和真实支付渠道。
- Stripe Test Mode 适配器已实现，但本次环境没有配置 `STRIPE_SECRET_KEY`，因此不能声称已完成
  Stripe 沙箱实测。
- 意图准确率应结合独立冻结集报告，不应由这 6 个场景推导；参见
  [融合冻结实验 v1](live_fusion_experiment_v1.md)。
