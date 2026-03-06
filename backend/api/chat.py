import asyncio
import json
import re
from collections.abc import AsyncGenerator, Iterator

from dotenv import load_dotenv
from fastapi import APIRouter, Query, Request, Response
from fastapi.responses import StreamingResponse

from core.conversation import (
    add_message,
    check_and_compress,
    clear_history,
    ensure_session,
    get_current_user_turn,
    get_debug_turns,
    get_history,
    get_last_user_message,
    get_recent_messages,
    get_summary,
    record_debug_request,
    record_debug_response,
    remove_last_user,
    remove_last_assistant,
    rewind_to_user_turn,
)
from core.concurrency import conversation_guard
from core.pending_tools import clear_pending_tool, get_pending_tool, set_pending_tool
from core.providers import get_sync_client_for_model, get_thinking_extra_params, resolve_model
from core.teaching_engine import clear_plan, get_plan, get_teaching_context
from core.token_utils import estimate_messages_tokens
from core.tool_definitions import TEACHING_TOOLS
from core.tool_handler import handle_tool_call
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
        from fastapi import HTTPException
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
) -> tuple[list[dict], str | None, dict, list[dict], bool, list[str], bool, dict | None]:
    """
    Resolve tool calls in non-streaming rounds, then return final text candidate.
    Returns: (messages, reply, raw, tool_trace, tools_enabled, probe_reasoning,
              force_compress, pending)

    When disconnect_check is provided (an async callable returning bool),
    the loop will check for client disconnection between API calls and exit early.
    """
    tool_trace: list[dict] = []
    probe_reasoning: list[str] = []
    reply: str | None = ""
    raw: dict = {}
    tools_enabled = True
    force_compress = False
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
                return messages, reply, raw, tool_trace, tools_enabled, probe_reasoning, force_compress, pending_request
            continue

        reply = choice.message.content or ""
        break
    else:
        reply = "（教学引擎处理超时）"

    return messages, reply, raw, tool_trace, tools_enabled, probe_reasoning, force_compress, pending_request


async def _save_assistant_and_debug(
    conversation_id: str,
    reply: str,
    response_raw: dict,
    force_compress: bool = False,
):
    await add_message("assistant", reply, conversation_id)
    record_debug_response(conversation_id, response_raw or {"assistant_text": reply})
    await check_and_compress(conversation_id, force=force_compress)


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


def _pick_text(item: dict, keys: list[str]) -> str:
    for key in keys:
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _format_question_block(title: str, questions: list[dict], target_keys: list[str]) -> str:
    if not questions:
        return ""
    lines = [title]
    for idx, item in enumerate(questions, start=1):
        if not isinstance(item, dict):
            continue
        q = _pick_text(item, ["question", "content", "text", "prompt", "stem"])
        target = _pick_text(item, target_keys)
        if not q:
            continue
        if target:
            lines.append(f"{idx}. {q}\n（目的：{target}）")
        else:
            lines.append(f"{idx}. {q}")
    return "\n".join(lines).strip()


def _build_tool_student_text(tool_trace: list[dict]) -> str:
    blocks: list[str] = []
    for round_item in tool_trace:
        for call in round_item.get("tool_calls", []):
            name = call.get("name")
            raw_args = call.get("arguments", "{}")
            try:
                args = json.loads(raw_args) if isinstance(raw_args, str) else {}
                if not isinstance(args, dict):
                    args = {}
            except Exception:
                args = {}

            if name == "assess":
                questions = args.get("questions", [])
                if isinstance(questions, list):
                    block = _format_question_block(
                        "【诊断问题】",
                        questions,
                        ["purpose", "expected_concept", "target", "goal"],
                    )
                    if block:
                        blocks.append(block)
            elif name == "quiz":
                questions = args.get("questions", [])
                if not isinstance(questions, list):
                    for alt in ("items", "quiz", "quiz_items"):
                        alt_questions = args.get(alt)
                        if isinstance(alt_questions, list):
                            questions = alt_questions
                            break
                phase_id = args.get("phase_id")
                title = f"【阶段{phase_id}检测题】" if phase_id is not None else "【检测题】"
                if isinstance(questions, list):
                    block = _format_question_block(
                        title,
                        questions,
                        ["expected_concept", "purpose", "target", "goal"],
                    )
                    if block:
                        blocks.append(block)

    # Deduplicate while preserving order
    uniq: list[str] = []
    for block in blocks:
        if block not in uniq:
            uniq.append(block)
    return "\n\n".join(uniq).strip()


def _format_answers_readable(tool_name: str, answers: list[dict]) -> str:
    """Human-readable summary of tool answers, saved to message history."""
    lines = []
    if tool_name == "assess":
        lines.append("[诊断问题回答]")
    elif tool_name == "quiz":
        lines.append("[阶段检测回答]")
    else:
        lines.append("[工具问答回答]")
    for i, item in enumerate(answers, start=1):
        q = item.get("question", f"问题{i}") if isinstance(item, dict) else f"问题{i}"
        a = item.get("answer", "(未回答)") if isinstance(item, dict) else "(未回答)"
        lines.append(f"{i}. 问题：{q}")
        lines.append(f"   回答：{a}")
    return "\n".join(lines)


def _format_answers(tool_name: str, answers: list[dict]) -> str:
    lines = []
    if tool_name == "assess":
        lines.append("学生的诊断回答：")
    elif tool_name == "quiz":
        lines.append("学生的检测回答：")
    else:
        lines.append("学生回答：")

    for i, item in enumerate(answers, start=1):
        q = item.get("question", f"问题{i}") if isinstance(item, dict) else f"问题{i}"
        a = item.get("answer", "(未回答)") if isinstance(item, dict) else "(未回答)"
        lines.append(f"{i}. 问题：{q}")
        lines.append(f"   回答：{a}")

    return json.dumps(
        {
            "status": "ok",
            "student_answers": "\n".join(lines),
        },
        ensure_ascii=False,
    )


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
) -> tuple[list[dict], dict | None, bool]:
    if not stream_tool_calls:
        return [], None, False

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
                await set_pending_tool(
                    conversation_id=conversation_id,
                    tool_call_id=call["id"],
                    tool_name=fn_name,
                    messages_snapshot=messages,
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

    return round_tools, pending_request, round_force_compress


async def _prepare_retry_context(conversation_id: str):
    history = await get_history(conversation_id)
    if not history:
        raise ValueError("No messages found for retry.")
    # If history ends with assistant, remove it so the model regenerates.
    # If history ends with user (e.g. abort before any content was saved),
    # that's fine — we just regenerate from the existing user message.
    if history[-1]["role"] == "assistant":
        await remove_last_assistant(conversation_id)
    user_message = await get_last_user_message(conversation_id)
    if user_message is None:
        raise ValueError("No user message found for retry.")
    return await _build_messages(conversation_id)


async def _prepare_rewind_context(conversation_id: str, req: RewindRequest):
    if not await rewind_to_user_turn(conversation_id, req.target_user_turn):
        raise ValueError("Invalid target_user_turn for rewind.")

    await remove_last_user(conversation_id)

    replacement = req.replacement_message.strip()
    if not replacement and not req.images:
        raise ValueError("replacement_message cannot be empty.")

    await add_message("user", _build_user_content(replacement, req.images), conversation_id)
    return await _build_messages(conversation_id)


@router.post("/chat", response_model=ChatOrToolResponse)
async def chat(req: ChatRequest, response: Response) -> ChatOrToolResponse:
    conversation_id = await ensure_session(req.conversation_id)
    response.headers["X-Conversation-Id"] = conversation_id
    model = _resolve_model(req.model)

    async with conversation_guard(conversation_id):
        try:
            # If a previous interactive tool was pending and the student sends a new message,
            # treat it as a fresh turn.
            await clear_pending_tool(conversation_id)
            await add_message("user", _build_user_content(req.message, req.images), conversation_id)
            messages = await _build_messages(conversation_id)
            record_debug_request(conversation_id, model, messages, turn_index=await get_current_user_turn(conversation_id))

            _, reply, raw, tool_trace, _, probe_reasoning, force_compress, pending = await _run_tool_loop(
                messages, model, conversation_id, thinking=req.thinking
            )
            if pending:
                partial_reply = _merge_probe_thinking(reply or "", probe_reasoning)
                return ChatOrToolResponse(reply=partial_reply, tool_input_required=pending)
            tool_student_text = _build_tool_student_text(tool_trace)
            if tool_student_text and tool_student_text not in reply:
                reply = f"{tool_student_text}\n\n{reply}".strip()
            reply = _merge_probe_thinking(reply, probe_reasoning)
            wrapped_raw = {
                "assistant_text": reply,
                "final_response": raw,
                "tool_rounds": tool_trace,
                "probe_reasoning": probe_reasoning,
            }
            await _save_assistant_and_debug(conversation_id, reply, wrapped_raw, force_compress=force_compress)
        except Exception:
            reply = _FALLBACK_REPLY

        return ChatOrToolResponse(reply=reply)


@router.post("/chat/stream")
async def chat_stream(req: ChatRequest, request: Request) -> StreamingResponse:
    conversation_id = await ensure_session(req.conversation_id)
    model = _resolve_model(req.model)
    thinking = req.thinking
    # If a previous interactive tool was pending and the student sends a new message,
    # treat it as a fresh turn.
    await clear_pending_tool(conversation_id)
    await add_message("user", _build_user_content(req.message, req.images), conversation_id)
    base_messages = await _build_messages(conversation_id)
    record_debug_request(conversation_id, model, base_messages, turn_index=await get_current_user_turn(conversation_id))

    async def event_generator() -> AsyncGenerator[str, None]:
        full_reply = ""
        full_thinking = ""
        raw_chunks: list[dict] = []
        stream: Iterator | None = None
        force_compress = False
        saved = False
        paused = False
        try:
            tool_trace: list[dict] = []
            messages = list(base_messages)
            tools_enabled = True

            stream_completed = False
            for _ in range(_TOOL_LOOP_LIMIT):
                stream, tools_enabled = _create_stream_completion(
                    model=model,
                    messages=messages,
                    use_tools=tools_enabled,
                    thinking=thinking,
                )
                round_content = ""
                round_tool_parts: list[dict] = []

                for chunk in stream:
                    if await request.is_disconnected():
                        break
                    chunk_raw = chunk.model_dump() if hasattr(chunk, "model_dump") else {}
                    if chunk_raw:
                        raw_chunks.append(chunk_raw)
                        if await request.is_disconnected():
                            break
                        yield _sse_payload({"type": "raw", "kind": "chunk", "data": chunk_raw})
                    if not chunk.choices:
                        continue
                    delta = chunk.choices[0].delta
                    content = getattr(delta, "content", None)
                    if content:
                        round_content += content
                        full_reply += content
                        if await request.is_disconnected():
                            break
                        yield _sse_payload({"type": "token", "content": content})
                    reasoning = _extract_reasoning(delta, chunk_raw)
                    if reasoning:
                        full_thinking += reasoning
                        if await request.is_disconnected():
                            break
                        yield _sse_payload({"type": "thinking", "content": reasoning})
                    round_tool_parts.extend(_extract_tool_deltas(delta, chunk_raw))

                if await request.is_disconnected():
                    return

                merged_tool_calls = _merge_tool_delta_parts(round_tool_parts)
                if not merged_tool_calls:
                    stream_completed = True
                    break

                round_tools, stream_pending, round_fc = await _process_stream_tool_calls(
                    messages,
                    merged_tool_calls,
                    round_content,
                    conversation_id,
                )
                force_compress = force_compress or round_fc
                if round_tools:
                    tool_trace.append(
                        {
                            "round": len(tool_trace) + 1,
                            "tool_calls": round_tools,
                        }
                    )
                    yield _sse_payload(
                        {
                            "type": "raw",
                            "kind": "tool_round",
                            "data": tool_trace[-1],
                        }
                    )
                    for tool in round_tools:
                        if await request.is_disconnected():
                            return
                        yield _sse_payload(
                            {
                                "type": "tool",
                                "name": tool.get("name", "unknown"),
                                "arguments": tool.get("arguments"),
                                "result": tool.get("result"),
                            }
                        )
                    round_tool_text = _build_tool_student_text(
                        [{"round": len(tool_trace), "tool_calls": round_tools}]
                    )
                    if round_tool_text:
                        text_payload = f"\n\n{round_tool_text}\n\n"
                        full_reply += text_payload
                        if await request.is_disconnected():
                            return
                        yield _sse_payload({"type": "token", "content": text_payload})
                if stream_pending:
                    yield _sse_payload({"type": "tool_input_required", **stream_pending})
                    yield _sse_payload(
                        {
                            "type": "done",
                            "full_content": full_reply,
                            "thinking_content": full_thinking,
                            "conversation_id": conversation_id,
                            "paused": True,
                        }
                    )
                    paused = True
                    return

            if not stream_completed and not await request.is_disconnected():
                timeout_note = "\n（教学引擎流式处理超时）"
                full_reply += timeout_note
                yield _sse_payload({"type": "token", "content": timeout_note})

            if await request.is_disconnected():
                return

            saved = True
            await _save_assistant_and_debug(
                conversation_id,
                full_reply,
                {
                    "assistant_text": full_reply,
                    "thinking_text": full_thinking,
                    "stream_chunks": raw_chunks,
                    "tool_rounds": tool_trace,
                    "tools_enabled": tools_enabled,
                },
                force_compress=force_compress,
            )
            yield _sse_payload(
                {
                    "type": "raw",
                    "kind": "final",
                    "data": {
                        "assistant_text": full_reply,
                        "thinking_text": full_thinking,
                        "stream_chunks": raw_chunks,
                        "tool_rounds": tool_trace,
                        "tools_enabled": tools_enabled,
                    },
                }
            )
            yield _sse_payload(
                {
                    "type": "done",
                    "full_content": full_reply,
                    "thinking_content": full_thinking,
                    "conversation_id": conversation_id,
                }
            )
        except asyncio.CancelledError:
            return
        except Exception as exc:
            record_debug_response(
                conversation_id,
                {
                    "assistant_text": full_reply,
                    "thinking_text": full_thinking,
                    "stream_chunks": raw_chunks,
                    "error": str(exc),
                },
            )
            if not await request.is_disconnected():
                yield _sse_payload({"type": "error", "message": str(exc), "conversation_id": conversation_id})
        finally:
            if stream and hasattr(stream, "close"):
                try:
                    stream.close()
                except Exception:
                    pass
            # Save partial reply so history stays consistent after abort
            if not saved and not paused and full_reply.strip():
                try:
                    await _save_assistant_and_debug(
                        conversation_id,
                        full_reply,
                        {
                            "assistant_text": full_reply,
                            "thinking_text": full_thinking,
                            "stream_chunks": raw_chunks,
                            "aborted": True,
                        },
                        force_compress=force_compress,
                    )
                except Exception:
                    pass
            # Guarantee a done event so the frontend can always finalize.
            if not saved and not paused:
                try:
                    if not await request.is_disconnected():
                        yield _sse_payload({"type": "done", "conversation_id": conversation_id})
                except Exception:
                    pass

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
    resolved_session = await ensure_session(conversation_id)
    messages = await get_recent_messages(resolved_session)
    pending = await get_pending_tool(resolved_session)
    result: dict = {
        "conversation_id": resolved_session,
        "messages": messages,
    }
    if pending and pending.get("input_request"):
        result["tool_input_required"] = pending["input_request"]
    return result


@router.get("/chat/debug-info")
async def debug_info(conversation_id: str | None = Query(default=None)) -> dict:
    resolved_session = await ensure_session(conversation_id)
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
    resolved_session = await ensure_session(conversation_id)
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

        model = _resolve_model(req.model)
        messages = list(pending.get("messages", []))
        messages.append(
            {
                "role": "tool",
                "tool_call_id": req.tool_call_id,
                "content": _format_answers(str(pending.get("tool_name", "")), req.answers),
            }
        )
        tool_name = str(pending.get("tool_name", ""))
        answers_text = _format_answers_readable(tool_name, req.answers)
        await clear_pending_tool(conversation_id)
        record_debug_request(conversation_id, model, messages, turn_index=await get_current_user_turn(conversation_id))

        try:
            (
                _,
                reply,
                raw,
                tool_trace,
                _,
                probe_reasoning,
                force_compress,
                new_pending,
            ) = await _run_tool_loop(messages, model, conversation_id, thinking=req.thinking)
            if new_pending:
                partial_reply = _merge_probe_thinking(reply or "", probe_reasoning)
                return ChatOrToolResponse(reply=partial_reply, tool_input_required=new_pending)

            tool_student_text = _build_tool_student_text(tool_trace)
            if tool_student_text and tool_student_text not in (reply or ""):
                reply = f"{tool_student_text}\n\n{reply or ''}".strip()
            reply = _merge_probe_thinking(reply or "", probe_reasoning)
            await add_message("user", answers_text, conversation_id)
            await _save_assistant_and_debug(
                conversation_id,
                reply,
                {
                    "assistant_text": reply,
                    "final_response": raw,
                    "tool_rounds": tool_trace,
                    "probe_reasoning": probe_reasoning,
                },
                force_compress=force_compress,
            )
            return ChatOrToolResponse(reply=reply)
        except Exception:
            return ChatOrToolResponse(reply=_FALLBACK_REPLY)


@router.post("/chat/tool-response/stream")
async def tool_response_stream(req: ToolResponseRequest, request: Request) -> StreamingResponse:
    conversation_id = await ensure_session(req.conversation_id)
    pending = await get_pending_tool(conversation_id)
    if not pending:
        async def no_pending() -> AsyncGenerator[str, None]:
            yield _sse_payload({"type": "error", "message": "No pending tool call", "conversation_id": conversation_id})
            yield _sse_payload({"type": "done", "conversation_id": conversation_id})
        return StreamingResponse(
            no_pending(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Conversation-Id": conversation_id},
        )
    if pending.get("tool_call_id") != req.tool_call_id:
        async def bad_id() -> AsyncGenerator[str, None]:
            yield _sse_payload({"type": "error", "message": "tool_call_id mismatch", "conversation_id": conversation_id})
            yield _sse_payload({"type": "done", "conversation_id": conversation_id})
        return StreamingResponse(
            bad_id(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Conversation-Id": conversation_id},
        )

    model = _resolve_model(req.model)
    thinking = req.thinking
    tool_name = str(pending.get("tool_name", ""))
    answers_text = _format_answers_readable(tool_name, req.answers)
    base_messages = list(pending.get("messages", []))
    base_messages.append(
        {
            "role": "tool",
            "tool_call_id": req.tool_call_id,
            "content": _format_answers(tool_name, req.answers),
        }
    )
    await clear_pending_tool(conversation_id)
    record_debug_request(conversation_id, model, base_messages, turn_index=await get_current_user_turn(conversation_id))

    async def event_generator() -> AsyncGenerator[str, None]:
        full_reply = ""
        full_thinking = ""
        raw_chunks: list[dict] = []
        stream: Iterator | None = None
        force_compress = False
        saved = False
        paused = False
        try:
            tool_trace: list[dict] = []
            messages = list(base_messages)
            tools_enabled = True

            stream_completed = False
            for _ in range(_TOOL_LOOP_LIMIT):
                stream, tools_enabled = _create_stream_completion(model, messages, use_tools=tools_enabled, thinking=thinking)
                round_content = ""
                round_tool_parts: list[dict] = []
                for chunk in stream:
                    if await request.is_disconnected():
                        return
                    chunk_raw = chunk.model_dump() if hasattr(chunk, "model_dump") else {}
                    if chunk_raw:
                        raw_chunks.append(chunk_raw)
                    if not chunk.choices:
                        continue
                    delta = chunk.choices[0].delta
                    content = getattr(delta, "content", None)
                    if content:
                        round_content += content
                        full_reply += content
                        yield _sse_payload({"type": "token", "content": content})
                    reasoning = _extract_reasoning(delta, chunk_raw)
                    if reasoning:
                        full_thinking += reasoning
                        yield _sse_payload({"type": "thinking", "content": reasoning})
                    round_tool_parts.extend(_extract_tool_deltas(delta, chunk_raw))

                merged_tool_calls = _merge_tool_delta_parts(round_tool_parts)
                if not merged_tool_calls:
                    stream_completed = True
                    break
                round_tools, stream_pending, round_fc = await _process_stream_tool_calls(
                    messages, merged_tool_calls, round_content, conversation_id
                )
                force_compress = force_compress or round_fc
                if round_tools:
                    tool_trace.append({"round": len(tool_trace) + 1, "tool_calls": round_tools})
                    for tool in round_tools:
                        yield _sse_payload({"type": "tool", "name": tool.get("name", "unknown"), "arguments": tool.get("arguments"), "result": tool.get("result")})
                    round_tool_text = _build_tool_student_text([{"round": len(tool_trace), "tool_calls": round_tools}])
                    if round_tool_text:
                        text_payload = f"\n\n{round_tool_text}\n\n"
                        full_reply += text_payload
                        yield _sse_payload({"type": "token", "content": text_payload})
                if stream_pending:
                    yield _sse_payload({"type": "tool_input_required", **stream_pending})
                    yield _sse_payload({"type": "done", "full_content": full_reply, "thinking_content": full_thinking, "conversation_id": conversation_id, "paused": True})
                    paused = True
                    return

            if not stream_completed:
                timeout_note = "\n（教学引擎流式处理超时）"
                full_reply += timeout_note
                yield _sse_payload({"type": "token", "content": timeout_note})

            saved = True
            await add_message("user", answers_text, conversation_id)
            await _save_assistant_and_debug(
                conversation_id,
                full_reply,
                {
                    "assistant_text": full_reply,
                    "thinking_text": full_thinking,
                    "stream_chunks": raw_chunks,
                    "tool_rounds": tool_trace,
                    "tools_enabled": tools_enabled,
                },
                force_compress=force_compress,
            )
            yield _sse_payload({"type": "done", "full_content": full_reply, "thinking_content": full_thinking, "conversation_id": conversation_id})
        except asyncio.CancelledError:
            return
        except Exception as exc:
            record_debug_response(conversation_id, {"assistant_text": full_reply, "thinking_text": full_thinking, "stream_chunks": raw_chunks, "error": str(exc)})
            if not await request.is_disconnected():
                yield _sse_payload({"type": "error", "message": str(exc), "conversation_id": conversation_id})
        finally:
            if stream and hasattr(stream, "close"):
                try:
                    stream.close()
                except Exception:
                    pass
            if not saved and not paused and full_reply.strip():
                try:
                    await _save_assistant_and_debug(
                        conversation_id,
                        full_reply,
                        {
                            "assistant_text": full_reply,
                            "thinking_text": full_thinking,
                            "stream_chunks": raw_chunks,
                            "aborted": True,
                        },
                        force_compress=force_compress,
                    )
                except Exception:
                    pass
            # Guarantee a done event so the frontend can always finalize.
            if not saved and not paused:
                try:
                    if not await request.is_disconnected():
                        yield _sse_payload({"type": "done", "conversation_id": conversation_id})
                except Exception:
                    pass

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
        await clear_pending_tool(conversation_id)

        try:
            messages = await _prepare_retry_context(conversation_id)
            record_debug_request(conversation_id, model, messages, turn_index=await get_current_user_turn(conversation_id))

            _, reply, raw, tool_trace, _, probe_reasoning, force_compress, pending = await _run_tool_loop(
                messages, model, conversation_id, thinking=req.thinking
            )
            if pending:
                partial_reply = _merge_probe_thinking(reply or "", probe_reasoning)
                return ChatOrToolResponse(reply=partial_reply, tool_input_required=pending)
            tool_student_text = _build_tool_student_text(tool_trace)
            if tool_student_text and tool_student_text not in reply:
                reply = f"{tool_student_text}\n\n{reply}".strip()
            reply = _merge_probe_thinking(reply, probe_reasoning)
            wrapped_raw = {
                "assistant_text": reply,
                "final_response": raw,
                "tool_rounds": tool_trace,
                "probe_reasoning": probe_reasoning,
            }
            await _save_assistant_and_debug(conversation_id, reply, wrapped_raw, force_compress=force_compress)
        except Exception:
            reply = _FALLBACK_REPLY

        return ChatOrToolResponse(reply=reply)


@router.post("/chat/retry/stream")
async def retry_stream(req: RetryRequest, request: Request) -> StreamingResponse:
    conversation_id = await ensure_session(req.conversation_id)
    model = _resolve_model(req.model)
    thinking = req.thinking
    await clear_pending_tool(conversation_id)

    try:
        base_messages = await _prepare_retry_context(conversation_id)
    except Exception as exc:
        error_message = str(exc)

        async def empty_retry_generator() -> AsyncGenerator[str, None]:
            yield _sse_payload({"type": "error", "message": error_message, "conversation_id": conversation_id})
            yield _sse_payload({"type": "done", "conversation_id": conversation_id})

        return StreamingResponse(
            empty_retry_generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Conversation-Id": conversation_id,
            },
        )

    record_debug_request(conversation_id, model, base_messages, turn_index=await get_current_user_turn(conversation_id))

    async def event_generator() -> AsyncGenerator[str, None]:
        full_reply = ""
        full_thinking = ""
        raw_chunks: list[dict] = []
        stream: Iterator | None = None
        force_compress = False
        saved = False
        paused = False
        try:
            tool_trace: list[dict] = []
            messages = list(base_messages)
            tools_enabled = True

            stream_completed = False
            for _ in range(_TOOL_LOOP_LIMIT):
                stream, tools_enabled = _create_stream_completion(
                    model=model,
                    messages=messages,
                    use_tools=tools_enabled,
                    thinking=thinking,
                )
                round_content = ""
                round_tool_parts: list[dict] = []

                for chunk in stream:
                    if await request.is_disconnected():
                        break
                    chunk_raw = chunk.model_dump() if hasattr(chunk, "model_dump") else {}
                    if chunk_raw:
                        raw_chunks.append(chunk_raw)
                    if not chunk.choices:
                        continue
                    delta = chunk.choices[0].delta
                    content = getattr(delta, "content", None)
                    if content:
                        round_content += content
                        full_reply += content
                        if await request.is_disconnected():
                            break
                        yield _sse_payload({"type": "token", "content": content})
                    reasoning = _extract_reasoning(delta, chunk_raw)
                    if reasoning:
                        full_thinking += reasoning
                        if await request.is_disconnected():
                            break
                        yield _sse_payload({"type": "thinking", "content": reasoning})
                    round_tool_parts.extend(_extract_tool_deltas(delta, chunk_raw))

                if await request.is_disconnected():
                    return

                merged_tool_calls = _merge_tool_delta_parts(round_tool_parts)
                if not merged_tool_calls:
                    stream_completed = True
                    break

                round_tools, stream_pending, round_fc = await _process_stream_tool_calls(
                    messages,
                    merged_tool_calls,
                    round_content,
                    conversation_id,
                )
                force_compress = force_compress or round_fc
                if round_tools:
                    tool_trace.append(
                        {
                            "round": len(tool_trace) + 1,
                            "tool_calls": round_tools,
                        }
                    )
                    for tool in round_tools:
                        if await request.is_disconnected():
                            return
                        yield _sse_payload(
                            {
                                "type": "tool",
                                "name": tool.get("name", "unknown"),
                                "arguments": tool.get("arguments"),
                                "result": tool.get("result"),
                            }
                        )
                    round_tool_text = _build_tool_student_text(
                        [{"round": len(tool_trace), "tool_calls": round_tools}]
                    )
                    if round_tool_text:
                        text_payload = f"\n\n{round_tool_text}\n\n"
                        full_reply += text_payload
                        if await request.is_disconnected():
                            return
                        yield _sse_payload({"type": "token", "content": text_payload})
                if stream_pending:
                    yield _sse_payload({"type": "tool_input_required", **stream_pending})
                    yield _sse_payload(
                        {
                            "type": "done",
                            "full_content": full_reply,
                            "thinking_content": full_thinking,
                            "conversation_id": conversation_id,
                            "paused": True,
                        }
                    )
                    paused = True
                    return

            if not stream_completed and not await request.is_disconnected():
                timeout_note = "\n（教学引擎流式处理超时）"
                full_reply += timeout_note
                yield _sse_payload({"type": "token", "content": timeout_note})

            if await request.is_disconnected():
                return

            saved = True
            await _save_assistant_and_debug(
                conversation_id,
                full_reply,
                {
                    "assistant_text": full_reply,
                    "thinking_text": full_thinking,
                    "stream_chunks": raw_chunks,
                    "tool_rounds": tool_trace,
                    "tools_enabled": tools_enabled,
                },
                force_compress=force_compress,
            )
            yield _sse_payload(
                {
                    "type": "done",
                    "full_content": full_reply,
                    "thinking_content": full_thinking,
                    "conversation_id": conversation_id,
                }
            )
        except asyncio.CancelledError:
            return
        except Exception as exc:
            record_debug_response(
                conversation_id,
                {
                    "assistant_text": full_reply,
                    "thinking_text": full_thinking,
                    "stream_chunks": raw_chunks,
                    "error": str(exc),
                },
            )
            if not await request.is_disconnected():
                yield _sse_payload({"type": "error", "message": str(exc), "conversation_id": conversation_id})
        finally:
            if stream and hasattr(stream, "close"):
                try:
                    stream.close()
                except Exception:
                    pass
            if not saved and not paused and full_reply.strip():
                try:
                    await _save_assistant_and_debug(
                        conversation_id,
                        full_reply,
                        {
                            "assistant_text": full_reply,
                            "thinking_text": full_thinking,
                            "stream_chunks": raw_chunks,
                            "aborted": True,
                        },
                        force_compress=force_compress,
                    )
                except Exception:
                    pass
            # Guarantee a done event so the frontend can always finalize.
            if not saved and not paused:
                try:
                    if not await request.is_disconnected():
                        yield _sse_payload({"type": "done", "conversation_id": conversation_id})
                except Exception:
                    pass

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
        await clear_pending_tool(conversation_id)

        try:
            messages = await _prepare_rewind_context(conversation_id, req)
            record_debug_request(conversation_id, model, messages, turn_index=req.target_user_turn)

            _, reply, raw, tool_trace, _, probe_reasoning, force_compress, pending = await _run_tool_loop(
                messages, model, conversation_id, thinking=req.thinking
            )
            if pending:
                partial_reply = _merge_probe_thinking(reply or "", probe_reasoning)
                return ChatOrToolResponse(reply=partial_reply, tool_input_required=pending)
            tool_student_text = _build_tool_student_text(tool_trace)
            if tool_student_text and tool_student_text not in reply:
                reply = f"{tool_student_text}\n\n{reply}".strip()
            reply = _merge_probe_thinking(reply, probe_reasoning)
            wrapped_raw = {
                "assistant_text": reply,
                "final_response": raw,
                "tool_rounds": tool_trace,
                "probe_reasoning": probe_reasoning,
            }
            await _save_assistant_and_debug(conversation_id, reply, wrapped_raw, force_compress=force_compress)
        except Exception:
            reply = _FALLBACK_REPLY

        return ChatOrToolResponse(reply=reply)


@router.post("/chat/rewind/stream")
async def rewind_stream(req: RewindRequest, request: Request) -> StreamingResponse:
    conversation_id = await ensure_session(req.conversation_id)
    model = _resolve_model(req.model)
    thinking = req.thinking
    await clear_pending_tool(conversation_id)

    try:
        base_messages = await _prepare_rewind_context(conversation_id, req)
    except Exception as exc:
        error_message = str(exc)

        async def bad_rewind_generator() -> AsyncGenerator[str, None]:
            yield _sse_payload({"type": "error", "message": error_message, "conversation_id": conversation_id})
            yield _sse_payload({"type": "done", "conversation_id": conversation_id})

        return StreamingResponse(
            bad_rewind_generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Conversation-Id": conversation_id,
            },
        )

    record_debug_request(conversation_id, model, base_messages, turn_index=req.target_user_turn)

    async def event_generator() -> AsyncGenerator[str, None]:
        full_reply = ""
        full_thinking = ""
        raw_chunks: list[dict] = []
        stream: Iterator | None = None
        force_compress = False
        saved = False
        paused = False
        try:
            tool_trace: list[dict] = []
            messages = list(base_messages)
            tools_enabled = True

            stream_completed = False
            for _ in range(_TOOL_LOOP_LIMIT):
                stream, tools_enabled = _create_stream_completion(
                    model=model,
                    messages=messages,
                    use_tools=tools_enabled,
                    thinking=thinking,
                )
                round_content = ""
                round_tool_parts: list[dict] = []

                for chunk in stream:
                    if await request.is_disconnected():
                        break
                    chunk_raw = chunk.model_dump() if hasattr(chunk, "model_dump") else {}
                    if chunk_raw:
                        raw_chunks.append(chunk_raw)
                    if not chunk.choices:
                        continue
                    delta = chunk.choices[0].delta
                    content = getattr(delta, "content", None)
                    if content:
                        round_content += content
                        full_reply += content
                        if await request.is_disconnected():
                            break
                        yield _sse_payload({"type": "token", "content": content})
                    reasoning = _extract_reasoning(delta, chunk_raw)
                    if reasoning:
                        full_thinking += reasoning
                        if await request.is_disconnected():
                            break
                        yield _sse_payload({"type": "thinking", "content": reasoning})
                    round_tool_parts.extend(_extract_tool_deltas(delta, chunk_raw))

                if await request.is_disconnected():
                    return

                merged_tool_calls = _merge_tool_delta_parts(round_tool_parts)
                if not merged_tool_calls:
                    stream_completed = True
                    break

                round_tools, stream_pending, round_fc = await _process_stream_tool_calls(
                    messages,
                    merged_tool_calls,
                    round_content,
                    conversation_id,
                )
                force_compress = force_compress or round_fc
                if round_tools:
                    tool_trace.append(
                        {
                            "round": len(tool_trace) + 1,
                            "tool_calls": round_tools,
                        }
                    )
                    for tool in round_tools:
                        if await request.is_disconnected():
                            return
                        yield _sse_payload(
                            {
                                "type": "tool",
                                "name": tool.get("name", "unknown"),
                                "arguments": tool.get("arguments"),
                                "result": tool.get("result"),
                            }
                        )
                    round_tool_text = _build_tool_student_text(
                        [{"round": len(tool_trace), "tool_calls": round_tools}]
                    )
                    if round_tool_text:
                        text_payload = f"\n\n{round_tool_text}\n\n"
                        full_reply += text_payload
                        if await request.is_disconnected():
                            return
                        yield _sse_payload({"type": "token", "content": text_payload})
                if stream_pending:
                    yield _sse_payload({"type": "tool_input_required", **stream_pending})
                    yield _sse_payload(
                        {
                            "type": "done",
                            "full_content": full_reply,
                            "thinking_content": full_thinking,
                            "conversation_id": conversation_id,
                            "paused": True,
                        }
                    )
                    paused = True
                    return

            if not stream_completed and not await request.is_disconnected():
                timeout_note = "\n（教学引擎流式处理超时）"
                full_reply += timeout_note
                yield _sse_payload({"type": "token", "content": timeout_note})

            if await request.is_disconnected():
                return

            saved = True
            await _save_assistant_and_debug(
                conversation_id,
                full_reply,
                {
                    "assistant_text": full_reply,
                    "thinking_text": full_thinking,
                    "stream_chunks": raw_chunks,
                    "tool_rounds": tool_trace,
                    "tools_enabled": tools_enabled,
                },
                force_compress=force_compress,
            )
            yield _sse_payload(
                {
                    "type": "done",
                    "full_content": full_reply,
                    "thinking_content": full_thinking,
                    "conversation_id": conversation_id,
                }
            )
        except asyncio.CancelledError:
            return
        except Exception as exc:
            record_debug_response(
                conversation_id,
                {
                    "assistant_text": full_reply,
                    "thinking_text": full_thinking,
                    "stream_chunks": raw_chunks,
                    "error": str(exc),
                },
            )
            if not await request.is_disconnected():
                yield _sse_payload({"type": "error", "message": str(exc), "conversation_id": conversation_id})
        finally:
            if stream and hasattr(stream, "close"):
                try:
                    stream.close()
                except Exception:
                    pass
            if not saved and not paused and full_reply.strip():
                try:
                    await _save_assistant_and_debug(
                        conversation_id,
                        full_reply,
                        {
                            "assistant_text": full_reply,
                            "thinking_text": full_thinking,
                            "stream_chunks": raw_chunks,
                            "aborted": True,
                        },
                        force_compress=force_compress,
                    )
                except Exception:
                    pass
            # Guarantee a done event so the frontend can always finalize.
            if not saved and not paused:
                try:
                    if not await request.is_disconnected():
                        yield _sse_payload({"type": "done", "conversation_id": conversation_id})
                except Exception:
                    pass

    return StreamingResponse(
        _guarded_stream(conversation_id, event_generator()),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Conversation-Id": conversation_id,
        },
    )



