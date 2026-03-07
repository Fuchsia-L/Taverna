import json
import os
import sys
from dataclasses import dataclass
from typing import Any


try:
    import httpx
    import requests
except ImportError as exc:
    print(f"[FATAL] Missing dependency: {exc}")
    print("Install with: pip install requests httpx")
    raise SystemExit(1) from exc


BASE_URL = os.getenv("TAVERNA_BASE_URL", "http://localhost:8000").rstrip("/")
MODEL = os.getenv("TAVERNA_MODEL") or None
TIMEOUT = float(os.getenv("TAVERNA_TIMEOUT", "90"))


class SmokeFailure(RuntimeError):
    pass


@dataclass
class HttpResult:
    response: requests.Response
    body: dict[str, Any]
    conversation_id: str | None


@dataclass
class StreamResult:
    status_code: int
    headers: dict[str, str]
    events: list[dict[str, Any]]
    conversation_id: str | None


def log_step(title: str) -> None:
    print(f"\n=== {title} ===")


def log_ok(message: str) -> None:
    print(f"[OK] {message}")


def log_info(message: str) -> None:
    print(f"[INFO] {message}")


def fail(message: str, extra: Any | None = None) -> None:
    print(f"[FAIL] {message}")
    if extra is not None:
        if isinstance(extra, (dict, list)):
            print(json.dumps(extra, ensure_ascii=False, indent=2))
        else:
            print(str(extra))
    raise SmokeFailure(message)


def endpoint(path: str) -> str:
    return f"{BASE_URL}{path}"


def post_json(session: requests.Session, path: str, payload: dict[str, Any]) -> HttpResult:
    response = session.post(endpoint(path), json=payload, timeout=TIMEOUT)
    body: dict[str, Any]
    try:
        body = response.json()
    except Exception:
        body = {"raw_text": response.text}
    if response.status_code >= 400:
        fail(f"{path} returned HTTP {response.status_code}", body)
    return HttpResult(
        response=response,
        body=body,
        conversation_id=response.headers.get("X-Conversation-Id"),
    )


def get_json(session: requests.Session, path: str, **params: Any) -> dict[str, Any]:
    response = session.get(endpoint(path), params=params, timeout=TIMEOUT)
    try:
        body = response.json()
    except Exception:
        body = {"raw_text": response.text}
    if response.status_code >= 400:
        fail(f"{path} returned HTTP {response.status_code}", body)
    return body


def stream_json(client: httpx.Client, path: str, payload: dict[str, Any]) -> StreamResult:
    events: list[dict[str, Any]] = []
    with client.stream("POST", endpoint(path), json=payload, timeout=TIMEOUT) as response:
        if response.status_code >= 400:
            fail(f"{path} returned HTTP {response.status_code}", response.text)
        for line in response.iter_lines():
            if not line or not line.startswith("data: "):
                continue
            raw = line[6:]
            try:
                parsed = json.loads(raw)
            except Exception:
                fail(f"{path} emitted invalid SSE JSON", raw)
            events.append(parsed)
        return StreamResult(
            status_code=response.status_code,
            headers=dict(response.headers),
            events=events,
            conversation_id=response.headers.get("X-Conversation-Id"),
        )


def require(condition: bool, message: str, extra: Any | None = None) -> None:
    if not condition:
        fail(message, extra)


def require_non_empty_reply(body: dict[str, Any], context: str) -> None:
    reply = str(body.get("reply", "") or "").strip()
    require(bool(reply), f"{context} returned empty reply", body)


def count_user_turns(history: dict[str, Any]) -> int:
    return sum(1 for item in history.get("messages", []) if item.get("role") == "user")


def extract_tool_event(events: list[dict[str, Any]]) -> dict[str, Any] | None:
    for event in events:
        if event.get("type") == "tool_input_required":
            return event
    return None


def extract_done_event(events: list[dict[str, Any]]) -> dict[str, Any] | None:
    for event in reversed(events):
        if event.get("type") == "done":
            return event
    return None


def verify_pause_event_order(events: list[dict[str, Any]], context: str) -> dict[str, Any]:
    tool_event = extract_tool_event(events)
    done_event = extract_done_event(events)
    require(tool_event is not None, f"{context} did not emit tool_input_required", events)
    require(done_event is not None, f"{context} did not emit done", events)
    require(done_event.get("paused") is True, f"{context} done event missing paused=True", events)
    tool_idx = events.index(tool_event)
    done_idx = events.index(done_event)
    require(tool_idx < done_idx, f"{context} emitted done before tool_input_required", events)
    return tool_event


def build_answers(tool_event: dict[str, Any], ambiguous: bool = False) -> list[dict[str, str]]:
    answers = []
    for idx, item in enumerate(tool_event.get("questions", []) or [], start=1):
        question = str(item.get("question") or f"问题{idx}")
        if ambiguous:
            answer = "我不太确定，我是猜的，可能没理解。"
        else:
            answer = f"这是第{idx}题的冒烟测试回答。"
        answers.append({"question": question, "answer": answer})
    return answers


def build_very_ambiguous_answers(tool_event: dict[str, Any]) -> list[dict[str, str]]:
    answers = []
    templates = [
        "我完全不会，也不知道怎么想。",
        "我猜可能是 x，但我其实不理解为什么。",
        "这题我不会，前面的概念我也没懂。",
    ]
    for idx, item in enumerate(tool_event.get("questions", []) or [], start=1):
        question = str(item.get("question") or f"问题{idx}")
        answer = templates[(idx - 1) % len(templates)]
        answers.append({"question": question, "answer": answer})
    return answers


def verify_server_up(session: requests.Session) -> None:
    response = session.get(endpoint("/openapi.json"), timeout=TIMEOUT)
    require(response.status_code < 500, "Server is not reachable", response.text)
    log_ok(f"Server reachable at {BASE_URL}")


def run_nonstream_flow(session: requests.Session) -> None:
    log_step("Non-stream Chat")
    chat_result = post_json(
        session,
        "/api/chat",
        {
            "message": "请只用一句中文回复“冒烟测试启动成功”。不要调用任何工具。",
            "conversation_id": None,
            "model": MODEL,
            "thinking": False,
        },
    )
    conversation_id = chat_result.conversation_id
    require(conversation_id is not None, "chat did not return X-Conversation-Id", chat_result.body)
    require_non_empty_reply(chat_result.body, "chat")
    log_ok(f"chat ok, conversation_id={conversation_id}")

    log_step("Retry")
    retry_result = post_json(
        session,
        "/api/chat/retry",
        {
            "conversation_id": conversation_id,
            "model": MODEL,
            "thinking": False,
        },
    )
    require(retry_result.conversation_id == conversation_id, "retry returned a different conversation_id", retry_result.body)
    require_non_empty_reply(retry_result.body, "retry")
    log_ok("retry ok")

    log_step("Rewind")
    history_before = get_json(session, "/api/chat/history", conversation_id=conversation_id)
    user_turns = count_user_turns(history_before)
    require(user_turns >= 1, "rewind precondition failed: no user turns found", history_before)
    rewind_result = post_json(
        session,
        "/api/chat/rewind",
        {
            "conversation_id": conversation_id,
            "target_user_turn": 1,
            "replacement_message": "请只用一句中文回复“rewind 成功”。不要调用任何工具。",
            "images": [],
            "model": MODEL,
            "thinking": False,
        },
    )
    require_non_empty_reply(rewind_result.body, "rewind")
    history_after = get_json(session, "/api/chat/history", conversation_id=conversation_id)
    user_messages = [item.get("content") for item in history_after.get("messages", []) if item.get("role") == "user"]
    require(
        any("rewind 成功" in str(content) for content in user_messages),
        "rewind history does not contain replacement message",
        history_after,
    )
    log_ok("rewind ok")


def trigger_first_pause(httpx_client: httpx.Client) -> tuple[str, dict[str, Any], list[dict[str, Any]]]:
    prompts = [
        "你是老师。不要直接讲解，不要调用 plan。请立即调用 assess 工具，提出 2 道简短诊断题来判断我是否理解一元一次方程。收到我的回答后，如果我的回答含糊、说不知道、或出现明显错误，你必须继续调用 assess 或 quiz，不能直接给讲解，也不能只发普通文本问题。",
        "你是老师。不要直接讲解，不要调用 plan。请立即调用 quiz 工具，给我 2 道非常短的题来检测我是否理解一元一次方程。收到我的回答后，如果我的回答含糊、说不知道、或出现明显错误，你必须继续调用 quiz 或 assess，不能直接给讲解，也不能只发普通文本问题。",
    ]
    failures: list[dict[str, Any]] = []
    for prompt in prompts:
        stream_result = stream_json(
            httpx_client,
            "/api/chat/stream",
            {
                "message": prompt,
                "conversation_id": None,
                "model": MODEL,
                "thinking": False,
            },
        )
        conversation_id = stream_result.conversation_id
        if not conversation_id:
            failures.append({"prompt": prompt, "events": stream_result.events, "error": "missing conversation id"})
            continue
        try:
            tool_event = verify_pause_event_order(stream_result.events, "chat/stream")
            log_ok(f"chat/stream pause ok, conversation_id={conversation_id}, tool={tool_event.get('tool')}")
            return conversation_id, tool_event, stream_result.events
        except SmokeFailure:
            failures.append({"prompt": prompt, "events": stream_result.events, "conversation_id": conversation_id})
    fail("Could not trigger assess/quiz pause through /api/chat/stream", failures)


def run_tool_stream_flow(session: requests.Session, httpx_client: httpx.Client) -> None:
    log_step("Stream Tool Pause")
    conversation_id, first_tool_event, first_events = trigger_first_pause(httpx_client)
    log_info(f"first pause event count={len(first_events)}")
    history = get_json(session, "/api/chat/history", conversation_id=conversation_id)
    require(history.get("tool_input_required"), "history did not expose pending tool input after stream pause", history)

    log_step("Tool Response Stream")
    answer_attempts = [
        build_very_ambiguous_answers(first_tool_event),
        build_answers(first_tool_event, ambiguous=True),
        [
            {
                "question": str(item.get("question") or f"问题{idx}"),
                "answer": "不知道，我前面的内容都没理解，请继续测试我，不要直接讲解。",
            }
            for idx, item in enumerate(first_tool_event.get("questions", []) or [], start=1)
        ],
    ]
    answer_attempts = [answers for answers in answer_attempts if answers]
    require(answer_attempts, "tool_input_required had no questions to answer", first_tool_event)

    second_tool_event: dict[str, Any] | None = None
    second_stream: StreamResult | None = None
    failures: list[dict[str, Any]] = []
    for attempt_idx, answers in enumerate(answer_attempts, start=1):
        second_stream = stream_json(
            httpx_client,
            "/api/chat/tool-response/stream",
            {
                "conversation_id": conversation_id,
                "tool_call_id": first_tool_event["tool_call_id"],
                "answers": answers,
                "model": MODEL,
                "thinking": False,
            },
        )
        try:
            second_tool_event = verify_pause_event_order(second_stream.events, f"chat/tool-response/stream attempt {attempt_idx}")
            break
        except SmokeFailure:
            failures.append(
                {
                    "attempt": attempt_idx,
                    "answers": answers,
                    "events": second_stream.events,
                }
            )
            if attempt_idx < len(answer_attempts):
                log_info(f"tool-response attempt {attempt_idx} did not pause again, retrying with a stronger ambiguous answer set")

    require(second_tool_event is not None and second_stream is not None, "chat/tool-response/stream did not emit tool_input_required", failures)
    require(
        second_tool_event.get("tool_call_id") != first_tool_event.get("tool_call_id"),
        "tool-response second pause reused the original tool_call_id",
        second_stream.events,
    )
    log_ok(f"tool-response/stream pause-again ok, next tool={second_tool_event.get('tool')}")

    history_after = get_json(session, "/api/chat/history", conversation_id=conversation_id)
    pending = history_after.get("tool_input_required")
    require(pending, "history missing pending tool after second pause", history_after)
    require(
        pending.get("tool_call_id") == second_tool_event.get("tool_call_id"),
        "history pending tool_call_id does not match second pause",
        history_after,
    )
    log_ok("history reflects second pending tool")


def main() -> int:
    print(f"Smoke target: {BASE_URL}")
    print(f"Model override: {MODEL or '<server default>'}")
    session = requests.Session()
    session.headers.update({"Content-Type": "application/json"})
    httpx_client = httpx.Client(
        headers={"Content-Type": "application/json"},
        timeout=TIMEOUT,
        follow_redirects=True,
    )
    try:
        verify_server_up(session)
        run_nonstream_flow(session)
        run_tool_stream_flow(session, httpx_client)
        print("\nSMOKE TEST PASSED")
        return 0
    except SmokeFailure:
        return 1
    except requests.RequestException as exc:
        print(f"[FATAL] requests error: {exc}")
        return 1
    except httpx.HTTPError as exc:
        print(f"[FATAL] httpx error: {exc}")
        return 1
    finally:
        httpx_client.close()
        session.close()


if __name__ == "__main__":
    raise SystemExit(main())
