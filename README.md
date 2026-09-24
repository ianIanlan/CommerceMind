# CommerceMind：可观测的电商客服 Multi-Agent 系统

CommerceMind 是一个面向电商售后的客服 Agent 系统。它不仅生成话术，还把订单、物流、
支付和退款事实接入受控工具，并为高风险写操作增加预检、确认、幂等和审计。

当前已经落地：

- 可复现的商品、订单、支付、物流和退款演示数据
- 订单、售后、账单领域 Agent 与代码层工具白名单
- 退款资格预检、待确认动作、幂等执行和审计记录
- 未授权订单访问隔离和高风险回复审核

智能编排与工程能力包括：

- 细粒度意图识别
- 路由驱动的多 Agent 编排
- 意图驱动 RAG 检索
- Redis + ChromaDB 分层记忆
- 动态 Skills 注入
- 在线监控与路由降权
- LLM-as-Judge 端到端评测

详细设计基线见 [CommerceMind 设计文档](docs/commerce_agent_design.md)，当前完成度、边界和后续优先级见
[项目审视与演进路线](docs/project_review_and_roadmap.md)。

## 你可以先看什么

- [系统设计](docs/commerce_agent_design.md)
- [项目完成度与路线](docs/project_review_and_roadmap.md)
- [业务流程说明](wiki/业务流程说明.md)
- [完整使用指南](wiki/完整使用指南.md)

## 快速开始

### 1. 准备环境

- Docker
- Docker Compose
- `ANTHROPIC_API_KEY`

如果使用兼容 Anthropic 协议的第三方模型服务，也可以配置：

```env
ANTHROPIC_BASE_URL=https://api.deepseek.com/anthropic
ANTHROPIC_MODEL=deepseek-v4-pro
ANTHROPIC_API_KEY=your_key
```

如果网关使用 OpenAI Responses API：

```env
ANTHROPIC_BASE_URL=https://your-gateway.example
ANTHROPIC_MODEL=your-model
ANTHROPIC_API_KEY=your_key
LLM_API_FORMAT=responses
DISABLE_LLM=false
```

Responses 适配器使用 SSE 流式请求，并支持函数调用事件与工具结果回传。变量名暂时保留
`ANTHROPIC_*` 是为了兼容原配置，后续可统一迁移为 `LLM_*`。

### 2. 配置环境变量

复制示例配置：

```bash
cp .env.example .env
```

最少确认这些变量可用：

```env
ANTHROPIC_API_KEY=your_api_key
REDIS_PASSWORD=commercemind123
```

### 3. 启动服务

推荐直接启动全栈：

```bash
docker compose up -d --build
```

完整的首次安装、验收和数据保留说明见 [Docker 快速启动](docs/docker_quickstart.md)。Compose
会同时启动 Vue 前端、FastAPI、PostgreSQL、Redis、ChromaDB 和 Prometheus。

查看状态：

```bash
docker compose ps
```

看日志：

```bash
docker compose logs -f commercemind
```

### 无 Docker 的本地演示模式

本模式使用进程内短期记忆、轻量知识检索和 SQLite 业务库，适合快速体验；完整环境仍使用
Redis + ChromaDB。首次准备：

```bash
python3 -m venv .runtime-venv
.runtime-venv/bin/pip install -r requirements-local.txt
```

启动后端：

```bash
./scripts/run_local.sh
```

另开终端启动前端：

```bash
cd ../CommerceMindFrontend
npm install
npm run dev
```

访问 `http://localhost:5173`，默认演示用户为 `demo-user`。可直接测试
`ORD-10002` 物流、`ORD-10005` 重复扣款和 `ORD-10003` 退款确认。演示模式不依赖有效
LLM Key；设置 `DISABLE_LLM=false` 后即可在本地基础设施模式下使用完整三路识别和模型工具调用。

### 4. 访问入口

- API: `http://localhost:8000`
- Swagger: `http://localhost:8000/docs`
- Nginx: `http://localhost`
- Health: `http://localhost:8000/health`

## 核心功能

### 对话主链路

`POST /chat`

流程是：

```text
读取记忆 -> 意图识别 -> 知识检索 -> Agent 路由 -> 回复生成 -> 写回记忆
```

### 知识库

- `POST /search`
- `POST /knowledge/add`
- `POST /knowledge/upload`
- `GET /knowledge/stats`

### 电商业务动作

- `GET /commerce/orders/{order_id}?user_id=demo-user`
- `GET /commerce/orders/{order_id}/payments?user_id=demo-user`
- `POST /commerce/refunds/prepare`
- `POST /actions/{action_id}/confirm`

退款采用两阶段流程：`prepare` 只返回待确认动作，`confirm` 才创建退款申请；重复确认不会
重复创建退款。

可选的 Stripe Test Mode 会把确认后的退款发送到真实支付沙箱，并使用动作 ID 保证渠道侧幂等；
运行方法与证据要求见 [Stripe Test Mode 集成验证](docs/stripe_test_integration.md)。

取消订单和修改地址也使用同一套 `prepare -> confirm` 状态机：

- `POST /commerce/orders/cancel/prepare`
- `POST /commerce/orders/address/prepare`
- `POST /actions/{action_id}/confirm`
- `GET /commerce/handoffs?user_id=...`

生产身份模式设置 `AUTH_MODE=signed_token` 和 `AUTH_SECRET`。此时 API 忽略 body/query 中的
`user_id`，只使用 HMAC Bearer Token 的 `sub`。本地前端演示继续使用 `AUTH_MODE=demo`。

设置 `DATABASE_URL` 后电商事务仓库自动切换到 PostgreSQL；未设置时使用 SQLite 演示库。

### Skills

- `GET /skills`
- `POST /skills/reload`

### 监控与评测

- `GET /monitor`
- `POST /eval/run`

意图消融实验：

```bash
# 不调用外部模型：关键词、Embedding、本地双路
.runtime-venv/bin/python scripts/run_ablation.py

# 完整实时对照：包含 LLM 和生产融合
.runtime-venv/bin/python scripts/run_ablation.py --live-llm
```

实验结论见 [意图识别消融实验结论](docs/intent_ablation_findings.md)。

RAG 检索消融实验：

```bash
.runtime-venv/bin/python scripts/run_rag_ablation.py
```

报告同时给出 Hit@1、Hit@3、MRR、应拒答准确率、错域率和延迟，避免只凭回答文本主观判断检索质量。
实验结论见 [RAG 消融实验结论](docs/rag_ablation_findings.md)。

## 项目结构

```text
api/main.py                  FastAPI 入口
agents/agent_orchestrator.py 多 Agent 编排
agents/tools.py              角色工具白名单
commerce/models.py           电商领域数据契约
commerce/store.py            SQLite 演示业务库与审计
commerce/service.py          订单/支付/退款规则和动作状态机
commerce/guard.py            高风险回复确定性审核
core/intent_recognizer.py    三路融合意图识别
core/skill_loader.py         动态 Skills 加载
memory/conversation_memory.py  Redis + ChromaDB 记忆
mcp/tool_manager.py          工具层、缓存、熔断、重排
mcp/knowledge_base.py        ChromaDB 知识库
monitor/performance_monitor.py 在线监控
evaluation/evaluator.py      端到端评测
wiki/                       详细文档
skills/                     动态业务规则
data/                       持久化数据
```

## 运行时架构

```text
用户请求
  -> /chat
  -> MemoryManager 读取工作记忆、情景记忆、用户画像
  -> IntentRecognizer 输出 intent / intent_group / urgency / entities
  -> 按意图决定是否检索知识库
  -> AgentOrchestrator 路由到 Order / AfterSales / Billing / Escalation
  -> Skills 注入、工具调用、回复生成
  -> 写回 Redis 和 ChromaDB
  -> Monitor 采集在线指标
  -> Evaluator 做意图识别和回复质量评测
```

## 主要端口

| 服务 | 端口 |
|---|---:|
| CommerceMind API | 8000 |
| ChromaDB | 8001 |
| Redis | 6379 |
| Prometheus | 9090 |
| Nginx | 80 |

## 开发和调试

常用顺序：

```text
1. /health
2. /chat
3. /skills
4. /monitor
5. /eval/run
```

如果你只想看项目怎么工作，直接读：

- [CommerceMind 定位与技术亮点](wiki/CommerceMind定位与技术亮点.md)
- [系统设计](docs/commerce_agent_design.md)
- [项目完成度与路线](docs/project_review_and_roadmap.md)

## 一句话概括

CommerceMind 是一个可查询、可确认、可审计、可评测的电商售后 Agent。
