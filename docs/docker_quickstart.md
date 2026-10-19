# CommerceMind Docker 快速启动

## Docker 包含什么

默认 Compose 命令会启动五个后端容器：

- `commercemind`：FastAPI、多 Agent、工具调用和评测接口
- `postgres`：订单、动作、退款、审计和人工工单
- `redis`：短期对话记忆
- `chromadb`：知识库与长期记忆向量存储
- `prometheus`：运行指标

`frontend` 是可选的 `ui` profile。这样单独 clone 后端仓库也能启动，不要求本地必须存在另一个前端目录。

## macOS 首次准备

1. 安装并启动 Docker Desktop。
2. 在后端目录复制配置：`cp .env.example .env`。
3. 在 `.env` 中填写自己的模型网关、模型名称和 API Key。
4. 为数据库、Redis 和认证设置非默认密码。

不要把真实 `.env` 或 API Key 提交到 Git。

## 启动

在 `CommerceMind` 后端仓库根目录运行：

```bash
docker compose up -d --build
docker compose ps
```

首次默认构建需要下载基础镜像和 Python 依赖，耗时会比后续启动长；启用 `ui` profile 时还会安装前端 npm 依赖。
后端默认使用清华 PyPI 镜像加速构建；如需切换可在启动前设置
`PYTHON_PACKAGE_INDEX=https://pypi.org/simple`。

Docker 演示配置默认关闭 LLM 查询改写与逐轮画像提炼，以避免一次用户请求放大为过多模型调用。
需要实验这些能力时，可在 `.env.local` 设置：

```env
RAG_QUERY_REWRITE_ENABLED=true
PROFILE_AUTO_UPDATE=true
```

ChromaDB 中的知识库和长期记忆默认使用无需下载权重的本地字符 n-gram 向量。这保证离线启动；
它适合作为部署兜底，不应包装成训练型语义 Embedding 的等价替代。

默认访问：

- 后端健康检查：http://localhost:8000/health
- Swagger：http://localhost:8000/docs
- Prometheus：http://localhost:9090

## 可选 Vue 工作台

将前端项目放在后端同级的 `CommerceMindFrontend` 目录，或设置 `COMMERCEMIND_FRONTEND_CONTEXT`，然后运行：

```bash
docker compose --profile ui up -d --build
```

此时访问：

- 前端：http://localhost
- Nginx 代理健康检查：http://localhost/api/python/health

## 验收

```bash
./scripts/docker_smoke_test.sh
docker compose logs --tail=100 commercemind
```

脚本默认直接验证 `http://localhost:8000`。启用 UI profile 时可额外设置 `COMMERCE_UI_BASE_URL=http://localhost` 验证前端首页。

## 停止与数据

```bash
docker compose down
```

这会停止容器但保留 PostgreSQL、Redis、ChromaDB 和 Prometheus 的命名卷。只有明确想清空演示数据时才运行：

```bash
docker compose down -v
```

`-v` 会删除数据库数据，属于破坏性操作。
