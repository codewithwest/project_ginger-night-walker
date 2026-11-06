"""Task-scoped A2A message contracts and an in-process coordination bus.

The bus transports proposals and evidence references; it does not grant tools or permissions.
"""
from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from nightwalker.contracts import Capability


class A2AMessageType(str, Enum):
    ASSIGNMENT = "assignment"
    RESULT = "result"
    ASSISTANCE_REQUEST = "assistance_request"
    HANDOFF = "handoff"
    BLOCKER = "blocker"
    STATUS = "status"


class A2AMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message_id: str = Field(default_factory=lambda: str(uuid4()))
    correlation_id: str = Field(default_factory=lambda: str(uuid4()))
    parent_task_id: str
    subtask_id: str
    sender_agent_id: str
    recipient_agent_id: str
    message_type: A2AMessageType
    capability: Capability
    objective: str = Field(min_length=1, max_length=1000)
    payload: dict[str, Any] = Field(default_factory=dict)
    evidence_refs: list[str] = Field(default_factory=list, max_length=50)
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())


class A2ABus:
    """Minimal in-process A2A transport; replace with durable broker for multi-worker use."""

    def __init__(self) -> None:
        self._messages: dict[str, list[A2AMessage]] = {}

    def send(self, message: A2AMessage) -> A2AMessage:
        self._messages.setdefault(message.parent_task_id, []).append(message)
        return message

    def history(self, task_id: str) -> list[dict[str, Any]]:
        return [message.model_dump(mode="json") for message in self._messages.get(task_id, [])]

    def restore(self, task_id: str, messages: list[dict[str, Any]]) -> None:
        self._messages[task_id] = [A2AMessage.model_validate(item) for item in messages]
