"""
Processes tool calls returned by the AI and routes them
to the appropriate teaching engine functions.
"""

from __future__ import annotations

import json
from uuid import UUID

from sqlalchemy import select

from core.teaching_engine import advance_phase, get_plan, set_plan, update_plan
from db.models import Conversation, ConversationType
from db.session import SessionLocal

INTERACTIVE_TOOLS = {"assess", "quiz"}
AUTO_TOOLS = {"plan", "advance"}


def is_interactive_tool(tool_name: str) -> bool:
    return tool_name in INTERACTIVE_TOOLS


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
                "questions": arguments.get("questions", []),
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
            return {
                "requires_input": False,
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

        existing = await get_plan(conversation_id)
        if existing and any(phase.status == "completed" for phase in existing.phases):
            plan = await update_plan(conversation_id, arguments["goal"], arguments["phases"])
        else:
            plan = await set_plan(conversation_id, arguments["goal"], arguments["phases"])

        return {
            "requires_input": False,
            "result": json.dumps(
                {
                    "status": "ok",
                    "mode": "conversation",
                    "message": f"教学计划已创建：{plan.goal}，共{len(plan.phases)}个阶段。",
                    "current_phase": plan.current_phase_id,
                },
                ensure_ascii=False,
            ),
        }

    if tool_name == "advance":
        result = await advance_phase(
            conversation_id,
            arguments["completed_phase"],
            arguments["summary"],
            arguments["move_to"],
        )

        return {
            "requires_input": False,
            "force_compress": bool(result.get("success")),
            "result": json.dumps(
                {
                    "status": "ok" if result.get("success") else "error",
                    **result,
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
                "questions": arguments.get("questions", []),
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
