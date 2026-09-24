import asyncio
import json

import pytest

from core.responses_client import ResponsesClient, _Messages


def sse(event):
    return "data: " + json.dumps(event, ensure_ascii=False)


class FakeResponse:
    def __init__(self, lines):
        self.lines = lines

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    def raise_for_status(self):
        return None

    async def aiter_lines(self):
        for line in self.lines:
            yield line


class FakeHttpClient:
    lines = []
    last_request = None

    def __init__(self, **_):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    def stream(self, method, url, **kwargs):
        type(self).last_request = {"method": method, "url": url, **kwargs}
        return FakeResponse(type(self).lines)


def test_streaming_response_collects_text_and_function_call(monkeypatch):
    FakeHttpClient.lines = [
        sse({"type": "response.output_text.delta", "delta": "正在查询"}),
        sse({
            "type": "response.output_item.added",
            "item": {"type": "function_call", "call_id": "call_1", "name": "get_order", "arguments": ""},
        }),
        sse({"type": "response.function_call_arguments.delta", "call_id": "call_1", "name": "get_order", "delta": '{"order_id":"ORD-10002"}'}),
        sse({"type": "response.function_call_arguments.done", "call_id": "call_1", "name": "get_order", "arguments": '{"order_id":"ORD-10002"}'}),
        "data: [DONE]",
    ]
    monkeypatch.setattr("core.responses_client.httpx.AsyncClient", FakeHttpClient)
    client = ResponsesClient("test-key", "https://gateway.example")

    response = asyncio.run(client.messages.create(
        model="deepseek-flash",
        max_tokens=200,
        messages=[{"role": "user", "content": "查询订单"}],
        tools=[{
            "name": "get_order",
            "description": "查询订单",
            "input_schema": {"type": "object", "properties": {"order_id": {"type": "string"}}},
        }],
    ))

    assert response.content[0].text == "正在查询"
    assert response.content[1].type == "tool_use"
    assert response.content[1].name == "get_order"
    assert response.content[1].input == {"order_id": "ORD-10002"}
    assert FakeHttpClient.last_request["json"]["stream"] is True
    assert FakeHttpClient.last_request["json"]["tools"][0]["type"] == "function"


def test_tool_result_round_trip_is_converted_to_responses_items():
    tool_use = type("ToolUse", (), {
        "type": "tool_use",
        "id": "call_1",
        "name": "get_order",
        "input": {"order_id": "ORD-10002"},
    })()
    converted = _Messages._convert_messages([
        {"role": "assistant", "content": [tool_use]},
        {"role": "user", "content": [{
            "type": "tool_result",
            "tool_use_id": "call_1",
            "content": '{"success":true}',
        }]},
    ])

    assert converted[0] == {
        "type": "function_call",
        "call_id": "call_1",
        "name": "get_order",
        "arguments": '{"order_id": "ORD-10002"}',
    }
    assert converted[1] == {
        "type": "function_call_output",
        "call_id": "call_1",
        "output": '{"success":true}',
    }


def test_empty_stream_is_treated_as_provider_failure(monkeypatch):
    FakeHttpClient.lines = ["data: [DONE]"]
    monkeypatch.setattr("core.responses_client.httpx.AsyncClient", FakeHttpClient)
    client = ResponsesClient("test-key", "https://gateway.example")

    with pytest.raises(RuntimeError, match="未返回文本或工具调用"):
        asyncio.run(client.messages.create(
            model="deepseek-flash",
            messages=[{"role": "user", "content": "hello"}],
        ))
