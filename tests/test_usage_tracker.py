from types import SimpleNamespace

from core.usage_tracker import record_response_usage, usage_scope


def test_usage_tracker_is_request_scoped_and_counts_tokens(monkeypatch):
    monkeypatch.setenv("LLM_INPUT_USD_PER_MILLION", "1")
    monkeypatch.setenv("LLM_OUTPUT_USD_PER_MILLION", "2")
    with usage_scope() as usage:
        record_response_usage(SimpleNamespace(usage={"input_tokens": 1000, "output_tokens": 500}))
        result = usage.as_dict()
    assert result["calls"] == 1
    assert result["usage_complete"] is True
    assert result["input_tokens"] == 1000
    assert result["output_tokens"] == 500
    assert result["cost_usd"] == 0.002


def test_usage_tracker_does_not_invent_missing_usage():
    with usage_scope() as usage:
        record_response_usage(SimpleNamespace(usage=None))
        result = usage.as_dict()
    assert result["calls"] == 1
    assert result["usage_complete"] is False
    assert result["input_tokens"] is None
    assert result["cost_usd"] is None
