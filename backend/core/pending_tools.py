"""
Store pending interactive tool calls waiting for user input.
"""

from copy import deepcopy
from typing import Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, ProgrammingError

from db.models import PendingToolCall
from db.session import SessionLocal

_pending_tools_fallback: dict[str, dict] = {}


def _is_missing_pending_table_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return "pending_tool_calls" in text and ("does not exist" in text or "undefinedtable" in text)


def _set_fallback(conversation_id: str, tool_call_id: str, tool_name: str, messages_snapshot: list[dict], input_request: dict | None, message_count: int | None = None):
    _pending_tools_fallback[conversation_id] = {
        "tool_call_id": tool_call_id,
        "tool_name": tool_name,
        "messages": deepcopy(messages_snapshot),
        "input_request": deepcopy(input_request or {}),
        "message_count": message_count,
    }


def _parse_conversation_uuid(conversation_id: str) -> UUID | None:
    try:
        return UUID(conversation_id)
    except ValueError:
        return None


async def set_pending_tool(
    conversation_id: str,
    tool_call_id: str,
    tool_name: str,
    messages_snapshot: list[dict],
    input_request: dict | None = None,
    message_count: int | None = None,
):
    conv_uuid = _parse_conversation_uuid(conversation_id)
    if not conv_uuid:
        _set_fallback(conversation_id, tool_call_id, tool_name, messages_snapshot, input_request, message_count)
        return

    payload_messages = deepcopy(messages_snapshot)
    payload_input = deepcopy(input_request or {})
    if message_count is not None:
        payload_input["_message_count"] = message_count

    try:
        async with SessionLocal() as db:
            row = await db.scalar(select(PendingToolCall).where(PendingToolCall.conversation_id == conv_uuid))
            if row:
                row.tool_call_id = tool_call_id
                row.tool_name = tool_name
                row.messages_snapshot = payload_messages
                row.input_request = payload_input
                await db.commit()
                return

            db.add(
                PendingToolCall(
                    conversation_id=conv_uuid,
                    tool_call_id=tool_call_id,
                    tool_name=tool_name,
                    messages_snapshot=payload_messages,
                    input_request=payload_input,
                )
            )
            try:
                await db.commit()
            except IntegrityError:
                await db.rollback()
                existing = await db.scalar(select(PendingToolCall).where(PendingToolCall.conversation_id == conv_uuid))
                if not existing:
                    return
                existing.tool_call_id = tool_call_id
                existing.tool_name = tool_name
                existing.messages_snapshot = payload_messages
                existing.input_request = payload_input
                await db.commit()
    except ProgrammingError as exc:
        if _is_missing_pending_table_error(exc):
            _set_fallback(conversation_id, tool_call_id, tool_name, messages_snapshot, input_request)
            return
        raise


async def get_pending_tool(conversation_id: str) -> Optional[dict]:
    conv_uuid = _parse_conversation_uuid(conversation_id)
    if not conv_uuid:
        fallback = _pending_tools_fallback.get(conversation_id)
        return deepcopy(fallback) if fallback else None
    try:
        async with SessionLocal() as db:
            row = await db.scalar(select(PendingToolCall).where(PendingToolCall.conversation_id == conv_uuid))
            if not row:
                fallback = _pending_tools_fallback.get(conversation_id)
                return deepcopy(fallback) if fallback else None
            input_req = deepcopy(row.input_request or {})
            stored_count = input_req.pop("_message_count", None)
            return {
                "tool_call_id": row.tool_call_id,
                "tool_name": row.tool_name,
                "messages": deepcopy(row.messages_snapshot or []),
                "input_request": input_req,
                "message_count": stored_count,
            }
    except ProgrammingError as exc:
        if _is_missing_pending_table_error(exc):
            fallback = _pending_tools_fallback.get(conversation_id)
            return deepcopy(fallback) if fallback else None
        raise


async def clear_pending_tool(conversation_id: str):
    _pending_tools_fallback.pop(conversation_id, None)
    conv_uuid = _parse_conversation_uuid(conversation_id)
    if not conv_uuid:
        return
    try:
        async with SessionLocal() as db:
            row = await db.scalar(select(PendingToolCall).where(PendingToolCall.conversation_id == conv_uuid))
            if not row:
                return
            await db.delete(row)
            await db.commit()
    except ProgrammingError as exc:
        if _is_missing_pending_table_error(exc):
            return
        raise
