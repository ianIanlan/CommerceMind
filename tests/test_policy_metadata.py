from datetime import datetime, timezone


def test_expired_policy_is_inactive():
    from mcp.knowledge_base import KnowledgeBase

    assert KnowledgeBase._metadata_is_active(
        {"status": "active", "expires_at": "2026-01-01T00:00:00+00:00"},
        now=datetime(2026, 9, 21, tzinfo=timezone.utc),
    ) is False


def test_future_policy_is_not_yet_active():
    from mcp.knowledge_base import KnowledgeBase

    assert KnowledgeBase._metadata_is_active(
        {"status": "active", "effective_at": "2027-01-01T00:00:00+00:00"},
        now=datetime(2026, 9, 21, tzinfo=timezone.utc),
    ) is False


def test_current_policy_is_active():
    from mcp.knowledge_base import KnowledgeBase

    assert KnowledgeBase._metadata_is_active(
        {
            "status": "active",
            "effective_at": "2026-01-01T00:00:00+00:00",
            "expires_at": "2027-01-01T00:00:00+00:00",
        },
        now=datetime(2026, 9, 21, tzinfo=timezone.utc),
    ) is True
