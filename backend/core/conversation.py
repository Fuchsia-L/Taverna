"""
Conversation persistence backed by PostgreSQL.
Debug payload capture stays in-memory for developer tooling.
"""

from __future__ import annotations

from copy import deepcopy
from uuid import UUID, uuid4

from sqlalchemy import delete, func, select

from core.compressor import compress_messages
from core.token_utils import estimate_messages_tokens
from db.models import Conversation, ConversationStatus, ConversationType, Message, MessageRole
from db.session import SessionLocal

# In-memory debug metadata.
_debug_turns: dict[str, list[dict]] = {}

RECENT_WINDOW_TOKENS = 4000    # token budget for the recent messages sent to AI
MIN_RECENT_TURNS = 4           # floor: always keep at least this many user turns
COMPRESS_TOKEN_THRESHOLD = 4000  # compress only when old messages exceed this


def _normalize_conversation_id(conversation_id: str | None = None) -> str:
    raw = (conversation_id or "").strip()
    if not raw:
        return str(uuid4())
    try:
        return str(UUID(raw))
    except ValueError:
        return str(uuid4())


async def _ensure_conversation_exists(conversation_id: str | None = None) -> str:
    resolved = _normalize_conversation_id(conversation_id)
    conv_uuid = UUID(resolved)
    async with SessionLocal() as db:
        existing = await db.scalar(select(Conversation).where(Conversation.id == conv_uuid))
        if not existing:
            db.add(
                Conversation(
                    id=conv_uuid,
                    type=ConversationType.standalone,
                    status=ConversationStatus.active,
                    title="新会话",
                )
            )
            await db.commit()
    return resolved


async def ensure_conversation(conversation_id: str | None = None) -> str:
    """Ensure a conversation row exists and return normalized conversation_id."""
    return await _ensure_conversation_exists(conversation_id)


async def ensure_session(session_id: str | None = None) -> str:
    """Backward compatible alias."""
    return await _ensure_conversation_exists(session_id)


async def get_history(conversation_id: str = "default") -> list[dict]:
    resolved = await _ensure_conversation_exists(conversation_id)
    conv_uuid = UUID(resolved)
    async with SessionLocal() as db:
        rows = await db.scalars(
            select(Message)
            .where(Message.conversation_id == conv_uuid)
            .order_by(Message.created_at.asc(), Message.id.asc())
        )
        messages = list(rows)
    return [{"role": row.role.value, "content": row.content} for row in messages]


async def add_message(role: str, content: str | list, conversation_id: str = "default"):
    resolved = await _ensure_conversation_exists(conversation_id)
    conv_uuid = UUID(resolved)
    async with SessionLocal() as db:
        db.add(
            Message(
                conversation_id=conv_uuid,
                role=MessageRole(role),
                content=content,
            )
        )
        await db.commit()


async def get_summary(conversation_id: str = "default") -> str:
    resolved = await _ensure_conversation_exists(conversation_id)
    conv_uuid = UUID(resolved)
    async with SessionLocal() as db:
        conversation = await db.scalar(select(Conversation).where(Conversation.id == conv_uuid))
        if not conversation or not conversation.summary:
            return ""
        return conversation.summary


def _split_by_token_window(
    history: list[dict],
    window_tokens: int = RECENT_WINDOW_TOKENS,
    min_turns: int = MIN_RECENT_TURNS,
) -> tuple[list[dict], list[dict]]:
    """
    Split history into (old_messages, recent_messages).

    Scans backwards from the end, accumulating tokens.  The recent window
    grows until *both* conditions are met:
      - cumulative tokens >= window_tokens
      - user turn count >= min_turns

    The split always lands on a user message so the recent window starts
    cleanly at a user turn boundary.

    If the entire history fits without meeting both thresholds the function
    returns ([], history) — nothing to compress.
    """
    if not history:
        return [], []

    cumulative_tokens = 0
    user_count = 0

    for i in range(len(history) - 1, -1, -1):
        cumulative_tokens += estimate_messages_tokens([history[i]])
        if history[i]["role"] == "user":
            user_count += 1
            if cumulative_tokens >= window_tokens and user_count >= min_turns:
                return history[:i], history[i:]

    # Scanned everything without meeting both conditions — all is recent.
    return [], list(history)


async def _load_uncompressed(db, conv_uuid: UUID) -> tuple[list, Conversation | None]:
    """Load messages that haven't been compressed yet + the conversation row."""
    conversation = await db.scalar(select(Conversation).where(Conversation.id == conv_uuid))
    if not conversation:
        return [], None

    all_rows = list(
        await db.scalars(
            select(Message)
            .where(Message.conversation_id == conv_uuid)
            .order_by(Message.created_at.asc(), Message.id.asc())
        )
    )

    if not conversation.compressed_before_id:
        return all_rows, conversation

    # Find the boundary and return everything after it.
    boundary_idx = None
    for idx, row in enumerate(all_rows):
        if row.id == conversation.compressed_before_id:
            boundary_idx = idx
            break

    if boundary_idx is None:
        # Boundary message missing (shouldn't happen); treat as no compression.
        return all_rows, conversation

    return all_rows[boundary_idx + 1 :], conversation


async def get_recent_messages(conversation_id: str = "default") -> list[dict]:
    """Return the messages that should be sent to the AI as context.

    If no summary exists yet the full (uncompressed) history is returned so the
    model never loses context.  Once a summary exists the recent token-window
    is applied.
    """
    resolved = await _ensure_conversation_exists(conversation_id)
    conv_uuid = UUID(resolved)

    async with SessionLocal() as db:
        rows, conversation = await _load_uncompressed(db, conv_uuid)

    history = [{"role": row.role.value, "content": row.content} for row in rows]

    if not conversation or not (conversation.summary or "").strip():
        return history

    _, recent = _split_by_token_window(history)
    return recent


async def get_last_user_message(conversation_id: str = "default") -> str | list | None:
    resolved = await _ensure_conversation_exists(conversation_id)
    conv_uuid = UUID(resolved)
    async with SessionLocal() as db:
        row = await db.scalar(
            select(Message)
            .where(Message.conversation_id == conv_uuid, Message.role == MessageRole.user)
            .order_by(Message.created_at.desc(), Message.id.desc())
        )
        return row.content if row else None


async def get_current_user_turn(conversation_id: str = "default") -> int:
    resolved = await _ensure_conversation_exists(conversation_id)
    conv_uuid = UUID(resolved)
    async with SessionLocal() as db:
        value = await db.scalar(
            select(func.count(Message.id)).where(
                Message.conversation_id == conv_uuid,
                Message.role == MessageRole.user,
            )
        )
    return int(value or 0)


async def remove_last_assistant(conversation_id: str = "default") -> bool:
    resolved = await _ensure_conversation_exists(conversation_id)
    conv_uuid = UUID(resolved)
    async with SessionLocal() as db:
        last = await db.scalar(
            select(Message)
            .where(Message.conversation_id == conv_uuid)
            .order_by(Message.created_at.desc(), Message.id.desc())
        )
        if not last or last.role != MessageRole.assistant:
            return False
        await db.delete(last)
        await db.commit()
        return True


async def remove_last_user(conversation_id: str = "default") -> bool:
    resolved = await _ensure_conversation_exists(conversation_id)
    conv_uuid = UUID(resolved)
    async with SessionLocal() as db:
        last = await db.scalar(
            select(Message)
            .where(Message.conversation_id == conv_uuid)
            .order_by(Message.created_at.desc(), Message.id.desc())
        )
        if not last or last.role != MessageRole.user:
            return False
        await db.delete(last)
        await db.commit()
        return True


async def rewind_to_user_turn(conversation_id: str, target_user_turn: int) -> bool:
    if target_user_turn < 1:
        return False

    resolved = await _ensure_conversation_exists(conversation_id)
    conv_uuid = UUID(resolved)

    async with SessionLocal() as db:
        user_rows = await db.scalars(
            select(Message)
            .where(Message.conversation_id == conv_uuid, Message.role == MessageRole.user)
            .order_by(Message.created_at.asc(), Message.id.asc())
        )
        users = list(user_rows)
        if len(users) < target_user_turn:
            return False

        boundary = users[target_user_turn - 1]

        all_rows = await db.scalars(
            select(Message)
            .where(Message.conversation_id == conv_uuid)
            .order_by(Message.created_at.asc(), Message.id.asc())
        )
        rows = list(all_rows)
        boundary_index = next((idx for idx, row in enumerate(rows) if row.id == boundary.id), None)
        if boundary_index is None:
            return False

        to_delete = rows[boundary_index + 1 :]
        for row in to_delete:
            await db.delete(row)

        conversation = await db.scalar(select(Conversation).where(Conversation.id == conv_uuid))
        if conversation:
            conversation.summary = None
            conversation.compressed_before_id = None

        await db.commit()

    trim_debug_turns(resolved, target_user_turn - 1)
    return True


def record_debug_request(
    conversation_id: str,
    model: str,
    messages: list[dict],
    turn_index: int | None = None,
):
    turns = _debug_turns.setdefault(conversation_id, [])
    resolved_turn_index = turn_index if turn_index is not None else 0
    same_turn_versions = [t for t in turns if t.get("turn_index") == resolved_turn_index]
    version_index = len(same_turn_versions) + 1
    turns.append(
        {
            "turn_index": resolved_turn_index,
            "version_index": version_index,
            "model": model,
            "request_messages": deepcopy(messages),
            "response_raw": "",
        }
    )


def record_debug_response(conversation_id: str, response_raw):
    turns = _debug_turns.setdefault(conversation_id, [])
    if not turns:
        return
    turns[-1]["response_raw"] = response_raw


def trim_debug_turns(conversation_id: str, keep_turns: int):
    turns = _debug_turns.setdefault(conversation_id, [])
    keep_upto = max(0, keep_turns)
    _debug_turns[conversation_id] = [turn for turn in turns if turn.get("turn_index", 0) <= keep_upto]


def get_debug_turns(conversation_id: str = "default") -> list[dict]:
    return _debug_turns.setdefault(conversation_id, [])


async def check_and_compress(conversation_id: str = "default", force: bool = False) -> bool:
    """Compress older messages into a summary when enough have accumulated.

    Messages are **never deleted** — only a boundary marker
    (``compressed_before_id``) and the summary text are updated on the
    Conversation row so the frontend can still display the full history.
    """
    resolved = await _ensure_conversation_exists(conversation_id)
    conv_uuid = UUID(resolved)

    async with SessionLocal() as db:
        rows, conversation = await _load_uncompressed(db, conv_uuid)
        if not rows or not conversation:
            return False

        history = [{"role": row.role.value, "content": row.content} for row in rows]

        # Guard: only compress after an assistant reply has landed.
        if history[-1].get("role") != "assistant":
            return False

        # Split into old / recent by token window.
        old_msgs, keep_msgs = _split_by_token_window(history)
        if not old_msgs:
            return False

        # Check threshold (force_compress from advance skips this).
        if not force:
            old_tokens = estimate_messages_tokens(old_msgs)
            if old_tokens < COMPRESS_TOKEN_THRESHOLD:
                return False

        # Prepend existing summary so incremental compression doesn't lose context.
        old_summary = (conversation.summary or "").strip()
        compress_input = list(old_msgs)
        if old_summary:
            compress_input = [
                {"role": "system", "content": f"之前的摘要：{old_summary}"}
            ] + compress_input

        new_summary = compress_messages(compress_input)

        # Update boundary marker — the last message in old_msgs.
        # old_msgs has len(old_msgs) items which map to rows[:len(old_msgs)].
        boundary_row = rows[len(old_msgs) - 1]
        conversation.summary = new_summary
        conversation.compressed_before_id = boundary_row.id

        await db.commit()
        return True


async def clear_history(conversation_id: str = "default"):
    resolved = await _ensure_conversation_exists(conversation_id)
    conv_uuid = UUID(resolved)
    async with SessionLocal() as db:
        await db.execute(delete(Message).where(Message.conversation_id == conv_uuid))
        conversation = await db.scalar(select(Conversation).where(Conversation.id == conv_uuid))
        if conversation:
            conversation.summary = None
            conversation.compressed_before_id = None
        await db.commit()
    _debug_turns[resolved] = []
