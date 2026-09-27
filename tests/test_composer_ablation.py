from scripts.run_composer_ablation import percentile, process_assertions, repetition_ratio, summarize


def test_composer_ablation_metrics_are_deterministic():
    assert percentile([100, 200, 300, 400], 0.5) == 250
    assert repetition_ratio("第一行\n第二行\n第一行") == 0.3333


def test_process_assertions_require_both_domains_and_tools():
    payload = {
        "primary_agent": "billing",
        "supporting_agents": ["technical"],
        "tools_used": ["get_payment_records", "lookup_error_code"],
        "response": "已核验支付记录并发现两笔交易。下面提供登录错误的排查步骤，请依次尝试并保留完整错误信息，仍失败时再联系人工客服。",
        "safety_violations": [],
    }

    assert all(process_assertions(payload).values())


def test_summary_reports_latency_and_process_success():
    assertions = {
        "both_domains": True, "billing_evidence": True, "technical_evidence": True,
        "nonempty_response": True, "no_internal_leak": True, "no_safety_violation": True,
    }
    samples = [
        {"server_total_ms": 100, "composer_ms": 1, "agents_parallel_wall_ms": 70,
         "response_chars": 100, "repetition_ratio": 0.0, "assertions": assertions},
        {"server_total_ms": 200, "composer_ms": 2, "agents_parallel_wall_ms": 80,
         "response_chars": 120, "repetition_ratio": 0.1, "assertions": assertions},
    ]

    result = summarize("deterministic", samples)

    assert result["process_success_rate"] == 1.0
    assert result["latency_ms"]["total_p50"] == 150.0
    assert result["response"]["mean_repetition_ratio"] == 0.05
