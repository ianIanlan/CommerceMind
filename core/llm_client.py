"""Provider-neutral LLM client factory."""
import os
from typing import Any, Optional

from anthropic import AsyncAnthropic

from core.usage_tracker import record_response_usage


class _MeteredMessages:
    def __init__(self, messages: Any):
        self._messages = messages

    async def create(self, **kwargs: Any) -> Any:
        response = await self._messages.create(**kwargs)
        record_response_usage(response)
        return response


class _MeteredClient:
    def __init__(self, client: Any):
        self._client = client
        self.messages = _MeteredMessages(client.messages)


def build_llm_client(api_key: str, base_url: Optional[str] = None) -> Any:
    if os.getenv("LLM_API_FORMAT", "anthropic").lower() == "responses":
        if not base_url:
            raise ValueError("Responses API 需要配置 LLM_BASE_URL/ANTHROPIC_BASE_URL")
        from core.responses_client import ResponsesClient
        return _MeteredClient(ResponsesClient(api_key=api_key, base_url=base_url))
    kwargs = {"api_key": api_key}
    if base_url:
        kwargs["base_url"] = base_url
    return _MeteredClient(AsyncAnthropic(**kwargs))
