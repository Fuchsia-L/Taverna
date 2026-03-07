import json
import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch


def _install_chat_import_stubs() -> None:
    if "dotenv" not in sys.modules:
        dotenv = types.ModuleType("dotenv")
        dotenv.load_dotenv = lambda *args, **kwargs: None
        sys.modules["dotenv"] = dotenv

    if "fastapi" not in sys.modules:
        fastapi = types.ModuleType("fastapi")

        class APIRouter:
            def post(self, *args, **kwargs):
                def decorator(fn):
                    return fn
                return decorator

            def get(self, *args, **kwargs):
                def decorator(fn):
                    return fn
                return decorator

        class HTTPException(Exception):
            def __init__(self, status_code: int, detail: str):
                super().__init__(detail)
                self.status_code = status_code
                self.detail = detail

        class Request:
            async def is_disconnected(self) -> bool:
                return False

        class Response:
            def __init__(self):
                self.headers = {}

        def Query(default=None, **kwargs):
            return default

        fastapi.APIRouter = APIRouter
        fastapi.HTTPException = HTTPException
        fastapi.Query = Query
        fastapi.Request = Request
        fastapi.Response = Response
        sys.modules["fastapi"] = fastapi

        fastapi_responses = types.ModuleType("fastapi.responses")

        class StreamingResponse:
            def __init__(self, body_iterator, media_type=None, headers=None):
                self.body_iterator = body_iterator
                self.media_type = media_type
                self.headers = headers or {}

        fastapi_responses.StreamingResponse = StreamingResponse
        sys.modules["fastapi.responses"] = fastapi_responses

    if "api.chat_formatting" not in sys.modules:
        formatting = types.ModuleType("api.chat_formatting")
        formatting.build_tool_student_text = lambda tool_trace: ""
        formatting.format_answers = lambda tool_name, answers: json.dumps(
            {"status": "ok", "student_answers": answers},
            ensure_ascii=False,
        )
        formatting.format_answers_readable = lambda tool_name, answers: f"{tool_name}:{len(answers)}"
        sys.modules["api.chat_formatting"] = formatting

    if "core.conversation" not in sys.modules:
        conversation = types.ModuleType("core.conversation")
        conversation.add_message = AsyncMock()
        conversation.check_and_compress = AsyncMock()
        conversation.clear_history = AsyncMock()
        conversation.ensure_session = AsyncMock(side_effect=lambda cid=None: cid or "conv-1")
        conversation.get_current_user_turn = AsyncMock(return_value=1)
        conversation.get_debug_turns = Mock(return_value=[])
        conversation.get_history = AsyncMock(return_value=[])
        conversation.get_message_count = AsyncMock(return_value=0)
        conversation.get_recent_messages = AsyncMock(return_value=[])
        conversation.get_summary = AsyncMock(return_value="")
        conversation.preview_rewind_to_user_turn = AsyncMock(return_value=("", [], AsyncMock()))
        conversation.record_debug_request = Mock()
        conversation.record_debug_response = Mock()
        conversation.remove_last_assistant = AsyncMock()
        sys.modules["core.conversation"] = conversation

    if "core.concurrency" not in sys.modules:
        concurrency = types.ModuleType("core.concurrency")

        class _Guard:
            async def __aenter__(self):
                return None

            async def __aexit__(self, exc_type, exc, tb):
                return False

        concurrency.conversation_guard = lambda conversation_id: _Guard()
        sys.modules["core.concurrency"] = concurrency

    if "core.pending_tools" not in sys.modules:
        pending_tools = types.ModuleType("core.pending_tools")
        pending_tools.clear_pending_tool = AsyncMock()
        pending_tools.get_pending_tool = AsyncMock(return_value=None)
        pending_tools.set_pending_tool = AsyncMock()
        sys.modules["core.pending_tools"] = pending_tools

    if "core.providers" not in sys.modules:
        providers = types.ModuleType("core.providers")
        providers.get_sync_client_for_model = Mock()
        providers.get_thinking_extra_params = Mock(return_value=None)
        providers.resolve_model = Mock(side_effect=lambda model=None: model or "test-model")
        sys.modules["core.providers"] = providers

    if "core.teaching_engine" not in sys.modules:
        teaching_engine = types.ModuleType("core.teaching_engine")
        teaching_engine.advance_phase = AsyncMock()
        teaching_engine.clear_plan = AsyncMock()
        teaching_engine.get_plan = AsyncMock(return_value=None)
        teaching_engine.get_teaching_context = AsyncMock(return_value={})
        teaching_engine.set_plan = AsyncMock()
        teaching_engine.update_plan = AsyncMock()
        sys.modules["core.teaching_engine"] = teaching_engine

    if "core.token_utils" not in sys.modules:
        token_utils = types.ModuleType("core.token_utils")
        token_utils.estimate_messages_tokens = lambda messages: len(messages or [])
        sys.modules["core.token_utils"] = token_utils

    if "core.tool_definitions" not in sys.modules:
        tool_definitions = types.ModuleType("core.tool_definitions")
        tool_definitions.TEACHING_TOOLS = []
        sys.modules["core.tool_definitions"] = tool_definitions

    if "core.tool_handler" not in sys.modules:
        tool_handler = types.ModuleType("core.tool_handler")
        tool_handler.handle_tool_call = AsyncMock(
            return_value={"requires_input": False, "result": json.dumps({"status": "ok"}, ensure_ascii=False)}
        )
        tool_handler._try_autoname_conversation = AsyncMock()
        tool_handler._try_autoname_project = AsyncMock()
        sys.modules["core.tool_handler"] = tool_handler

    if "models.chat" not in sys.modules:
        models_chat = types.ModuleType("models.chat")

        class _Base:
            def __init__(self, **kwargs):
                for key, value in kwargs.items():
                    setattr(self, key, value)

        class ChatRequest(_Base):
            def __init__(self, message: str, images=None, model=None, conversation_id=None, thinking=False):
                super().__init__(
                    message=message,
                    images=images or [],
                    model=model,
                    conversation_id=conversation_id,
                    thinking=thinking,
                )

        class RetryRequest(_Base):
            def __init__(self, conversation_id: str, model=None, thinking=False):
                super().__init__(conversation_id=conversation_id, model=model, thinking=thinking)

        class RewindRequest(_Base):
            def __init__(
                self,
                conversation_id: str,
                target_user_turn: int,
                replacement_message: str,
                images=None,
                model=None,
                thinking=False,
            ):
                super().__init__(
                    conversation_id=conversation_id,
                    target_user_turn=target_user_turn,
                    replacement_message=replacement_message,
                    images=images or [],
                    model=model,
                    thinking=thinking,
                )

        class ToolResponseRequest(_Base):
            def __init__(self, conversation_id: str, tool_call_id: str, answers, model=None, thinking=False):
                super().__init__(
                    conversation_id=conversation_id,
                    tool_call_id=tool_call_id,
                    answers=answers,
                    model=model,
                    thinking=thinking,
                )

        class ChatResponse(_Base):
            pass

        class ChatOrToolResponse(_Base):
            def __init__(self, reply: str, tool_input_required=None, plan_card=None):
                super().__init__(reply=reply, tool_input_required=tool_input_required, plan_card=plan_card)

        models_chat.ChatRequest = ChatRequest
        models_chat.RetryRequest = RetryRequest
        models_chat.RewindRequest = RewindRequest
        models_chat.ToolResponseRequest = ToolResponseRequest
        models_chat.ChatResponse = ChatResponse
        models_chat.ChatOrToolResponse = ChatOrToolResponse
        sys.modules["models.chat"] = models_chat

    if "prompts.template" not in sys.modules:
        template = types.ModuleType("prompts.template")
        template.build_system_prompt = lambda **kwargs: "system-prompt"
        template.load_soul = lambda path: "teacher-soul"
        sys.modules["prompts.template"] = template


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
_install_chat_import_stubs()

from api import chat  # noqa: E402


class FakeRequest:
    def __init__(self, disconnected: bool = False):
        self.disconnected = disconnected

    async def is_disconnected(self) -> bool:
        return self.disconnected


class FlipDisconnectRequest:
    def __init__(self, flip_after: int):
        self.calls = 0
        self.flip_after = flip_after

    async def is_disconnected(self) -> bool:
        self.calls += 1
        return self.calls >= self.flip_after


class FakeChunk:
    def __init__(self, content=None, reasoning=None, tool_calls=None, raw=None):
        self.choices = [
            SimpleNamespace(
                delta=SimpleNamespace(
                    content=content,
                    reasoning=reasoning,
                    tool_calls=tool_calls,
                )
            )
        ]
        self._raw = raw or {"choices": [{"delta": {}}]}

    def model_dump(self):
        return self._raw


def decode_event(event: str) -> dict:
    payload = event.removeprefix("data: ").strip()
    return json.loads(payload)


class ChatRefactorTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        for module_name in ("core.conversation", "core.pending_tools", "core.teaching_engine", "core.tool_handler"):
            module = sys.modules.get(module_name)
            if not module:
                continue
            for attr_name in dir(module):
                attr = getattr(module, attr_name)
                if hasattr(attr, "reset_mock"):
                    attr.reset_mock()

    async def test_prepare_retry_context_defers_removal_until_commit(self):
        with patch.object(chat, "_build_messages", AsyncMock(return_value=[{"role": "system"}, {"role": "user"}, {"role": "assistant"}])), \
             patch.object(chat, "remove_last_assistant", AsyncMock()) as remove_last_assistant:
            messages, commit = await chat._prepare_retry_context("conv-1")
            self.assertEqual(messages, [{"role": "system"}, {"role": "user"}])
            remove_last_assistant.assert_not_awaited()
            await commit()
            remove_last_assistant.assert_awaited_once_with("conv-1")

    async def test_prepare_rewind_context_uses_preview_without_mutating(self):
        commit = AsyncMock()
        with patch.object(chat, "preview_rewind_to_user_turn", AsyncMock(return_value=("sum", [{"role": "user", "content": "x"}], commit))), \
             patch.object(chat, "_build_messages_with_context", AsyncMock(return_value=[{"role": "system"}])) as build_messages:
            messages, returned_commit = await chat._prepare_rewind_context(
                "conv-1",
                SimpleNamespace(replacement_message="hello", images=[], target_user_turn=1),
            )

        self.assertEqual(messages, [{"role": "system"}])
        build_messages.assert_awaited_once_with("conv-1", "sum", [{"role": "user", "content": "x"}])
        commit.assert_not_awaited()
        await returned_commit()
        commit.assert_awaited_once()

    async def test_prepare_tool_response_turn_pause_and_success_commits(self):
        pending = {
            "tool_name": "quiz",
            "messages": [{"role": "assistant", "content": "q"}],
            "deferred_plan": {"mode": "set"},
            "deferred_advance": {"completed_phase": 1},
        }
        req = SimpleNamespace(tool_call_id="tool-1", answers=[{"question": "Q", "answer": "A"}])
        with patch.object(chat, "get_current_user_turn", AsyncMock(return_value=3)), \
             patch.object(chat, "add_message", AsyncMock()) as add_message, \
             patch.object(chat, "clear_pending_tool", AsyncMock()) as clear_pending:
            prepared = await chat._prepare_tool_response_turn("conv-1", req, pending)
            await prepared.commit_on_pause()
            await prepared.commit_on_success()

        self.assertEqual(prepared.deferred_plan, {"mode": "set"})
        self.assertEqual(prepared.deferred_advance, {"completed_phase": 1})
        self.assertEqual(add_message.await_count, 2)
        clear_pending.assert_awaited_once_with("conv-1")

    async def test_prepare_tool_response_turn_pause_refreshes_new_pending_message_count(self):
        pending = {
            "tool_name": "quiz",
            "messages": [{"role": "assistant", "content": "q"}],
        }
        req = SimpleNamespace(tool_call_id="tool-1", answers=[{"question": "Q", "answer": "A"}])
        refreshed_pending = {
            "tool_call_id": "tool-2",
            "tool_name": "quiz",
            "messages": [{"role": "assistant", "content": "next"}],
            "input_request": {"tool": "quiz"},
            "deferred_plan": {"mode": "set"},
            "deferred_advance": {"completed_phase": 1},
        }
        with patch.object(chat, "get_current_user_turn", AsyncMock(return_value=3)), \
             patch.object(chat, "add_message", AsyncMock()) as add_message, \
             patch.object(chat, "get_pending_tool", AsyncMock(return_value=refreshed_pending)), \
             patch.object(chat, "get_message_count", AsyncMock(return_value=9)), \
             patch.object(chat, "set_pending_tool", AsyncMock()) as set_pending:
            prepared = await chat._prepare_tool_response_turn("conv-1", req, pending)
            await prepared.commit_on_pause()

        add_message.assert_awaited_once()
        set_pending.assert_awaited_once_with(
            conversation_id="conv-1",
            tool_call_id="tool-2",
            tool_name="quiz",
            messages_snapshot=[{"role": "assistant", "content": "next"}],
            input_request={"tool": "quiz"},
            message_count=9,
            deferred_plan={"mode": "set"},
            deferred_advance={"completed_phase": 1},
        )

    async def test_prepare_retry_turn_prefers_pending_snapshot(self):
        pending = {
            "messages": [{"role": "assistant", "content": "pending-snapshot"}],
            "deferred_plan": {"mode": "set"},
            "deferred_advance": {"completed_phase": 1},
        }
        with patch.object(chat, "get_pending_tool", AsyncMock(return_value=pending)), \
             patch.object(chat, "get_current_user_turn", AsyncMock(return_value=5)), \
             patch.object(chat, "_prepare_retry_context", AsyncMock()) as prepare_retry_context, \
             patch.object(chat, "clear_pending_tool", AsyncMock()) as clear_pending:
            prepared = await chat._prepare_retry_turn("conv-1")
            await prepared.commit_on_success()

        self.assertEqual(prepared.messages, [{"role": "assistant", "content": "pending-snapshot"}])
        self.assertEqual(prepared.deferred_plan, {"mode": "set"})
        self.assertEqual(prepared.deferred_advance, {"completed_phase": 1})
        prepare_retry_context.assert_not_awaited()
        clear_pending.assert_awaited_once_with("conv-1")

    async def test_run_nonstream_turn_paused_uses_pause_commit(self):
        prepared = chat.PreparedTurn(
            messages=[{"role": "user", "content": "x"}],
            turn_index=2,
            commit_on_pause=AsyncMock(),
            commit_on_success=AsyncMock(),
        )
        result = chat.ExecuteResult(
            status="paused",
            reply="partial",
            pending={"tool": "quiz"},
        )
        with patch.object(chat, "record_debug_request", Mock()), \
             patch.object(chat, "_execute_nonstream_turn", AsyncMock(return_value=result)):
            response = await chat._run_nonstream_turn(prepared, "model", "conv-1", False)

        self.assertEqual(response.reply, "partial")
        self.assertEqual(response.tool_input_required, {"tool": "quiz"})
        prepared.commit_on_pause.assert_awaited_once()
        prepared.commit_on_success.assert_not_awaited()

    async def test_run_nonstream_turn_success_commits_and_saves(self):
        prepared = chat.PreparedTurn(
            messages=[{"role": "user", "content": "x"}],
            turn_index=2,
            commit_on_pause=AsyncMock(),
            commit_on_success=AsyncMock(),
        )
        result = chat.ExecuteResult(
            status="done",
            reply="final",
            raw={"final_response": {"id": "1"}},
            tool_trace=[],
        )
        with patch.object(chat, "record_debug_request", Mock()), \
             patch.object(chat, "_execute_nonstream_turn", AsyncMock(return_value=result)), \
             patch.object(chat, "_save_assistant_and_debug", AsyncMock()) as save:
            response = await chat._run_nonstream_turn(prepared, "model", "conv-1", False)

        self.assertEqual(response.reply, "final")
        prepared.commit_on_success.assert_awaited_once()
        save.assert_awaited_once()

    async def test_nonstream_chat_pause_does_not_save_assistant(self):
        req = chat.ChatRequest(message="hello", conversation_id="conv-1")
        response = SimpleNamespace(headers={})
        paused_result = chat.ExecuteResult(status="paused", reply="", pending={"tool": "quiz"})
        with patch.object(chat, "ensure_session", AsyncMock(return_value="conv-1")), \
             patch.object(chat, "_resolve_model", Mock(return_value="model")), \
             patch.object(chat, "_prepare_chat_turn", AsyncMock(return_value=chat.PreparedTurn(messages=[], turn_index=1))), \
             patch.object(chat, "_execute_nonstream_turn", AsyncMock(return_value=paused_result)), \
             patch.object(chat, "_save_assistant_and_debug", AsyncMock()) as save:
            result = await chat.chat(req, response)

        self.assertEqual(result.tool_input_required, {"tool": "quiz"})
        save.assert_not_awaited()

    async def test_run_nonstream_turn_empty_reply_returns_fallback_without_save(self):
        prepared = chat.PreparedTurn(
            messages=[{"role": "user", "content": "x"}],
            turn_index=2,
            commit_on_pause=AsyncMock(),
            commit_on_success=AsyncMock(),
        )
        result = chat.ExecuteResult(status="done", reply="   ")
        with patch.object(chat, "record_debug_request", Mock()), \
             patch.object(chat, "_execute_nonstream_turn", AsyncMock(return_value=result)), \
             patch.object(chat, "_save_assistant_and_debug", AsyncMock()) as save:
            response = await chat._run_nonstream_turn(prepared, "model", "conv-1", False)

        self.assertEqual(response.reply, chat._FALLBACK_REPLY)
        prepared.commit_on_success.assert_not_awaited()
        save.assert_not_awaited()

    async def test_nonstream_tool_response_success_clears_pending_and_persists_answers(self):
        req = chat.ToolResponseRequest(
            conversation_id="conv-1",
            tool_call_id="tool-1",
            answers=[{"question": "Q", "answer": "A"}],
        )
        response = SimpleNamespace(headers={})
        pending = {"tool_call_id": "tool-1", "message_count": 2}
        prepared = chat.PreparedTurn(messages=[], turn_index=2, commit_on_pause=AsyncMock(), commit_on_success=AsyncMock())
        success_result = chat.ExecuteResult(status="done", reply="ok")
        with patch.object(chat, "ensure_session", AsyncMock(return_value="conv-1")), \
             patch.object(chat, "get_pending_tool", AsyncMock(return_value=pending)), \
             patch.object(chat, "get_message_count", AsyncMock(return_value=2)), \
             patch.object(chat, "_resolve_model", Mock(return_value="model")), \
             patch.object(chat, "_prepare_tool_response_turn", AsyncMock(return_value=prepared)), \
             patch.object(chat, "_execute_nonstream_turn", AsyncMock(return_value=success_result)), \
             patch.object(chat, "_save_assistant_and_debug", AsyncMock()):
            result = await chat.tool_response(req, response)

        self.assertEqual(result.reply, "ok")
        prepared.commit_on_success.assert_awaited_once()

    async def test_nonstream_retry_failure_does_not_remove_old_assistant(self):
        req = chat.RetryRequest(conversation_id="conv-1")
        response = SimpleNamespace(headers={})
        retry_commit = AsyncMock(side_effect=RuntimeError("commit should not run"))
        prepared = chat.PreparedTurn(messages=[], turn_index=2, commit_on_pause=retry_commit, commit_on_success=retry_commit)
        with patch.object(chat, "ensure_session", AsyncMock(return_value="conv-1")), \
             patch.object(chat, "_resolve_model", Mock(return_value="model")), \
             patch.object(chat, "_prepare_retry_turn", AsyncMock(return_value=prepared)), \
             patch.object(chat, "_execute_nonstream_turn", AsyncMock(side_effect=RuntimeError("boom"))):
            result = await chat.retry(req, response)

        self.assertEqual(result.reply, chat._FALLBACK_REPLY)
        retry_commit.assert_not_awaited()

    async def test_nonstream_rewind_failure_does_not_mutate_history(self):
        req = chat.RewindRequest(conversation_id="conv-1", target_user_turn=1, replacement_message="new")
        response = SimpleNamespace(headers={})
        rewind_commit = AsyncMock(side_effect=RuntimeError("commit should not run"))
        prepared = chat.PreparedTurn(messages=[], turn_index=1, commit_on_pause=rewind_commit, commit_on_success=rewind_commit)
        with patch.object(chat, "ensure_session", AsyncMock(return_value="conv-1")), \
             patch.object(chat, "_resolve_model", Mock(return_value="model")), \
             patch.object(chat, "_prepare_rewind_turn", AsyncMock(return_value=prepared)), \
             patch.object(chat, "_execute_nonstream_turn", AsyncMock(side_effect=RuntimeError("boom"))):
            result = await chat.rewind(req, response)

        self.assertEqual(result.reply, chat._FALLBACK_REPLY)
        rewind_commit.assert_not_awaited()

    async def test_run_stream_turn_paused_uses_pause_commit(self):
        prepared = chat.PreparedTurn(
            messages=[{"role": "user", "content": "x"}],
            turn_index=1,
            commit_on_pause=AsyncMock(),
            commit_on_success=AsyncMock(),
        )
        request = FakeRequest()
        with patch.object(chat, "record_debug_request", Mock()), \
             patch.object(chat, "_create_stream_completion", Mock(return_value=(iter([FakeChunk()]), True))), \
             patch.object(chat, "_extract_tool_deltas", Mock(return_value=[{"index": 0, "id": "tool-1", "name": "quiz", "arguments": "{}"}])), \
             patch.object(chat, "_process_stream_tool_calls", AsyncMock(return_value=([], {"tool": "quiz"}, False, None, None))):
            events = [event async for event in chat._run_stream_turn(prepared, "model", "conv-1", False, request)]

        decoded = [decode_event(event) for event in events]
        self.assertEqual([decoded[-2]["type"], decoded[-1]["type"]], ["tool_input_required", "done"])
        self.assertTrue(decoded[-1]["paused"])
        prepared.commit_on_pause.assert_awaited_once()
        prepared.commit_on_success.assert_not_awaited()

    async def test_run_stream_turn_success_commits_and_saves(self):
        prepared = chat.PreparedTurn(
            messages=[{"role": "user", "content": "x"}],
            turn_index=1,
            commit_on_pause=AsyncMock(),
            commit_on_success=AsyncMock(),
        )
        request = FakeRequest()
        with patch.object(chat, "record_debug_request", Mock()), \
             patch.object(chat, "_create_stream_completion", Mock(return_value=(iter([FakeChunk("hello")]), True))), \
             patch.object(chat, "_extract_tool_deltas", Mock(return_value=[])), \
             patch.object(chat, "_save_assistant_and_debug", AsyncMock()) as save:
            events = [
                event async for event in chat._run_stream_turn(
                    prepared,
                    "model",
                    "conv-1",
                    False,
                    request,
                    emit_raw_final=True,
                )
            ]

        decoded = [decode_event(event) for event in events]
        self.assertTrue(any(event.get("type") == "done" for event in decoded))
        self.assertTrue(any(event.get("kind") == "final" for event in decoded))
        prepared.commit_on_success.assert_awaited_once()
        save.assert_awaited_once()

    async def test_stream_disconnect_does_not_save_partial_assistant(self):
        prepared = chat.PreparedTurn(
            messages=[{"role": "user", "content": "x"}],
            turn_index=1,
            commit_on_pause=AsyncMock(),
            commit_on_success=AsyncMock(),
        )
        request = FlipDisconnectRequest(flip_after=2)
        with patch.object(chat, "record_debug_request", Mock()), \
             patch.object(chat, "_create_stream_completion", Mock(return_value=(iter([FakeChunk("hello")]), True))), \
             patch.object(chat, "_extract_tool_deltas", Mock(return_value=[])), \
             patch.object(chat, "_save_assistant_and_debug", AsyncMock()) as save:
            events = [event async for event in chat._run_stream_turn(prepared, "model", "conv-1", False, request)]

        self.assertEqual(events, [])
        save.assert_not_awaited()
        prepared.commit_on_success.assert_not_awaited()

    async def test_stream_error_emits_done_without_falling_back_to_finally(self):
        prepared = chat.PreparedTurn(messages=[{"role": "user", "content": "x"}], turn_index=1)
        request = FakeRequest()
        with patch.object(chat, "record_debug_request", Mock()), \
             patch.object(chat, "_create_stream_completion", Mock(side_effect=RuntimeError("boom"))), \
             patch.object(chat, "record_debug_response", Mock()):
            events = [event async for event in chat._run_stream_turn(prepared, "model", "conv-1", False, request)]

        decoded = [decode_event(event) for event in events]
        self.assertEqual([event["type"] for event in decoded], ["error", "done"])

    async def test_stream_empty_reply_emits_error_then_done_without_save(self):
        prepared = chat.PreparedTurn(messages=[{"role": "user", "content": "x"}], turn_index=1)
        request = FakeRequest()
        with patch.object(chat, "record_debug_request", Mock()), \
             patch.object(chat, "_create_stream_completion", Mock(return_value=(iter([FakeChunk()]), True))), \
             patch.object(chat, "_extract_tool_deltas", Mock(return_value=[])), \
             patch.object(chat, "_save_assistant_and_debug", AsyncMock()) as save:
            events = [event async for event in chat._run_stream_turn(prepared, "model", "conv-1", False, request)]

        decoded = [decode_event(event) for event in events]
        self.assertEqual([event["type"] for event in decoded], ["error", "done"])
        self.assertEqual(decoded[0]["message"], chat._FALLBACK_REPLY)
        save.assert_not_awaited()

    async def test_tool_response_stream_pause_keeps_pending_and_done_semantics(self):
        req = chat.ToolResponseRequest(conversation_id="conv-1", tool_call_id="tool-1", answers=[])
        pending = {"tool_call_id": "tool-1", "message_count": 2}
        prepared = chat.PreparedTurn(messages=[], turn_index=1)
        with patch.object(chat, "ensure_session", AsyncMock(return_value="conv-1")), \
             patch.object(chat, "get_pending_tool", AsyncMock(return_value=pending)), \
             patch.object(chat, "get_message_count", AsyncMock(return_value=2)), \
             patch.object(chat, "_resolve_model", Mock(return_value="model")), \
             patch.object(chat, "_prepare_tool_response_turn", AsyncMock(return_value=prepared)), \
             patch.object(chat, "_run_stream_turn", Mock(return_value=_async_iter([
                 chat._sse_payload({"type": "tool_input_required", "tool": "quiz"}),
                 chat._sse_payload({"type": "done", "paused": True, "conversation_id": "conv-1"}),
             ]))), \
             patch.object(chat, "clear_pending_tool", AsyncMock()) as clear_pending:
            streaming = await chat.tool_response_stream(req, FakeRequest())
            events = [event async for event in streaming.body_iterator]

        decoded = [decode_event(event) for event in events]
        self.assertEqual(decoded[-1]["type"], "done")
        self.assertTrue(decoded[-1]["paused"])
        clear_pending.assert_not_awaited()

    async def test_chat_history_returns_pending_tool_input(self):
        pending = {
            "tool_call_id": "tool-1",
            "input_request": {
                "tool": "assess",
                "questions": [{"question": "Q1"}],
            }
        }
        with patch.object(chat, "get_history", AsyncMock(return_value=[{"role": "user", "content": "hi"}])), \
             patch.object(chat, "get_pending_tool", AsyncMock(return_value=pending)):
            result = await chat.chat_history(conversation_id="123e4567-e89b-12d3-a456-426614174000")

        self.assertEqual(result["conversation_id"], "123e4567-e89b-12d3-a456-426614174000")
        self.assertEqual(
            result["tool_input_required"],
            {
                **pending["input_request"],
                "tool_call_id": "tool-1",
            },
        )

    async def test_deferred_values_are_saved_from_execute_result(self):
        prepared = chat.PreparedTurn(messages=[{"role": "user", "content": "x"}], turn_index=1)
        result = chat.ExecuteResult(
            status="done",
            reply="final",
            deferred_plan={"mode": "set"},
            deferred_advance={"completed_phase": 1},
        )
        with patch.object(chat, "record_debug_request", Mock()), \
             patch.object(chat, "_execute_nonstream_turn", AsyncMock(return_value=result)), \
             patch.object(chat, "_save_assistant_and_debug", AsyncMock()) as save:
            await chat._run_nonstream_turn(prepared, "model", "conv-1", False)

        _, kwargs = save.await_args
        self.assertEqual(kwargs["deferred_plan"], {"mode": "set"})
        self.assertEqual(kwargs["deferred_advance"], {"completed_phase": 1})


def _async_iter(items):
    async def gen():
        for item in items:
            yield item

    return gen()


if __name__ == "__main__":
    unittest.main()
