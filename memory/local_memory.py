"""Local demo memory used when Redis/Chroma are not available.

This keeps the same small interface consumed by the API, so the complete chat
pipeline can be demonstrated on a laptop without infrastructure services.
It is intentionally process-local and must not be used as production storage.
"""
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional


class MsgRole(Enum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"


@dataclass
class Message:
    role: MsgRole
    content: str
    timestamp: datetime = field(default_factory=datetime.now)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class MemoryContext:
    recent_messages: List[Message]
    relevant_history: List[str] = field(default_factory=list)
    user_profile: Dict[str, Any] = field(default_factory=dict)
    summary: str = ""

    def to_prompt_text(self) -> str:
        if not self.recent_messages:
            return ""
        lines = ["[最近对话]"]
        lines.extend(
            f"{message.role.value}: {message.content}"
            for message in self.recent_messages[-8:]
        )
        return "\n".join(lines)


class LocalMemoryManager:
    """Bounded in-memory conversation history for local demonstrations."""

    def __init__(self, max_messages: int = 20):
        self.max_messages = max_messages
        self._messages: Dict[str, List[Message]] = defaultdict(list)

    @staticmethod
    def _key(user_id: str, conv_id: str) -> str:
        return f"{user_id}:{conv_id}"

    async def get_context(self, user_id: str, conv_id: str, query: str = "") -> MemoryContext:
        messages = list(self._messages[self._key(user_id, conv_id)])
        return MemoryContext(recent_messages=messages)

    async def add_message(
        self,
        user_id: str,
        conv_id: str,
        role: MsgRole,
        content: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        key = self._key(user_id, conv_id)
        self._messages[key].append(
            Message(role=MsgRole(role.value), content=content, metadata=metadata or {})
        )
        self._messages[key] = self._messages[key][-self.max_messages :]

    async def update_profile(self, user_id: str, conv_id: str) -> None:
        return None

    async def close(self) -> None:
        self._messages.clear()
