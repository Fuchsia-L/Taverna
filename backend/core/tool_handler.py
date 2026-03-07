"""
Processes tool calls returned by the AI and routes them
to the appropriate teaching engine functions.
"""

from __future__ import annotations

import ast
import json
from uuid import UUID

from sqlalchemy import select

from core.teaching_engine import get_plan
from db.models import Conversation, ConversationType, Project
from db.session import SessionLocal

INTERACTIVE_TOOLS = {"assess", "quiz"}
AUTO_TOOLS = {"plan", "advance"}
UNTITLED_PROJECT_NAME = "untitled"


def is_interactive_tool(tool_name: str) -> bool:
    return tool_name in INTERACTIVE_TOOLS


def _normalize_questions(raw: object) -> list[dict]:
    def _from_obj(value: object) -> list[dict]:
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
        if isinstance(value, dict):
            return [value]
        return []

    if isinstance(raw, list):
        return [item for item in raw if isinstance(item, dict)]
    if isinstance(raw, dict):
        return [raw]
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return []
        probe: object = text

        # Try nested JSON decoding first (some providers double-encode fields).
        for _ in range(3):
            if not isinstance(probe, str):
                break
            probe_text = probe.strip()
            if not probe_text:
                return []
            try:
                probe = json.loads(probe_text)
                continue
            except Exception:
                break

        normalized = _from_obj(probe)
        if normalized:
            return normalized

        # Fallback for Python-literal style strings from non-strict tool outputs.
        if isinstance(probe, str):
            try:
                literal = ast.literal_eval(probe)
            except Exception:
                return []
            normalized = _from_obj(literal)
            if normalized:
                return normalized
    return []


async def _resolve_conversation_type(conversation_id: str | None) -> str:
    if not conversation_id:
        return ConversationType.standalone.value
    try:
        conv_uuid = UUID(conversation_id)
    except ValueError:
        return ConversationType.standalone.value
    async with SessionLocal() as db:
        conversation = await db.scalar(select(Conversation).where(Conversation.id == conv_uuid))
        if not conversation:
            return ConversationType.standalone.value
        return conversation.type.value


async def _try_autoname_project(conversation_id: str, goal: str) -> None:
    """Use the plan goal to name the project (only if still untitled)."""
    name = goal.strip()
    if not name:
        return
    try:
        conv_uuid = UUID(conversation_id)
    except ValueError:
        return
    async with SessionLocal() as db:
        conversation = await db.scalar(select(Conversation).where(Conversation.id == conv_uuid))
        if not conversation or not conversation.project_id:
            return
        project = await db.scalar(select(Project).where(Project.id == conversation.project_id))
        if not project:
            return
        if project.title.strip().lower() != UNTITLED_PROJECT_NAME:
            return
        project.title = name[:255]
        await db.commit()


_PLACEHOLDER_TITLES = {"独立会话", "新会话", ""}


async def _try_autoname_conversation(conversation_id: str, goal: str) -> None:
    """Use the plan goal to name a standalone/learning conversation (only if untitled)."""
    name = goal.strip()
    if not name:
        return
    try:
        conv_uuid = UUID(conversation_id)
    except ValueError:
        return
    async with SessionLocal() as db:
        conversation = await db.scalar(select(Conversation).where(Conversation.id == conv_uuid))
        if not conversation:
            return
        current = (conversation.title or "").strip()
        if current and current not in _PLACEHOLDER_TITLES:
            return
        conversation.title = name[:255]
        await db.commit()


async def handle_tool_call(tool_name: str, arguments: dict, conversation_id: str = "default") -> dict:
    """
    Route a tool call to the right handler.
    Returns:
      - auto tool: {"requires_input": False, "result": "..."}
      - interactive tool: {"requires_input": True, "input_request": {...}}
    """
    if tool_name == "assess":
        return {
            "requires_input": True,
            "input_request": {
                "tool": "assess",
                "questions": _normalize_questions(arguments.get("questions", [])),
            },
        }

    if tool_name == "plan":
        conversation_type = await _resolve_conversation_type(conversation_id)
        if conversation_type == ConversationType.planning.value:
            phases = arguments.get("phases", [])
            draft_nodes = []
            if isinstance(phases, list):
                for idx, phase in enumerate(phases):
                    if not isinstance(phase, dict):
                        continue
                    draft_nodes.append(
                        {
                            "id": str(phase.get("id", idx + 1)),
                            "title": str(phase.get("title", f"节点{idx + 1}")),
                            "objective": str(phase.get("objective", "")),
                            "status": "pending",
                        }
                    )
            # Defer autoname to after assistant message is saved.
            return {
                "requires_input": False,
                "deferred_plan": {
                    "mode": "project_autoname",
                    "conversation_id": conversation_id,
                    "goal": str(arguments.get("goal", "")),
                },
                "result": json.dumps(
                    {
                        "status": "ok",
                        "mode": "project",
                        "message": "已生成项目级学习路径草案，请确认后写入项目。",
                        "learning_path_draft": draft_nodes,
                    },
                    ensure_ascii=False,
                ),
            }

        # Read-only check to determine set vs update and pre-compute result.
        existing = await get_plan(conversation_id)
        is_update = bool(existing and any(phase.status == "completed" for phase in existing.phases))

        goal = arguments.get("goal", "")
        raw_phases = arguments.get("phases", [])

        # Pre-compute current_phase_id without writing to DB.
        if is_update and existing:
            completed_ids = {p.id for p in existing.phases if p.status == "completed"}
            current_phase_id = next(
                (p["id"] for p in raw_phases if p.get("id") not in completed_ids),
                raw_phases[0]["id"] if raw_phases else None,
            )
        else:
            current_phase_id = raw_phases[0]["id"] if raw_phases else None

        # Defer actual DB write; caller executes after saving assistant message.
        return {
            "requires_input": False,
            "deferred_plan": {
                "mode": "update" if is_update else "set",
                "conversation_id": conversation_id,
                "goal": goal,
                "phases": raw_phases,
            },
            "result": json.dumps(
                {
                    "status": "ok",
                    "mode": "conversation",
                    "message": f"教学计划已创建：{goal}，共{len(raw_phases)}个阶段。",
                    "current_phase": current_phase_id,
                },
                ensure_ascii=False,
            ),
        }

    if tool_name == "advance":
        # Defer actual DB write; caller executes after saving assistant message.
        return {
            "requires_input": False,
            "force_compress": True,
            "deferred_advance": {
                "completed_phase": arguments["completed_phase"],
                "summary": arguments["summary"],
                "move_to": arguments["move_to"],
            },
            "result": json.dumps(
                {
                    "status": "ok",
                    "success": True,
                    "completed": arguments["completed_phase"],
                    "now_active": arguments["move_to"],
                },
                ensure_ascii=False,
            ),
        }

    if tool_name == "quiz":
        return {
            "requires_input": True,
            "input_request": {
                "tool": "quiz",
                "phase_id": arguments.get("phase_id"),
                "questions": _normalize_questions(arguments.get("questions", [])),
            },
        }

    return {
        "requires_input": False,
        "result": json.dumps(
            {
                "status": "error",
                "message": f"Unknown tool: {tool_name}",
            },
            ensure_ascii=False,
        ),
    }
