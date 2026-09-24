"""Provider-neutral LLM client factory."""
import os
from typing import Any, Optional

from anthropic import AsyncAnthropic


def build_llm_client(api_key: str, base_url: Optional[str] = None) -> Any:
    if os.getenv("LLM_API_FORMAT", "anthropic").lower() == "responses":
        if not base_url:
            raise ValueError("Responses API 需要配置 LLM_BASE_URL/ANTHROPIC_BASE_URL")
        from core.responses_client import ResponsesClient
        return ResponsesClient(api_key=api_key, base_url=base_url)
    kwargs = {"api_key": api_key}
    if base_url:
        kwargs["base_url"] = base_url
    return AsyncAnthropic(**kwargs)
