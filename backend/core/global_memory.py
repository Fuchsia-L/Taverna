from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import GlobalMemory


async def get_or_create_global_memory(db: AsyncSession, user_id: str) -> GlobalMemory:
    memory = await db.scalar(select(GlobalMemory).where(GlobalMemory.user_id == user_id))
    if memory:
        return memory
    memory = GlobalMemory(
        user_id=user_id,
        knowledge_graph={"nodes": [], "edges": []},
        learner_profile={},
    )
    db.add(memory)
    await db.commit()
    await db.refresh(memory)
    return memory


async def update_mastery(
    db: AsyncSession,
    user_id: str,
    node_id: str,
    is_correct: bool,
    source: str = "quiz",
) -> dict[str, Any]:
    memory = await get_or_create_global_memory(db, user_id)
    graph = memory.knowledge_graph or {"nodes": [], "edges": []}
    nodes = graph.get("nodes", [])
    node = next((n for n in nodes if n.get("id") == node_id), None)
    now_iso = datetime.now(timezone.utc).isoformat()

    if node is None:
        node = {"id": node_id, "label": node_id, "mastery": 0.0, "last_reviewed": now_iso}
        nodes.append(node)

    mastery = float(node.get("mastery", 0.0))
    if is_correct:
        mastery += (1.0 - mastery) * 0.3
    else:
        mastery -= mastery * 0.2

    mastery = max(0.0, min(1.0, mastery))
    node["mastery"] = mastery
    node["last_reviewed"] = now_iso
    node["source"] = source
    graph["nodes"] = nodes
    memory.knowledge_graph = graph

    await db.commit()
    return node


async def merge_learner_profile(db: AsyncSession, user_id: str, patch: dict[str, Any]) -> dict[str, Any]:
    memory = await get_or_create_global_memory(db, user_id)
    profile = memory.learner_profile or {}
    profile.update(patch or {})
    memory.learner_profile = profile
    await db.commit()
    return profile
