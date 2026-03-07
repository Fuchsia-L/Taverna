# Changelog

## Chat Refactor Stage Update

本阶段完成了 `chat.py` 重构的状态一致性收口，并补齐了自动化与真实接口冒烟验证。

### 已完成的代码改动

- [backend/api/chat.py](/d:/_PROJECTS/Taverna/backend/api/chat.py)
  - 统一流式路径到 `ExecuteResult` 语义，未引入额外 `StreamExecuteResult`
  - 提取流式 finalize helper，明确 `pause` / `success` / `error` / `cancelled` 分支
  - 修正 `error -> done` 事件输出，避免 `finally` 对 `cancelled` 误补 `done`
  - 在 `tool_response` 再次 pause 时刷新新 pending 的 `message_count`
  - 修复 pending 写入时遗漏 `input_request`
  - 修复 `/api/chat/history` 返回的 pending 卡片遗漏 `tool_call_id`

- [backend/api/chat_state_machine.md](/d:/_PROJECTS/Taverna/backend/api/chat_state_machine.md)
  - 补充 `tool_response` pause 后刷新 pending `message_count` 的约束
  - 记录“非流式空 reply 返回 fallback / 流式空 reply 发 `error`+`done`”的协议差异

- [backend/tests/test_chat_refactor.py](/d:/_PROJECTS/Taverna/backend/tests/test_chat_refactor.py)
  - 改成无依赖自举测试，可在缺少后端运行依赖时执行
  - 增加 `P0/P1` 回归覆盖：
    - 非流式 `pause` / `retry` / `rewind` / 空 reply
    - 流式 `pause + done`、`disconnect` 不落库、`error -> done`、空 reply
    - `tool_response` pause 后 pending `message_count` 刷新
    - `/chat/history` 返回完整 `tool_input_required`（含 `tool_call_id`）
    - deferred `plan/advance` 透传保存

- [backend/tests/smoke_test.py](/d:/_PROJECTS/Taverna/backend/tests/smoke_test.py)
  - 新增真实接口冒烟脚本，覆盖：
    - 普通聊天
    - `retry`
    - `rewind`
    - `chat/stream` 触发 `assess/quiz`
    - `tool-response/stream` 再次触发 pause
    - SSE 事件顺序与 history/pending 一致性验证

### 验证结果

- `python -m unittest backend.tests.test_chat_refactor -v` 通过
- `backend/tests/smoke_test.py` 对本地 `http://localhost:8000` 真实接口冒烟通过

### 建议提交信息

```text
refactor(chat): tighten pending/state consistency and add smoke coverage
```

或：

```text
phase-c-d: stabilize chat pending/history semantics and add smoke test
```
