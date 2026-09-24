"""OpenAI Responses API streaming adapter with an Anthropic-like surface.

The project was originally written against ``client.messages.create``.  Some
compatible gateways only return complete text/function calls in Responses SSE
events, so this adapter translates the existing message/tool contract without
coupling the domain code to one provider SDK.
"""
from __future__ import annotations

import json
import uuid
from types import SimpleNamespace
from typing import Any, Dict, Iterable, List, Optional

import httpx


class ResponsesClient:
    def __init__(self, api_key: str, base_url: str, timeout: float = 90.0):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.messages = _Messages(self)


class _Messages:
    def __init__(self, client: ResponsesClient):
        self._client = client

    async def create(
        self,
        *,
        model: str,
        messages: List[Dict[str, Any]],
        max_tokens: int = 1024,
        system: str = "",
        tools: Optional[List[Dict[str, Any]]] = None,
        **_: Any,
    ) -> Any:
        payload: Dict[str, Any] = {
            "model": model,
            "input": self._convert_messages(messages),
            "max_output_tokens": max_tokens,
            "stream": True,
        }
        if system:
            payload["instructions"] = system
        if tools:
            payload["tools"] = [
                {
                    "type": "function",
                    "name": tool["name"],
                    "description": tool.get("description", ""),
                    "parameters": tool.get("input_schema") or {"type": "object", "properties": {}},
                }
                for tool in tools
            ]
            payload["tool_choice"] = "auto"

        headers = {
            "Authorization": f"Bearer {self._client.api_key}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        }
        content: List[Any] = []
        text_parts: List[str] = []
        calls: Dict[str, Dict[str, str]] = {}

        async with httpx.AsyncClient(timeout=self._client.timeout) as client:
            async with client.stream(
                "POST",
                f"{self._client.base_url}/v1/responses",
                headers=headers,
                json=payload,
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    raw = line[6:]
                    if raw == "[DONE]":
                        break
                    try:
                        event = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    event_type = event.get("type")
                    if event_type == "response.output_text.delta":
                        text_parts.append(str(event.get("delta", "")))
                    elif event_type == "response.output_item.added":
                        item = event.get("item") or {}
                        if item.get("type") == "function_call":
                            call_id = str(item.get("call_id") or item.get("id") or uuid.uuid4().hex)
                            calls[call_id] = {
                                "id": call_id,
                                "name": str(item.get("name", "")),
                                "arguments": str(item.get("arguments", "")),
                            }
                    elif event_type == "response.function_call_arguments.delta":
                        call_id = str(event.get("call_id") or "")
                        calls.setdefault(call_id, {"id": call_id, "name": str(event.get("name", "")), "arguments": ""})
                        calls[call_id]["arguments"] += str(event.get("delta", ""))
                    elif event_type == "response.function_call_arguments.done":
                        call_id = str(event.get("call_id") or "")
                        calls.setdefault(call_id, {"id": call_id, "name": str(event.get("name", "")), "arguments": ""})
                        calls[call_id]["name"] = str(event.get("name") or calls[call_id]["name"])
                        calls[call_id]["arguments"] = str(event.get("arguments") or calls[call_id]["arguments"])

        text = "".join(text_parts).strip()
        if text:
            content.append(SimpleNamespace(type="text", text=text))
        for call in calls.values():
            try:
                arguments = json.loads(call["arguments"] or "{}")
            except json.JSONDecodeError:
                arguments = {}
            content.append(SimpleNamespace(
                type="tool_use",
                id=call["id"],
                name=call["name"],
                input=arguments,
            ))
        if not content:
            raise RuntimeError("Responses API 未返回文本或工具调用")
        return SimpleNamespace(content=content)

    @staticmethod
    def _convert_messages(messages: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
        converted: List[Dict[str, Any]] = []
        for message in messages:
            role = str(message.get("role", "user"))
            value = message.get("content", "")
            if isinstance(value, str):
                converted.append({"role": role, "content": value})
                continue
            if not isinstance(value, list):
                converted.append({"role": role, "content": str(value)})
                continue
            for block in value:
                block_type = block.get("type") if isinstance(block, dict) else getattr(block, "type", "")
                if block_type == "text":
                    text = block.get("text", "") if isinstance(block, dict) else getattr(block, "text", "")
                    if text:
                        converted.append({"role": role, "content": text})
                elif block_type == "tool_use":
                    name = block.get("name", "") if isinstance(block, dict) else getattr(block, "name", "")
                    call_id = block.get("id", "") if isinstance(block, dict) else getattr(block, "id", "")
                    arguments = block.get("input", {}) if isinstance(block, dict) else getattr(block, "input", {})
                    converted.append({
                        "type": "function_call",
                        "call_id": call_id,
                        "name": name,
                        "arguments": json.dumps(arguments, ensure_ascii=False),
                    })
                elif block_type == "tool_result":
                    call_id = block.get("tool_use_id", "") if isinstance(block, dict) else getattr(block, "tool_use_id", "")
                    output = block.get("content", "") if isinstance(block, dict) else getattr(block, "content", "")
                    converted.append({
                        "type": "function_call_output",
                        "call_id": call_id,
                        "output": output if isinstance(output, str) else json.dumps(output, ensure_ascii=False),
                    })
        return converted
