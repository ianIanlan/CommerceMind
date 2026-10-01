#!/usr/bin/env python3
"""Create the concise, versioned external benchmark report from ablation JSON."""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
RESULT = ROOT / "outputs/external_banking77_v1_live.json"
MANIFEST = ROOT / "data/eval/external_banking77_v1.manifest.json"
OUTPUT = ROOT / "docs/external_banking77_v1.md"


def main() -> None:
    report = json.loads(RESULT.read_text(encoding="utf-8"))
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    variants = {item["name"]: item for item in report["variants"]}
    deployed = variants["llm_pattern"]
    error_pairs = Counter((item["expected"], item["predicted"]) for item in deployed["errors"])
    error_sources = Counter(item["case_id"].split("::")[1] for item in deployed["errors"])
    lines = [
        "# CommerceMind 外部冻结评测：Banking77 v1", "",
        "## 数据来源", "",
        "- 上游：PolyAI Banking77 官方 `test` split；不是本项目编写或生成的表达。",
        f"- 固定提交：`{manifest['source_commit']}`。",
        f"- 原始 CSV SHA-256：`{manifest['source_csv_sha256']}`。",
        f"- 授权：{manifest['license']}；引用 Casanueva et al., NLP4ConvAI 2020。",
        "- 抽样：8 个源类别各 15 条，固定随机种子 20261001，共 120 条；不翻译、不改写。",
        "", "## 结果", "",
        "当前部署关闭了效果较差的字符 Embedding，因此主结果采用与运行配置一致的 `LLM + Pattern`。", "",
        "| 方案 | Accuracy (95% CI) | Macro-F1 | P50/P95 | 错误 |", "|---|---:|---:|---:|---:|",
    ]
    for name in ("pattern_only", "embedding_only", "llm_only", "llm_pattern"):
        item = variants[name]
        lines.append(
            f"| {name} | {item['accuracy']:.2%} [{item['accuracy_ci95'][0]:.2%}, {item['accuracy_ci95'][1]:.2%}] | "
            f"{item['macro_f1']:.2%} | {item['p50_latency_ms']:.1f}/{item['p95_latency_ms']:.1f} ms | {len(item['errors'])} |"
        )
    lines += [
        "",
        f"LLM 共发起 {report['llm_request_count']} 次请求；{report['llm_retry_rate']:.2%} 的样本发生重试，"
        f"LLM 失败率为 {report['source_metrics']['llm']['failure_rate']:.2%}。",
        "", "## 主要错误", "",
    ]
    for (expected, predicted), count in error_pairs.most_common():
        lines.append(f"- {count} 条：`{expected}` → `{predicted}`。")
    lines += ["", "按 Banking77 源类别统计：", ""]
    for source, count in error_sources.most_common():
        lines.append(f"- `{source}`：{count}/15 错误。")
    lines += [
        "", "## 结论", "",
        "- 外部 Accuracy 75.83%，比内部合成意图集 92.86% 低 17.03 个百分点，证明内部结果存在明显乐观偏差。",
        "- LLM 能识别全部 15 条重复扣款和全部 15 条支付失败表达，但在相邻意图边界上明显失分。",
        "- `card_payment_not_recognised` 多数被判为 `payment_issue`，说明当前 `account_security` 与支付异常的边界定义不充分。",
        "- `passcode_forgotten` 经常退化为宽泛 `account`，说明细粒度登录意图的跨语言稳定性不足。",
        "- Pattern 与字符 Embedding 几乎不能处理英文数据；它们是中文本地兜底，不具备跨语言保证。",
        "", "## 防泄漏约束", "",
        "该数据集从本次起冻结，不用于修改 Prompt、关键词、模板、权重或阈值。后续优化必须在单独的开发数据上完成，并只在新的外部 `v2` 上做一次最终验证。",
        "", "## 证据边界", "",
        "- Banking77 是项目外部公开客服查询，但官方论文没有将其声明为原始生产聊天日志；它属于英文银行领域，不等于中文电商生产流量。",
        "- 源标签到 CommerceMind 标签的映射由项目方完成，尚未经过第二位标注者独立复核。",
        "- `card_payment_not_recognised` 等类别天然跨越支付与安全领域，映射误差会影响最终分数。",
        "- 本实验只评估单轮意图，不评价工具调用、回复质量或交易成功率。",
        "", "## 复现", "",
        "```bash",
        "python scripts/build_external_banking77.py /path/to/task-specific-datasets/banking_data/test.csv",
        ".runtime-venv/bin/python scripts/run_ablation.py --live-llm \\",
        "  --dataset data/eval/external_banking77_v1.jsonl --name external_banking77_v1",
        ".runtime-venv/bin/python scripts/summarize_external_benchmark.py",
        "```", "",
        "来源：[Banking77 数据卡](https://huggingface.co/datasets/PolyAI/banking77) · "
        "[官方数据仓库](https://github.com/PolyAI-LDN/task-specific-datasets) · "
        "[原论文](https://aclanthology.org/2020.nlp4convai-1.5/)",
    ]
    OUTPUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(OUTPUT)


if __name__ == "__main__":
    main()
