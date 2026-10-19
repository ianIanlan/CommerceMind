# 语义 Embedding 与选择性成本路由实验

CommerceMind 将字符 n-gram 基线与真正的语义 Embedding 明确区分。实验报告会记录实际后端名称；远端模型失败默认计为失败，不会静默使用字符向量并继续标记为“Embedding”。

项目支持本地 FastEmbed/ONNX。本轮最终实验使用多语言模型 `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`，以同时覆盖中文项目用例与真实英文 AmazonHelp 语料。FastEmbed 是可选依赖，不进入默认 API 镜像。

模型信息来源：[FastEmbed 官方支持模型列表](https://qdrant.github.io/fastembed/examples/Supported_Models/)。

## 接口要求

服务应支持 OpenAI-compatible：

```text
POST /v1/embeddings
Authorization: Bearer <key>
```

请求使用 `model`、批量 `input` 和 `encoding_format=float`。客户端会批量生成模板向量、进行 L2 归一化并缓存重复文本。

## 配置

### 本地多语言模型

```bash
.runtime-venv/bin/python -m pip install -r requirements-embeddings.txt
.runtime-venv/bin/python scripts/run_ablation.py \
  --embedding-backend fastembed \
  --embedding-model sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2 \
  --dataset data/eval/intent_holdout_v1.jsonl \
  --name fastembed_zh_holdout_v1
```

首次运行会下载模型；后续使用本地缓存。报告后端应显示 `semantic_local_fastembed`。

### 远程兼容接口

在本地 `.env` 配置：

```env
EMBEDDING_BASE_URL=https://your-provider.example
EMBEDDING_API_KEY=your-new-key
EMBEDDING_MODEL=sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2
INTENT_EMBEDDING_ENABLED=true
INTENT_EMBEDDING_FAST_PATH_ENABLED=false
EMBEDDING_FALLBACK_LOCAL=false
```

不要复用已经泄露的模型 Key，也不要把 `.env` 提交到 Git。

## 预注册运行方式

先运行开发集确认接口正确，再运行冻结集一次：

```bash
.runtime-venv/bin/python scripts/run_ablation.py --dataset data/eval/intent_cases.jsonl --name semantic_embedding_dev
.runtime-venv/bin/python scripts/run_ablation.py --dataset data/eval/intent_holdout_v1.jsonl --name semantic_embedding_holdout_v1
```

报告必须满足：

- `Embedding 后端` 与实际方案一致：本地为 `semantic_local_fastembed`，远程为 `semantic_remote`；
- `failure_rate` 单独报告，不能删除失败样本；
- 保留冻结集 SHA-256；
- 不依据 holdout v1 错误继续改规则或标签；
- 下一轮优化使用 holdout v2。

## 比较指标

比较字符基线和语义模型的 Accuracy、Macro-F1、95% CI、P50/P95 延迟和失败率。只有语义模型在新 holdout 上稳定优于字符基线，才考虑在生产融合中打开 `INTENT_EMBEDDING_ENABLED`。

## AmazonHelp 选择性路由结果

固定策略是：Embedding 的 top-1 相似度和 top1-top2 margin 同时达到阈值时跳过 LLM，否则继续调用 LLM。阈值搜索只使用55条诊断集，并要求被路由样本精度不低于95%；25条验证集不参与阈值选择。

| 数据 | 样本 | 路由数 | 覆盖率/预计 LLM 调用下降 | 路由精度 |
|---|---:|---:|---:|---:|
| AmazonHelp diagnostic | 55 | 8 | 14.55% | 100% |
| AmazonHelp frozen validation | 25 | 1 | 4.00% | 100% |

校准阈值为 similarity `0.72`、margin `0.04`。验证集没有错误路由，但只有1条命中，样本不足以证明稳定降本。因此 `INTENT_EMBEDDING_FAST_PATH_ENABLED` 保持默认关闭；这项实验支持“技术可行、当前收益不足”的结论，而不是生产收益声明。

复现实验：

```bash
.runtime-venv/bin/python scripts/run_embedding_cost_router.py \
  --calibration data/eval/external_amazonhelp_v1.jsonl \
  --validation data/eval/external_amazonhelp_holdout_v1.jsonl \
  --output outputs/embedding_cost_router_amazonhelp.json
```
