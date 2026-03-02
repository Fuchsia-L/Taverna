"""
Store pending interactive tool calls waiting for user input.
"""

from copy import deepcopy
from typing import Optional

_pending_tools: dict[str, dict] = {}


def set_pending_tool(conversation_id: str, tool_call_id: str, tool_name: str, messages_snapshot: list[dict]):
    _pending_tools[conversation_id] = {
        "tool_call_id": tool_call_id,
        "tool_name": tool_name,
        "messages": deepcopy(messages_snapshot),
    }


def get_pending_tool(conversation_id: str) -> Optional[dict]:
    return _pending_tools.get(conversation_id)


def clear_pending_tool(conversation_id: str):
    _pending_tools.pop(conversation_id, None)
