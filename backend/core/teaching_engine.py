"""
Teaching plan persistence backed by PostgreSQL.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from db.models import TeachingPlan as TeachingPlanModel
from db.session import SessionLocal


@dataclass
class Phase:
    id: int
    title: str
    objective: str
    status: str = "pending"
    summary: Optional[str] = None


@dataclass
class TeachingPlan:
    goal: str
    phases: list[Phase] = field(default_factory=list)
    current_phase_id: Optional[int] = None


def _serialize_plan(plan: TeachingPlan) -> dict:
    return {
        "goal": plan.goal,
        "current_phase_id": plan.current_phase_id,
        "phases": [
            {
                "id": phase.id,
                "title": phase.title,
                "objective": phase.objective,
                "status": phase.status,
                "summary": phase.summary,
            }
            for phase in plan.phases
        ],
    }


def _deserialize_plan(payload: dict | None) -> Optional[TeachingPlan]:
    if not isinstance(payload, dict):
        return None
    phases_raw = payload.get("phases", [])
    phases: list[Phase] = []
    if isinstance(phases_raw, list):
        for item in phases_raw:
            if not isinstance(item, dict):
                continue
            phases.append(
                Phase(
                    id=int(item.get("id", 0)),
                    title=str(item.get("title", "")),
                    objective=str(item.get("objective", "")),
                    status=str(item.get("status", "pending")),
                    summary=item.get("summary"),
                )
            )
    return TeachingPlan(
        goal=str(payload.get("goal", "")),
        phases=phases,
        current_phase_id=payload.get("current_phase_id"),
    )


async def get_plan(conversation_id: str = "default") -> Optional[TeachingPlan]:
    try:
        conv_uuid = UUID(conversation_id)
    except ValueError:
        return None
    async with SessionLocal() as db:
        row = await db.scalar(select(TeachingPlanModel).where(TeachingPlanModel.conversation_id == conv_uuid))
        if not row:
            return None
        return _deserialize_plan(row.plan)


async def _upsert_plan(conversation_id: str, plan: TeachingPlan) -> TeachingPlan:
    conv_uuid = UUID(conversation_id)
    async with SessionLocal() as db:
        row = await db.scalar(select(TeachingPlanModel).where(TeachingPlanModel.conversation_id == conv_uuid))
        payload = _serialize_plan(plan)
        if row:
            row.plan = payload
        else:
            db.add(TeachingPlanModel(conversation_id=conv_uuid, plan=payload))
        try:
            await db.commit()
        except IntegrityError:
            await db.rollback()
            existing = await db.scalar(select(TeachingPlanModel).where(TeachingPlanModel.conversation_id == conv_uuid))
            if existing:
                existing.plan = payload
                await db.commit()
            else:
                raise
    return plan


async def set_plan(conversation_id: str, goal: str, phases: list[dict]) -> TeachingPlan:
    plan = TeachingPlan(
        goal=goal,
        phases=[Phase(id=p["id"], title=p["title"], objective=p["objective"]) for p in phases],
        current_phase_id=phases[0]["id"] if phases else None,
    )
    if plan.phases:
        plan.phases[0].status = "active"
    return await _upsert_plan(conversation_id, plan)


async def update_plan(conversation_id: str, goal: str, phases: list[dict]) -> TeachingPlan:
    old_plan = await get_plan(conversation_id)
    old_summaries: dict[int, str] = {}
    if old_plan:
        for phase in old_plan.phases:
            if phase.status == "completed" and phase.summary:
                old_summaries[phase.id] = phase.summary

    new_plan = TeachingPlan(
        goal=goal,
        phases=[Phase(id=p["id"], title=p["title"], objective=p["objective"]) for p in phases],
    )

    for phase in new_plan.phases:
        if phase.id in old_summaries:
            phase.status = "completed"
            phase.summary = old_summaries[phase.id]

    for phase in new_plan.phases:
        if phase.status != "completed":
            phase.status = "active"
            new_plan.current_phase_id = phase.id
            break

    return await _upsert_plan(conversation_id, new_plan)


async def advance_phase(
    conversation_id: str,
    completed_phase_id: int,
    summary: str,
    move_to: int,
) -> dict:
    plan = await get_plan(conversation_id)
    if not plan:
        return {"success": False, "error": "No teaching plan exists"}

    completed_found = False
    for phase in plan.phases:
        if phase.id == completed_phase_id:
            phase.status = "completed"
            phase.summary = summary
            completed_found = True
            break

    if not completed_found:
        return {"success": False, "error": f"Phase {completed_phase_id} not found"}

    next_found = False
    for phase in plan.phases:
        if phase.id == move_to:
            phase.status = "active"
            plan.current_phase_id = move_to
            next_found = True
            break

    if not next_found:
        plan.current_phase_id = None

    await _upsert_plan(conversation_id, plan)

    return {
        "success": True,
        "completed": completed_phase_id,
        "now_active": move_to if next_found else None,
        "all_complete": all(p.status == "completed" for p in plan.phases),
    }


async def get_teaching_context(conversation_id: str = "default") -> str:
    plan = await get_plan(conversation_id)
    if not plan:
        return ""

    lines = []
    lines.append("【教学计划】")
    lines.append(f"目标：{plan.goal}")
    lines.append("")

    status_map = {"pending": "未开始", "active": "进行中", "completed": "已完成"}
    for phase in plan.phases:
        status_label = status_map.get(phase.status, phase.status)
        line = f"阶段{phase.id}（{status_label}）：{phase.title}"
        if phase.objective:
            line += f" — 目标：{phase.objective}"
        lines.append(line)

    completed = [p for p in plan.phases if p.status == "completed" and p.summary]
    if completed:
        lines.append("")
        lines.append("【已完成阶段摘要】")
        for phase in completed:
            lines.append(f"阶段{phase.id}（{phase.title}）：{phase.summary}")

    if plan.current_phase_id:
        current = next((p for p in plan.phases if p.id == plan.current_phase_id), None)
        if current:
            lines.append("")
            lines.append(f"【当前阶段】阶段{current.id}：{current.title}")
            lines.append(f"本阶段目标：{current.objective}")

    return "\n".join(lines)


async def clear_plan(conversation_id: str = "default"):
    try:
        conv_uuid = UUID(conversation_id)
    except ValueError:
        return
    async with SessionLocal() as db:
        await db.execute(delete(TeachingPlanModel).where(TeachingPlanModel.conversation_id == conv_uuid))
        await db.commit()
