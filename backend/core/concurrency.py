from __future__ import annotations

import asyncio
from collections import defaultdict
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

_lock_registry_guard = asyncio.Lock()
_conversation_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)


async def _get_conversation_lock(conversation_id: str) -> asyncio.Lock:
    async with _lock_registry_guard:
        return _conversation_locks[conversation_id]


@asynccontextmanager
async def conversation_guard(conversation_id: str) -> AsyncIterator[None]:
    lock = await _get_conversation_lock(conversation_id)
    async with lock:
        yield
