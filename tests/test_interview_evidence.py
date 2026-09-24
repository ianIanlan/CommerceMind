from scripts.run_interview_evidence import EvidenceRunner


def test_evidence_checks_require_agent_union_and_tool_use():
    payload = {
        "intent": "technical_login",
        "primary_agent": "technical",
        "supporting_agents": ["billing"],
        "tools_used": ["get_payment_records"],
        "escalated": False,
    }

    failures = EvidenceRunner._require(
        payload,
        intents={"technical_login"},
        required_agents={"technical", "billing"},
        any_tools={"get_payment_records"},
        escalated=False,
    )

    assert failures == []


def test_evidence_checks_report_missing_behavior():
    failures = EvidenceRunner._require(
        {"intent": "other", "primary_agent": "general", "supporting_agents": [], "tools_used": []},
        intents={"logistics"},
        primary="order",
        any_tools={"get_logistics"},
    )

    assert len(failures) == 3
