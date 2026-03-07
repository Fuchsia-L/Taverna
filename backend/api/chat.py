import asyncio
import json
import logging
import re
from collections.abc import AsyncGenerator, Awaitable, Callable, Iterator
from dataclasses import dataclass, field
from uuid import UUID

from dotenv import load_dotenv
from fastapi import APIRouter, HTTPException, Query, Request, Response
from fastapi.responses import StreamingResponse

from api.chat_formatting import build_tool_student_text, format_answers, format_answers_readable
from core.conversation import (
    add_message,
    check_and_compress,
    clear_history,
    ensure_session,
    get_current_user_turn,
    get_debug_turns,
    get_history,
    get_message_count,
    get_recent_messages,
    get_summary,
    preview_rewind_to_user_turn,
    record_debug_request,
    record_debug_response,
    remove_last_assistant,
)
from core.concurrency import conversation_guard
from core.pending_tools import clear_pending_tool, get_pending_tool, set_pending_tool
from core.providers import get_sync_client_for_model, get_thinking_extra_params, resolve_model
from core.teaching_engine import advance_phase, clear_plan, get_plan, get_teaching_context, set_plan, update_plan
from core.token_utils import estimate_messages_tokens
from core.tool_definitions import TEACHING_TOOLS
from core.tool_handler import (
    handle_tool_call,
    _try_autoname_conversation,
    _try_autoname_project,
)
from models.chat import (
    ChatOrToolResponse,
    ChatRequest,
    ChatResponse,
    RewindRequest,
    RetryRequest,
    ToolResponseRequest,
)
from prompts.template import build_system_prompt, load_soul

router = APIRouter()
load_dotenv()
_teacher_soul = load_soul("teacher_socratic.md")
_FALLBACK_REPLY = "⚠️ AI暂时无法回应，请稍后再试。"


@dataclass
class PreparedTurn:
    messages: list[dict]
    turn_index: int
    commit_on_pause: Callable[[], Awaitable[None]] | None = None
    commit_on_success: Callable[[], Awaitable[None]] | None = None
    deferred_plan: dict | None = None
    deferred_advance: dict | None = None


@dataclass
class ExecuteResult:
    status: str
    reply: str = ""
    thinking: str = ""
    raw: dict = field(default_factory=dict)
    raw_chunks: list[dict] = field(default_factory=list)
    tool_trace: list[dict] = field(default_factory=list)
    pending: dict | None = None
    force_compress: bool = False
    deferred_plan: dict | None = None
    deferred_advance: dict | None = None
    tools_enabled: bool = True


def _stream_response_raw(result: ExecuteResult) -> dict:
    return {
        "assistant_text": result.reply,
        "thinking_text": result.thinking,
        "stream_chunks": result.raw_chunks,
        "tool_rounds": result.tool_trace,
        "tools_enabled": result.tools_enabled,
    }


def _stream_done_payload(result: ExecuteResult, conversation_id: str, paused: bool = False) -> dict:
    payload = {
        "type": "done",
        "full_content": result.reply,
        "thinking_content": result.thinking,
        "conversation_id": conversation_id,
    }
    if paused:
        payload["paused"] = True
    return payload


def _require_conversation_id(conversation_id: str | None) -> str:
    """Validate and return conversation_id, raising 400 if missing/invalid."""
    if not conversation_id or not conversation_id.strip():
        raise HTTPException(status_code=400, detail="conversation_id is required")
    try:
        return str(UUID(conversation_id.strip()))
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid conversation_id format")
_TOOL_LOOP_LIMIT = 5


async def _guarded_stream(conversation_id: str, gen: AsyncGenerator[str, None]) -> AsyncGenerator[str, None]:
    """Wrap an SSE generator so conversation_guard is held for its entire lifetime."""
    async with conversation_guard(conversation_id):
        async for item in gen:
            yield item


def _resolve_model(req_model: str | None) -> str:
    try:
        return resolve_model(req_model)
    except ValueError as exc:
        raise HTTPException(status_code=503, detail=str(exc))


async def _build_messages(conversation_id: str) -> list[dict]:
    summary = await get_summary(conversation_id)
    teaching_context = await get_teaching_context(conversation_id)
    system_prompt = build_system_prompt(
        teacher_soul=_teacher_soul,
        student_profile="",
        current_topic="",
        summary=summary,
        current_state=teaching_context,
    )
    return [{"role": "system", "content": system_prompt}] + await get_recent_messages(conversation_id)


async def _build_messages_with_context(conversation_id: str, summary: str, recent_messages: list[dict]) -> list[dict]:
    teaching_context = await get_teaching_context(conversation_id)
    system_prompt = build_system_prompt(
        teacher_soul=_teacher_soul,
        student_profile="",
        current_topic="",
        summary=summary,
        current_state=teaching_context,
    )
    return [{"role": "system", "content": system_prompt}] + recent_messages


async def _noop_commit() -> None:
    return


def _build_user_content(message: str, images: list[str] | None) -> str | list[dict]:
    text = (message or "").strip()
    safe_images = [item for item in (images or []) if isinstance(item, str) and item.strip()]
    if not safe_images:
        return text

    content: list[dict] = [{"type": "text", "text": text}]
    for data_url in safe_images:
        content.append({"type": "image_url", "image_url": {"url": data_url}})
    return content


def _is_tool_unsupported_error(exc: Exception) -> bool:
    text = str(exc).lower()
    markers = [
        "tool",
        "function call",
        "function calling",
        "tool_calls",
        "unsupported",
        "does not support",
        "invalid tool",
    ]
    return any(marker in text for marker in markers)


def _create_completion(model: str, messages: list[dict], use_tools: bool = True, thinking: bool = False):
    client = get_sync_client_for_model(model)
    kwargs = {
        "model": model,
        "messages": messages,
    }
    if use_tools:
        kwargs["tools"] = TEACHING_TOOLS
    if thinking:
        extra = get_thinking_extra_params(model)
        if extra:
            kwargs["extra_body"] = extra

    try:
        response = client.chat.completions.create(**kwargs)
        return response, use_tools
    except Exception as exc:
        if use_tools and _is_tool_unsupported_error(exc):
            kwargs.pop("tools", None)
            response = client.chat.completions.create(**kwargs)
            return response, False
        raise


def _create_stream_completion(model: str, messages: list[dict], use_tools: bool = True, thinking: bool = False):
    client = get_sync_client_for_model(model)
    kwargs = {
        "model": model,
        "messages": messages,
        "stream": True,
    }
    if use_tools:
        kwargs["tools"] = TEACHING_TOOLS
    if thinking:
        extra = get_thinking_extra_params(model)
        if extra:
            kwargs["extra_body"] = extra

    try:
        stream = client.chat.completions.create(**kwargs)
        return stream, use_tools
    except Exception as exc:
        if use_tools and _is_tool_unsupported_error(exc):
            kwargs.pop("tools", None)
            stream = client.chat.completions.create(**kwargs)
            return stream, False
        raise


def _extract_text_and_raw(response) -> tuple[str, dict]:
    raw = response.model_dump() if hasattr(response, "model_dump") else {}
    content = ""
    if getattr(response, "choices", None):
        message = response.choices[0].message
        content = message.content or ""
    return content, raw


def _sse_payload(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _extract_reasoning(delta, chunk_raw: dict | None = None) -> str:
    def _normalize_reasoning(value) -> str:
        if isinstance(value, str):
            return value
        if isinstance(value, list):
            parts: list[str] = []
            for item in value:
                if isinstance(item, str):
                    parts.append(item)
                elif isinstance(item, dict):
                    text = item.get("text") or item.get("content") or item.get("reasoning") or ""
                    if isinstance(text, str) and text:
                        parts.append(text)
            return "".join(parts)
        if isinstance(value, dict):
            text = value.get("text") or value.get("content") or value.get("reasoning") or ""
            return text if isinstance(text, str) else ""
        return ""

    candidates = [
        getattr(delta, "reasoning_content", None),
        getattr(delta, "thinking", None),
        getattr(delta, "reasoning", None),
        getattr(delta, "reasoning_text", None),
    ]
    for item in candidates:
        normalized = _normalize_reasoning(item)
        if normalized:
            return normalized

    if isinstance(chunk_raw, dict):
        try:
            raw_delta = (chunk_raw.get("choices") or [{}])[0].get("delta", {})
            for key in ("reasoning_content", "thinking", "reasoning", "reasoning_text"):
                value = raw_delta.get(key)
                normalized = _normalize_reasoning(value)
                if normalized:
                    return normalized
        except Exception:
            return ""
    return ""


def _extract_message_reasoning(message, response_raw: dict | None = None) -> str:
    candidates = [
        getattr(message, "reasoning_content", None),
        getattr(message, "thinking", None),
        getattr(message, "reasoning", None),
        getattr(message, "reasoning_text", None),
    ]
    for item in candidates:
        normalized = _extract_reasoning(type("Obj", (), {"reasoning": item})(), None)
        if normalized:
            return normalized

    if isinstance(response_raw, dict):
        try:
            raw_message = (response_raw.get("choices") or [{}])[0].get("message", {})
            for key in ("reasoning_content", "thinking", "reasoning", "reasoning_text"):
                value = raw_message.get(key)
                normalized = _extract_reasoning(type("Obj", (), {"reasoning": value})(), None)
                if normalized:
                    return normalized
        except Exception:
            return ""
    return ""


async def _run_tool_loop(
    messages: list[dict],
    model: str,
    conversation_id: str,
    max_rounds: int = _TOOL_LOOP_LIMIT,
    disconnect_check=None,
    thinking: bool = False,
) -> tuple[list[dict], str | None, dict, list[dict], bool, list[str], bool, dict | None, dict | None, dict | None]:
    """
    Resolve tool calls in non-streaming rounds, then return final text candidate.
    Returns: (messages, reply, raw, tool_trace, tools_enabled, probe_reasoning,
              force_compress, pending, deferred_advance, deferred_plan)

    When disconnect_check is provided (an async callable returning bool),
    the loop will check for client disconnection between API calls and exit early.
    """
    tool_trace: list[dict] = []
    probe_reasoning: list[str] = []
    reply: str | None = ""
    raw: dict = {}
    tools_enabled = True
    force_compress = False
    deferred_advance: dict | None = None
    deferred_plan: dict | None = None
    pending_request: dict | None = None

    for round_index in range(max_rounds):
        if disconnect_check and await disconnect_check():
            break
        response, tools_enabled = await asyncio.to_thread(
            _create_completion, model, messages, use_tools=tools_enabled, thinking=thinking
        )
        if disconnect_check and await disconnect_check():
            break
        choice = response.choices[0]
        raw = response.model_dump() if hasattr(response, "model_dump") else {}
        round_reasoning = _extract_message_reasoning(choice.message, raw)
        if round_reasoning:
            probe_reasoning.append(round_reasoning)

        tool_calls = getattr(choice.message, "tool_calls", None) or []
        if tool_calls:
            if choice.message.content:
                reply = (reply or "") + choice.message.content
            assistant_msg = choice.message.model_dump() if hasattr(choice.message, "model_dump") else {
                "role": "assistant",
                "content": choice.message.content,
            }
            messages.append(assistant_msg)

            round_tools: list[dict] = []
            interactive_seen = False
            for tool_call in tool_calls:
                fn_name = tool_call.function.name
                raw_args = tool_call.function.arguments or "{}"
                try:
                    fn_args = json.loads(raw_args)
                    if not isinstance(fn_args, dict):
                        raise ValueError("Tool arguments must be a JSON object")
                    handled = await handle_tool_call(fn_name, fn_args, conversation_id)
                except Exception as parse_exc:
                    handled = {
                        "requires_input": False,
                        "result": json.dumps(
                            {
                                "status": "error",
                                "message": f"Tool args parse/exec failed: {parse_exc}",
                            },
                            ensure_ascii=False,
                        ),
                    }

                if handled.get("requires_input"):
                    if not interactive_seen:
                        await set_pending_tool(
                            conversation_id=conversation_id,
                            tool_call_id=tool_call.id,
                            tool_name=fn_name,
                            messages_snapshot=messages,
                            input_request=handled.get("input_request"),
                            message_count=await get_message_count(conversation_id),
                            deferred_plan=deferred_plan,
                            deferred_advance=deferred_advance,
                        )
                        pending_request = {
                            **handled.get("input_request", {}),
                            "tool_call_id": tool_call.id,
                        }
                        interactive_seen = True
                    else:
                        deferred_result = json.dumps(
                            {
                                "status": "error",
                                "message": "Deferred: only the first interactive tool can pause this round.",
                            },
                            ensure_ascii=False,
                        )
                        messages.append(
                            {
                                "role": "tool",
                                "tool_call_id": tool_call.id,
                                "content": deferred_result,
                            }
                        )
                        round_tools.append(
                            {
                                "name": fn_name,
                                "arguments": raw_args,
                                "result": deferred_result,
                            }
                        )
                    continue

                if handled.get("force_compress"):
                    force_compress = True
                if handled.get("deferred_advance"):
                    deferred_advance = handled["deferred_advance"]
                if handled.get("deferred_plan"):
                    deferred_plan = handled["deferred_plan"]

                result = handled.get("result") or json.dumps(
                        {
                            "status": "error",
                            "message": "Tool returned empty result",
                        },
                        ensure_ascii=False,
                    )

                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": result,
                    }
                )
                round_tools.append(
                    {
                        "name": fn_name,
                        "arguments": raw_args,
                        "result": result,
                    }
                )

            tool_trace.append(
                {
                    "round": round_index + 1,
                    "tool_calls": round_tools,
                }
            )
            if pending_request:
                return messages, reply, raw, tool_trace, tools_enabled, probe_reasoning, force_compress, pending_request, deferred_advance, deferred_plan
            continue

        reply = choice.message.content or ""
        break
    else:
        reply = "（教学引擎处理超时）"

    return messages, reply, raw, tool_trace, tools_enabled, probe_reasoning, force_compress, pending_request, deferred_advance, deferred_plan


_log = logging.getLogger(__name__)


async def _save_assistant_and_debug(
    conversation_id: str,
    reply: str,
    response_raw: dict,
    force_compress: bool = False,
    deferred_advance: dict | None = None,
    deferred_plan: dict | None = None,
):
    await add_message("assistant", reply, conversation_id)
    record_debug_response(conversation_id, response_raw or {"assistant_text": reply})
    # Post-save side-effects are isolated so a failure here does not
    # propagate up and cause the caller to treat the save as failed
    # (the assistant message is already persisted at this point).
    if deferred_plan:
        try:
            await _execute_deferred_plan(conversation_id, deferred_plan)
        except Exception:
            _log.exception("deferred plan write failed for %s", conversation_id)
    if deferred_advance:
        try:
            await advance_phase(
                conversation_id,
                deferred_advance["completed_phase"],
                deferred_advance["summary"],
                deferred_advance["move_to"],
            )
        except Exception:
            _log.exception("deferred advance failed for %s", conversation_id)
    try:
        await check_and_compress(conversation_id, force=force_compress)
    except Exception:
        _log.exception("compression failed for %s", conversation_id)


async def _execute_deferred_plan(conversation_id: str, deferred: dict) -> None:
    """Replay plan writes that were deferred from the tool handler."""
    mode = deferred.get("mode")
    goal = deferred.get("goal", "")
    if mode == "project_autoname":
        await _try_autoname_project(conversation_id, goal)
    elif mode == "set":
        await set_plan(conversation_id, goal, deferred.get("phases", []))
        await _try_autoname_conversation(conversation_id, goal)
    elif mode == "update":
        await update_plan(conversation_id, goal, deferred.get("phases", []))
        await _try_autoname_conversation(conversation_id, goal)


async def _build_plan_payload(conversation_id: str) -> dict | None:
    plan = await get_plan(conversation_id)
    if not plan:
        return None
    return {
        "goal": plan.goal,
        "current_phase": plan.current_phase_id,
        "phases": [
            {
                "id": p.id,
                "title": p.title,
                "status": p.status,
                "summary": p.summary,
            }
            for p in plan.phases
        ],
    }


def _merge_probe_thinking(reply: str, probe_reasoning: list[str]) -> str:
    if not probe_reasoning:
        return reply
    if re.search(r"<think>[\s\S]*?</think>", reply, flags=re.IGNORECASE):
        return reply
    merged = "\n".join([item for item in probe_reasoning if item]).strip()
    if not merged:
        return reply
    return f"<think>{merged}</think>\n{reply}"


def _extract_tool_deltas(delta, chunk_raw: dict | None = None) -> list[dict]:
    tool_calls = getattr(delta, "tool_calls", None)
    if tool_calls:
        result: list[dict] = []
        for item in tool_calls:
            fn = getattr(item, "function", None)
            result.append(
                {
                    "index": getattr(item, "index", None),
                    "id": getattr(item, "id", None),
                    "name": getattr(fn, "name", None) if fn else None,
                    "arguments": getattr(fn, "arguments", None) if fn else None,
                }
            )
        return result

    if isinstance(chunk_raw, dict):
        try:
            raw_delta = (chunk_raw.get("choices") or [{}])[0].get("delta", {})
            raw_tool_calls = raw_delta.get("tool_calls") or []
            parsed: list[dict] = []
            for item in raw_tool_calls:
                fn = item.get("function") or {}
                parsed.append(
                    {
                        "index": item.get("index"),
                        "id": item.get("id"),
                        "name": fn.get("name"),
                        "arguments": fn.get("arguments"),
                    }
                )
            return parsed
        except Exception:
            return []
    return []


def _merge_tool_delta_parts(parts: list[dict]) -> list[dict]:
    buckets: dict[int, dict] = {}
    next_index = 0
    for part in parts:
        idx = part.get("index")
        if not isinstance(idx, int):
            idx = next_index
        next_index = max(next_index, idx + 1)
        bucket = buckets.setdefault(
            idx,
            {"id": "", "name": "", "arguments": ""},
        )
        if isinstance(part.get("id"), str) and part["id"]:
            bucket["id"] = part["id"]
        if isinstance(part.get("name"), str) and part["name"]:
            bucket["name"] += part["name"]
        if isinstance(part.get("arguments"), str) and part["arguments"]:
            bucket["arguments"] += part["arguments"]

    merged: list[dict] = []
    for idx in sorted(buckets.keys()):
        item = buckets[idx]
        if item["name"]:
            merged.append(item)
    return merged


async def _process_stream_tool_calls(
    messages: list[dict],
    stream_tool_calls: list[dict],
    assistant_content: str,
    conversation_id: str,
    accumulated_deferred_plan: dict | None = None,
    accumulated_deferred_advance: dict | None = None,
) -> tuple[list[dict], dict | None, bool, dict | None, dict | None]:
    if not stream_tool_calls:
        return [], None, False, None, None

    assistant_tool_calls = []
    for idx, call in enumerate(stream_tool_calls):
        call_id = call.get("id") or f"stream_tool_{idx}"
        assistant_tool_calls.append(
            {
                "id": call_id,
                "type": "function",
                "function": {
                    "name": call.get("name", ""),
                    "arguments": call.get("arguments", "{}"),
                },
            }
        )

    messages.append(
        {
            "role": "assistant",
            "content": assistant_content or "",
            "tool_calls": assistant_tool_calls,
        }
    )

    round_tools: list[dict] = []
    pending_request: dict | None = None
    round_force_compress = False
    round_deferred_advance: dict | None = None
    round_deferred_plan: dict | None = None
    interactive_seen = False
    for call in assistant_tool_calls:
        fn_name = call["function"].get("name", "")
        raw_args = call["function"].get("arguments", "{}")
        try:
            fn_args = json.loads(raw_args) if isinstance(raw_args, str) else {}
            if not isinstance(fn_args, dict):
                raise ValueError("Tool arguments must be a JSON object")
            handled = await handle_tool_call(fn_name, fn_args, conversation_id)
        except Exception as parse_exc:
            handled = {
                "requires_input": False,
                "result": json.dumps(
                    {
                        "status": "error",
                        "message": f"Tool args parse/exec failed: {parse_exc}",
                    },
                    ensure_ascii=False,
                ),
            }

        if handled.get("requires_input"):
            if not interactive_seen:
                # Merge current round's deferred data with accumulated from prior rounds.
                merged_dp = round_deferred_plan or accumulated_deferred_plan
                merged_da = round_deferred_advance or accumulated_deferred_advance
                await set_pending_tool(
                    conversation_id=conversation_id,
                    tool_call_id=call["id"],
                    tool_name=fn_name,
                    messages_snapshot=messages,
                    input_request=handled.get("input_request"),
                    message_count=await get_message_count(conversation_id),
                    deferred_plan=merged_dp,
                    deferred_advance=merged_da,
                )
                pending_request = {
                    **handled.get("input_request", {}),
                    "tool_call_id": call["id"],
                }
                interactive_seen = True
            else:
                deferred_result = json.dumps(
                    {
                        "status": "error",
                        "message": "Deferred: only the first interactive tool can pause this round.",
                    },
                    ensure_ascii=False,
                )
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call["id"],
                        "content": deferred_result,
                    }
                )
                round_tools.append(
                    {
                        "name": fn_name,
                        "arguments": raw_args,
                        "result": deferred_result,
                    }
                )
            continue

        if handled.get("force_compress"):
            round_force_compress = True
        if handled.get("deferred_advance"):
            round_deferred_advance = handled["deferred_advance"]
        if handled.get("deferred_plan"):
            round_deferred_plan = handled["deferred_plan"]

        result = handled.get("result") or json.dumps(
            {"status": "error", "message": "Tool returned empty result"},
            ensure_ascii=False,
        )

        messages.append(
            {
                "role": "tool",
                "tool_call_id": call["id"],
                "content": result,
            }
        )
        round_tools.append(
            {
                "name": fn_name,
                "arguments": raw_args,
                "result": result,
            }
        )

    return round_tools, pending_request, round_force_compress, round_deferred_advance, round_deferred_plan


async def _prepare_retry_context(conversation_id: str):
    """Build retry messages in-memory WITHOUT mutating DB.

    Returns (messages, commit_fn). Call commit_fn only after model succeeds.
    """
    messages = await _build_messages(conversation_id)
    needs_remove = False
    # If last non-system message is assistant, remove it in-memory.
    if len(messages) > 1 and messages[-1]["role"] == "assistant":
        messages = messages[:-1]
        needs_remove = True
    if not any(m["role"] == "user" for m in messages):
        raise ValueError("No user message found for retry.")

    async def commit():
        if needs_remove:
            await remove_last_assistant(conversation_id)

    return messages, commit


async def _prepare_rewind_context(conversation_id: str, req: RewindRequest):
    # Validate replacement content BEFORE mutating history.
    replacement = req.replacement_message.strip()
    if not replacement and not req.images:
        raise ValueError("replacement_message cannot be empty.")
    replacement_content = _build_user_content(replacement, req.images)
    summary, recent_messages, commit = await preview_rewind_to_user_turn(
        conversation_id,
        req.target_user_turn,
        replacement_content,
    )
    messages = await _build_messages_with_context(conversation_id, summary, recent_messages)
    return messages, commit


def _merge_deferred(
    prepared: PreparedTurn,
    result_plan: dict | None,
    result_advance: dict | None,
) -> tuple[dict | None, dict | None]:
    deferred_plan = result_plan or prepared.deferred_plan
    deferred_advance = result_advance or prepared.deferred_advance
    return deferred_plan, deferred_advance


def _finalize_nonstream_reply(reply: str, tool_trace: list[dict], probe_reasoning: list[str]) -> str:
    tool_student_text = build_tool_student_text(tool_trace)
    if tool_student_text and tool_student_text not in (reply or ""):
        reply = f"{tool_student_text}\n\n{reply or ''}".strip()
    return _merge_probe_thinking(reply or "", probe_reasoning)


async def _execute_nonstream_turn(
    prepared: PreparedTurn,
    model: str,
    conversation_id: str,
    thinking: bool,
) -> ExecuteResult:
    (
        _,
        reply,
        raw,
        tool_trace,
        tools_enabled,
        probe_reasoning,
        force_compress,
        pending,
        deferred_advance,
        deferred_plan,
    ) = await _run_tool_loop(prepared.messages, model, conversation_id, thinking=thinking)
    merged_plan, merged_advance = _merge_deferred(prepared, deferred_plan, deferred_advance)
    return ExecuteResult(
        status="paused" if pending else "done",
        reply=_finalize_nonstream_reply(reply or "", tool_trace, probe_reasoning),
        raw={
            "final_response": raw,
            "probe_reasoning": probe_reasoning,
        },
        tool_trace=tool_trace,
        pending=pending,
        force_compress=force_compress,
        deferred_plan=merged_plan,
        deferred_advance=merged_advance,
        tools_enabled=tools_enabled,
    )


async def _run_nonstream_turn(
    prepared: PreparedTurn,
    model: str,
    conversation_id: str,
    thinking: bool,
) -> ChatOrToolResponse:
    record_debug_request(conversation_id, model, prepared.messages, turn_index=prepared.turn_index)
    result = await _execute_nonstream_turn(prepared, model, conversation_id, thinking)
    if result.status == "paused":
        if prepared.commit_on_pause:
            await prepared.commit_on_pause()
        return ChatOrToolResponse(reply=result.reply, tool_input_required=result.pending)
    if not result.reply or not result.reply.strip():
        return ChatOrToolResponse(reply=_FALLBACK_REPLY)
    if prepared.commit_on_success:
        await prepared.commit_on_success()
    await _save_assistant_and_debug(
        conversation_id,
        result.reply,
        {
            "assistant_text": result.reply,
            **result.raw,
            "tool_rounds": result.tool_trace,
        },
        force_compress=result.force_compress,
        deferred_advance=result.deferred_advance,
        deferred_plan=result.deferred_plan,
    )
    return ChatOrToolResponse(reply=result.reply)


async def _finalize_stream_pause(
    prepared: PreparedTurn,
    result: ExecuteResult,
    conversation_id: str,
) -> list[str]:
    if prepared.commit_on_pause:
        await prepared.commit_on_pause()
    return [
        _sse_payload({"type": "tool_input_required", **(result.pending or {})}),
        _sse_payload(_stream_done_payload(result, conversation_id, paused=True)),
    ]


async def _refresh_pending_message_count(conversation_id: str) -> None:
    pending = await get_pending_tool(conversation_id)
    if not pending:
        return
    await set_pending_tool(
        conversation_id=conversation_id,
        tool_call_id=str(pending.get("tool_call_id", "")),
        tool_name=str(pending.get("tool_name", "")),
        messages_snapshot=list(pending.get("messages", [])),
        input_request=pending.get("input_request"),
        message_count=await get_message_count(conversation_id),
        deferred_plan=pending.get("deferred_plan"),
        deferred_advance=pending.get("deferred_advance"),
    )


async def _finalize_stream_success(
    prepared: PreparedTurn,
    result: ExecuteResult,
    conversation_id: str,
    emit_raw_final: bool,
) -> list[str]:
    if prepared.commit_on_success:
        await prepared.commit_on_success()
    response_raw = _stream_response_raw(result)
    await _save_assistant_and_debug(
        conversation_id,
        result.reply,
        response_raw,
        force_compress=result.force_compress,
        deferred_advance=result.deferred_advance,
        deferred_plan=result.deferred_plan,
    )
    events: list[str] = []
    if emit_raw_final:
        events.append(
            _sse_payload(
                {
                    "type": "raw",
                    "kind": "final",
                    "data": response_raw,
                }
            )
        )
    events.append(_sse_payload(_stream_done_payload(result, conversation_id)))
    return events


async def _prepare_chat_turn(conversation_id: str, req: ChatRequest) -> PreparedTurn:
    await clear_pending_tool(conversation_id)
    await add_message("user", _build_user_content(req.message, req.images), conversation_id)
    return PreparedTurn(
        messages=await _build_messages(conversation_id),
        turn_index=await get_current_user_turn(conversation_id),
        commit_on_pause=_noop_commit,
        commit_on_success=_noop_commit,
    )


async def _prepare_retry_turn(conversation_id: str) -> PreparedTurn:
    pending = await get_pending_tool(conversation_id)
    if pending and pending.get("messages"):
        async def clear_on_success() -> None:
            await clear_pending_tool(conversation_id)

        return PreparedTurn(
            messages=list(pending.get("messages", [])),
            turn_index=await get_current_user_turn(conversation_id),
            commit_on_pause=_noop_commit,
            commit_on_success=clear_on_success,
            deferred_plan=pending.get("deferred_plan"),
            deferred_advance=pending.get("deferred_advance"),
        )

    messages, retry_commit = await _prepare_retry_context(conversation_id)
    return PreparedTurn(
        messages=messages,
        turn_index=await get_current_user_turn(conversation_id),
        commit_on_pause=retry_commit,
        commit_on_success=retry_commit,
    )


async def _prepare_rewind_turn(conversation_id: str, req: RewindRequest) -> PreparedTurn:
    await clear_pending_tool(conversation_id)
    messages, rewind_commit = await _prepare_rewind_context(conversation_id, req)
    return PreparedTurn(
        messages=messages,
        turn_index=req.target_user_turn,
        commit_on_pause=rewind_commit,
        commit_on_success=rewind_commit,
    )


async def _prepare_tool_response_turn(conversation_id: str, req: ToolResponseRequest, pending: dict) -> PreparedTurn:
    tool_name = str(pending.get("tool_name", ""))
    answers_text = format_answers_readable(tool_name, req.answers)
    messages = list(pending.get("messages", []))
    messages.append(
        {
            "role": "tool",
            "tool_call_id": req.tool_call_id,
            "content": format_answers(tool_name, req.answers),
        }
    )

    async def commit_on_pause() -> None:
        await add_message("user", answers_text, conversation_id)
        await _refresh_pending_message_count(conversation_id)

    async def commit_on_success() -> None:
        await clear_pending_tool(conversation_id)
        await add_message("user", answers_text, conversation_id)

    return PreparedTurn(
        messages=messages,
        turn_index=await get_current_user_turn(conversation_id),
        commit_on_pause=commit_on_pause,
        commit_on_success=commit_on_success,
        deferred_plan=pending.get("deferred_plan"),
        deferred_advance=pending.get("deferred_advance"),
    )


async def _run_stream_turn(
    prepared: PreparedTurn,
    model: str,
    conversation_id: str,
    thinking: bool,
    request: Request,
    emit_raw_chunks: bool = False,
    emit_raw_tool_rounds: bool = False,
    emit_raw_final: bool = False,
) -> AsyncGenerator[str, None]:
    result = ExecuteResult(
        status="cancelled",
        deferred_plan=prepared.deferred_plan,
        deferred_advance=prepared.deferred_advance,
    )
    stream: Iterator | None = None
    saved = False
    paused = False
    done_emitted = False
    try:
        record_debug_request(conversation_id, model, prepared.messages, turn_index=prepared.turn_index)
        messages = list(prepared.messages)
        result.tools_enabled = True

        stream_completed = False
        for _ in range(_TOOL_LOOP_LIMIT):
            stream, result.tools_enabled = _create_stream_completion(
                model=model,
                messages=messages,
                use_tools=result.tools_enabled,
                thinking=thinking,
            )
            round_content = ""
            round_tool_parts: list[dict] = []

            for chunk in stream:
                if await request.is_disconnected():
                    result.status = "cancelled"
                    break
                chunk_raw = chunk.model_dump() if hasattr(chunk, "model_dump") else {}
                if chunk_raw:
                    result.raw_chunks.append(chunk_raw)
                    if emit_raw_chunks:
                        if await request.is_disconnected():
                            result.status = "cancelled"
                            break
                        yield _sse_payload({"type": "raw", "kind": "chunk", "data": chunk_raw})
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                content = getattr(delta, "content", None)
                if content:
                    round_content += content
                    result.reply += content
                    if await request.is_disconnected():
                        result.status = "cancelled"
                        break
                    yield _sse_payload({"type": "token", "content": content})
                reasoning = _extract_reasoning(delta, chunk_raw)
                if reasoning:
                    result.thinking += reasoning
                    if await request.is_disconnected():
                        result.status = "cancelled"
                        break
                    yield _sse_payload({"type": "thinking", "content": reasoning})
                round_tool_parts.extend(_extract_tool_deltas(delta, chunk_raw))

            if await request.is_disconnected():
                result.status = "cancelled"
                return

            merged_tool_calls = _merge_tool_delta_parts(round_tool_parts)
            if not merged_tool_calls:
                stream_completed = True
                result.status = "done"
                break

            round_tools, stream_pending, round_fc, round_da, round_dp = await _process_stream_tool_calls(
                messages,
                merged_tool_calls,
                round_content,
                conversation_id,
                accumulated_deferred_plan=result.deferred_plan,
                accumulated_deferred_advance=result.deferred_advance,
            )
            result.force_compress = result.force_compress or round_fc
            if round_da:
                result.deferred_advance = round_da
            if round_dp:
                result.deferred_plan = round_dp
            if round_tools:
                result.tool_trace.append(
                    {
                        "round": len(result.tool_trace) + 1,
                        "tool_calls": round_tools,
                    }
                )
                if emit_raw_tool_rounds:
                    yield _sse_payload(
                        {
                            "type": "raw",
                            "kind": "tool_round",
                            "data": result.tool_trace[-1],
                        }
                    )
                for tool in round_tools:
                    if await request.is_disconnected():
                        result.status = "cancelled"
                        return
                    yield _sse_payload(
                        {
                            "type": "tool",
                            "name": tool.get("name", "unknown"),
                            "arguments": tool.get("arguments"),
                            "result": tool.get("result"),
                        }
                    )
                round_tool_text = build_tool_student_text(
                    [{"round": len(result.tool_trace), "tool_calls": round_tools}]
                )
                if round_tool_text:
                    text_payload = f"\n\n{round_tool_text}\n\n"
                    result.reply += text_payload
                    if await request.is_disconnected():
                        result.status = "cancelled"
                        return
                    yield _sse_payload({"type": "token", "content": text_payload})
            if stream_pending:
                result.status = "paused"
                result.pending = stream_pending
                for event in await _finalize_stream_pause(prepared, result, conversation_id):
                    yield event
                paused = True
                done_emitted = True
                return

        if not stream_completed and not await request.is_disconnected():
            timeout_note = "\n（教学引擎流式处理超时）"
            result.reply += timeout_note
            yield _sse_payload({"type": "token", "content": timeout_note})

        if await request.is_disconnected():
            result.status = "cancelled"
            return

        result.status = "done"
        if not result.reply.strip():
            yield _sse_payload({"type": "error", "message": _FALLBACK_REPLY, "conversation_id": conversation_id})
        else:
            for event in await _finalize_stream_success(prepared, result, conversation_id, emit_raw_final):
                yield event
            saved = True
            done_emitted = True
    except asyncio.CancelledError:
        result.status = "cancelled"
        return
    except Exception as exc:
        result.status = "error"
        record_debug_response(
            conversation_id,
            {
                "assistant_text": result.reply,
                "thinking_text": result.thinking,
                "stream_chunks": result.raw_chunks,
                "error": str(exc),
            },
        )
        if not await request.is_disconnected():
            yield _sse_payload({"type": "error", "message": str(exc), "conversation_id": conversation_id})
            yield _sse_payload(_stream_done_payload(result, conversation_id))
            done_emitted = True
    finally:
        if stream and hasattr(stream, "close"):
            try:
                stream.close()
            except Exception:
                pass
        if not saved and not paused and not done_emitted and result.status != "cancelled":
            try:
                if not await request.is_disconnected():
                    yield _sse_payload(_stream_done_payload(result, conversation_id))
            except Exception:
                pass


@router.post("/chat", response_model=ChatOrToolResponse)
async def chat(req: ChatRequest, response: Response) -> ChatOrToolResponse:
    conversation_id = await ensure_session(req.conversation_id)
    response.headers["X-Conversation-Id"] = conversation_id
    model = _resolve_model(req.model)

    async with conversation_guard(conversation_id):
        try:
            prepared = await _prepare_chat_turn(conversation_id, req)
            return await _run_nonstream_turn(prepared, model, conversation_id, req.thinking)
        except Exception:
            _log.exception("chat failed for %s", conversation_id)
            return ChatOrToolResponse(reply=_FALLBACK_REPLY)


@router.post("/chat/stream")
async def chat_stream(req: ChatRequest, request: Request) -> StreamingResponse:
    conversation_id = await ensure_session(req.conversation_id)
    model = _resolve_model(req.model)

    async def event_generator() -> AsyncGenerator[str, None]:
        try:
            prepared = await _prepare_chat_turn(conversation_id, req)
            async for event in _run_stream_turn(
                prepared,
                model,
                conversation_id,
                req.thinking,
                request,
                emit_raw_chunks=True,
                emit_raw_tool_rounds=True,
                emit_raw_final=True,
            ):
                yield event
        except Exception as exc:
            if not await request.is_disconnected():
                yield _sse_payload({"type": "error", "message": str(exc), "conversation_id": conversation_id})

    return StreamingResponse(
        _guarded_stream(conversation_id, event_generator()),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Conversation-Id": conversation_id,
        },
    )


@router.get("/chat/history")
async def chat_history(conversation_id: str | None = Query(default=None)):
    resolved_session = _require_conversation_id(conversation_id)
    messages = await get_history(resolved_session)
    pending = await get_pending_tool(resolved_session)
    result: dict = {
        "conversation_id": resolved_session,
        "messages": messages,
    }
    if pending and pending.get("input_request"):
        result["tool_input_required"] = {
            **pending["input_request"],
            "tool_call_id": pending.get("tool_call_id"),
        }
    return result


@router.get("/chat/debug-info")
async def debug_info(conversation_id: str | None = Query(default=None)) -> dict:
    resolved_session = _require_conversation_id(conversation_id)
    history = await get_history(resolved_session)
    summary = await get_summary(resolved_session)
    system_prompt = build_system_prompt(
        teacher_soul=_teacher_soul,
        student_profile="",
        current_topic="",
        summary=summary,
        current_state=await get_teaching_context(resolved_session),
    )
    return {
        "system_prompt": system_prompt,
        "summary": summary,
        "teaching_plan": await _build_plan_payload(resolved_session),
        "history_length": len(history),
        "token_estimate": estimate_messages_tokens(history),
        "model": resolve_model(None),
        "conversation_id": resolved_session,
        "debug_turns": get_debug_turns(resolved_session),
    }


@router.post("/chat/clear")
async def clear_chat(conversation_id: str | None = Query(default=None)) -> dict:
    resolved_session = _require_conversation_id(conversation_id)
    async with conversation_guard(resolved_session):
        await clear_history(resolved_session)
        await clear_plan(resolved_session)
        await clear_pending_tool(resolved_session)
    return {"status": "ok", "conversation_id": resolved_session}


@router.post("/chat/tool-response", response_model=ChatOrToolResponse)
async def tool_response(req: ToolResponseRequest, response: Response) -> ChatOrToolResponse:
    conversation_id = await ensure_session(req.conversation_id)
    response.headers["X-Conversation-Id"] = conversation_id

    async with conversation_guard(conversation_id):
        pending = await get_pending_tool(conversation_id)
        if not pending:
            return ChatOrToolResponse(reply="⚠️ 当前没有待回答的工具问题。")
        if pending.get("tool_call_id") != req.tool_call_id:
            return ChatOrToolResponse(reply="⚠️ 工具调用ID不匹配。")
        stored_count = pending.get("message_count")
        if stored_count is not None:
            current_count = await get_message_count(conversation_id)
            if current_count != stored_count:
                await clear_pending_tool(conversation_id)
                return ChatOrToolResponse(reply="⚠️ 会话已变更，请重新操作。")

        try:
            model = _resolve_model(req.model)
            prepared = await _prepare_tool_response_turn(conversation_id, req, pending)
            return await _run_nonstream_turn(prepared, model, conversation_id, req.thinking)
        except Exception:
            _log.exception("tool-response failed for %s", conversation_id)
            return ChatOrToolResponse(reply=_FALLBACK_REPLY)


@router.post("/chat/tool-response/stream")
async def tool_response_stream(req: ToolResponseRequest, request: Request) -> StreamingResponse:
    conversation_id = await ensure_session(req.conversation_id)

    async def event_generator() -> AsyncGenerator[str, None]:
        try:
            # --- validation under lock ---
            pending = await get_pending_tool(conversation_id)
            if not pending:
                yield _sse_payload({"type": "error", "message": "No pending tool call", "conversation_id": conversation_id})
                yield _sse_payload({"type": "done", "full_content": "", "conversation_id": conversation_id})
                return
            if pending.get("tool_call_id") != req.tool_call_id:
                yield _sse_payload({"type": "error", "message": "tool_call_id mismatch", "conversation_id": conversation_id})
                yield _sse_payload({"type": "done", "full_content": "", "conversation_id": conversation_id})
                return
            stored_count = pending.get("message_count")
            if stored_count is not None:
                current_count = await get_message_count(conversation_id)
                if current_count != stored_count:
                    await clear_pending_tool(conversation_id)
                    yield _sse_payload({"type": "error", "message": "会话已变更，请重新操作。", "conversation_id": conversation_id})
                    yield _sse_payload({"type": "done", "full_content": "", "conversation_id": conversation_id})
                    return

            model = _resolve_model(req.model)
            prepared = await _prepare_tool_response_turn(conversation_id, req, pending)
            async for event in _run_stream_turn(
                prepared,
                model,
                conversation_id,
                req.thinking,
                request,
            ):
                yield event
        except Exception as exc:
            if not await request.is_disconnected():
                yield _sse_payload({"type": "error", "message": str(exc), "conversation_id": conversation_id})

    return StreamingResponse(
        _guarded_stream(conversation_id, event_generator()),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Conversation-Id": conversation_id},
    )


@router.post("/chat/retry", response_model=ChatOrToolResponse)
async def retry(req: RetryRequest, response: Response) -> ChatOrToolResponse:
    conversation_id = await ensure_session(req.conversation_id)
    response.headers["X-Conversation-Id"] = conversation_id
    model = _resolve_model(req.model)

    async with conversation_guard(conversation_id):
        try:
            prepared = await _prepare_retry_turn(conversation_id)
            return await _run_nonstream_turn(prepared, model, conversation_id, req.thinking)
        except Exception:
            _log.exception("retry failed for %s", conversation_id)
            return ChatOrToolResponse(reply=_FALLBACK_REPLY)


@router.post("/chat/retry/stream")
async def retry_stream(req: RetryRequest, request: Request) -> StreamingResponse:
    conversation_id = await ensure_session(req.conversation_id)
    model = _resolve_model(req.model)

    async def event_generator() -> AsyncGenerator[str, None]:
        try:
            prepared = await _prepare_retry_turn(conversation_id)
            async for event in _run_stream_turn(
                prepared,
                model,
                conversation_id,
                req.thinking,
                request,
            ):
                yield event
        except Exception as exc:
            if not await request.is_disconnected():
                yield _sse_payload({"type": "error", "message": str(exc), "conversation_id": conversation_id})

    return StreamingResponse(
        _guarded_stream(conversation_id, event_generator()),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Conversation-Id": conversation_id,
        },
    )


@router.post("/chat/rewind", response_model=ChatOrToolResponse)
async def rewind(req: RewindRequest, response: Response) -> ChatOrToolResponse:
    conversation_id = await ensure_session(req.conversation_id)
    response.headers["X-Conversation-Id"] = conversation_id
    model = _resolve_model(req.model)

    async with conversation_guard(conversation_id):
        try:
            prepared = await _prepare_rewind_turn(conversation_id, req)
            return await _run_nonstream_turn(prepared, model, conversation_id, req.thinking)
        except Exception:
            _log.exception("rewind failed for %s", conversation_id)
            return ChatOrToolResponse(reply=_FALLBACK_REPLY)


@router.post("/chat/rewind/stream")
async def rewind_stream(req: RewindRequest, request: Request) -> StreamingResponse:
    conversation_id = await ensure_session(req.conversation_id)
    model = _resolve_model(req.model)

    async def event_generator() -> AsyncGenerator[str, None]:
        try:
            prepared = await _prepare_rewind_turn(conversation_id, req)
            async for event in _run_stream_turn(
                prepared,
                model,
                conversation_id,
                req.thinking,
                request,
            ):
                yield event
        except Exception as exc:
            if not await request.is_disconnected():
                yield _sse_payload({"type": "error", "message": str(exc), "conversation_id": conversation_id})

    return StreamingResponse(
        _guarded_stream(conversation_id, event_generator()),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Conversation-Id": conversation_id,
        },
    )



