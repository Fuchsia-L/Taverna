# Chat State Machine Baseline

This document records the intended `prepare / execute / finalize` behavior for
the 8 chat endpoints before and during the refactor. It exists as a review
baseline so repeated logic can be collapsed without changing commit timing.

## Non-streaming

| Endpoint | Prepare | Pause Commit | Success Commit | Finalize |
| --- | --- | --- | --- | --- |
| `POST /chat` | Clear pending, persist user, build messages | None | None | Save assistant, debug, deferred, compression |
| `POST /chat/tool-response` | Validate pending, build messages from snapshot + tool answer | Persist readable user answers only | Clear pending, persist readable user answers | Save assistant, debug, deferred, compression |
| `POST /chat/retry` | Preview retry in memory | Remove last assistant | Remove last assistant | Save assistant, debug, deferred, compression |
| `POST /chat/rewind` | Preview rewind in memory | Commit rewind + replacement user | Commit rewind + replacement user | Save assistant, debug, deferred, compression |

## Streaming

| Endpoint | Prepare | Pause Commit | Success Commit | Finalize |
| --- | --- | --- | --- | --- |
| `POST /chat/stream` | Clear pending, persist user, build messages | None | None | Save assistant, debug, deferred, compression, emit `done` |
| `POST /chat/tool-response/stream` | Validate pending, build messages from snapshot + tool answer | Persist readable user answers only | Clear pending, persist readable user answers | Save assistant, debug, deferred, compression, emit `done` |
| `POST /chat/retry/stream` | Preview retry in memory | Remove last assistant | Remove last assistant | Save assistant, debug, deferred, compression, emit `done` |
| `POST /chat/rewind/stream` | Preview rewind in memory | Commit rewind + replacement user | Commit rewind + replacement user | Save assistant, debug, deferred, compression, emit `done` |

## Shared Invariants

- `pause` is an execute-path exit, not a standalone phase.
- `retry` and `rewind` must not mutate DB state during prepare; mutation happens only on pause/success commit.
- `tool-response` pause branches must keep the newly upserted pending tool state.
- `tool-response` pause branches persist readable user answers, then refresh the new pending snapshot `message_count` so later resume/retry validation stays aligned.
- Streaming cancel/disconnect paths must not persist partial assistant replies.
- Deferred `plan` / `advance` state must survive pause/resume through pending snapshots.
- Empty assistant reply is a documented protocol difference: non-streaming returns `_FALLBACK_REPLY` in `reply`, while streaming emits `error` and then `done`; neither path persists an assistant message.
