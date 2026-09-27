"""Request-scoped LLM usage accounting.

The tracker deliberately reports unknown values as unavailable instead of
estimating tokens from character counts.  Context variables keep concurrent
FastAPI requests isolated while allowing child Agent tasks to contribute to
the same request total.
"""
from __future__ import annotations

import contextvars
import os
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Iterator


@dataclass
class UsageAccumulator:
    calls: int = 0
    calls_with_usage: int = 0
    input_tokens: int = 0
    output_tokens: int = 0

    def as_dict(self) -> dict[str, Any]:
        input_price = float(os.getenv("LLM_INPUT_USD_PER_MILLION", "0") or 0)
        output_price = float(os.getenv("LLM_OUTPUT_USD_PER_MILLION", "0") or 0)
        usage_complete = self.calls > 0 and self.calls_with_usage == self.calls
        cost_available = usage_complete and (input_price > 0 or output_price > 0)
        estimated_cost = (
            (self.input_tokens * input_price + self.output_tokens * output_price) / 1_000_000
            if cost_available else None
        )
        return {
            "calls": self.calls,
            "calls_with_usage": self.calls_with_usage,
            "usage_complete": usage_complete,
            "input_tokens": self.input_tokens if self.calls_with_usage else None,
            "output_tokens": self.output_tokens if self.calls_with_usage else None,
            "cost_usd": round(estimated_cost, 8) if estimated_cost is not None else None,
            "pricing_configured": input_price > 0 or output_price > 0,
        }


_CURRENT: contextvars.ContextVar[UsageAccumulator | None] = contextvars.ContextVar(
    "commercemind_llm_usage", default=None
)


@contextmanager
def usage_scope() -> Iterator[UsageAccumulator]:
    accumulator = UsageAccumulator()
    token = _CURRENT.set(accumulator)
    try:
        yield accumulator
    finally:
        _CURRENT.reset(token)


def record_response_usage(response: Any) -> None:
    accumulator = _CURRENT.get()
    if accumulator is None:
        return
    accumulator.calls += 1
    usage = getattr(response, "usage", None)
    if usage is None:
        return
    if isinstance(usage, dict):
        input_tokens = usage.get("input_tokens", usage.get("prompt_tokens"))
        output_tokens = usage.get("output_tokens", usage.get("completion_tokens"))
    else:
        input_tokens = getattr(usage, "input_tokens", getattr(usage, "prompt_tokens", None))
        output_tokens = getattr(usage, "output_tokens", getattr(usage, "completion_tokens", None))
    if input_tokens is None or output_tokens is None:
        return
    accumulator.calls_with_usage += 1
    accumulator.input_tokens += int(input_tokens)
    accumulator.output_tokens += int(output_tokens)

