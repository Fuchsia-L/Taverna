from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import Conversation, ConversationStatus, ConversationType, Project, ProjectStatus

from db.session import SessionLocal

UNTITLED_PROJECT_NAME = "untitled"


def _as_uuid(value: str) -> UUID:
    return UUID(value)


def _normalize_project_title(title: str | None) -> str:
    normalized = (title or "").strip()
    return normalized or UNTITLED_PROJECT_NAME


def _derive_project_title_from_nodes(nodes: list[dict[str, Any]]) -> str:
    if not nodes:
        return UNTITLED_PROJECT_NAME
    for node in nodes:
        title = str(node.get("title") or "").strip()
        if title:
            return title[:255]
    return UNTITLED_PROJECT_NAME


async def create_project(
    db: AsyncSession,
    user_id: str,
    title: str | None = None,
    description: str | None = None,
) -> tuple[Project, Conversation]:
    project_title = _normalize_project_title(title)
    project = Project(
        user_id=_as_uuid(user_id),
        title=project_title,
        description=description,
        status=ProjectStatus.planning,
        learning_path={"nodes": []},
    )
    db.add(project)
    await db.flush()

    planning_conversation = Conversation(
        project_id=project.id,
        type=ConversationType.planning,
        title=f"{project.title} · 规划会话",
        objective="制定项目级学习路径",
        status=ConversationStatus.active,
    )
    db.add(planning_conversation)
    await db.commit()
    await db.refresh(project)
    await db.refresh(planning_conversation)
    return project, planning_conversation


async def list_projects(db: AsyncSession, user_id: str) -> list[dict[str, Any]]:
    rows = await db.execute(
        select(
            Project,
            func.count(Conversation.id).label("conversation_count"),
        )
        .outerjoin(Conversation, Conversation.project_id == Project.id)
        .where(Project.user_id == _as_uuid(user_id))
        .group_by(Project.id)
        .order_by(Project.updated_at.desc())
    )
    results: list[dict[str, Any]] = []
    for project, count in rows:
        results.append(
            {
                "id": str(project.id),
                "title": project.title,
                "description": project.description,
                "status": project.status.value,
                "learning_path": project.learning_path or {"nodes": []},
                "conversation_count": int(count or 0),
                "created_at": project.created_at.isoformat() if project.created_at else None,
                "updated_at": project.updated_at.isoformat() if project.updated_at else None,
            }
        )
    return results


async def get_project(db: AsyncSession, project_id: str, user_id: str) -> Project | None:
    return await db.scalar(
        select(Project).where(Project.id == _as_uuid(project_id), Project.user_id == _as_uuid(user_id))
    )


async def list_project_conversations(db: AsyncSession, project_id: str) -> list[Conversation]:
    rows = await db.scalars(
        select(Conversation)
        .where(Conversation.project_id == _as_uuid(project_id))
        .order_by(Conversation.created_at.asc())
    )
    return list(rows)


async def create_standalone_conversation(
    db: AsyncSession,
    title: str | None = None,
    objective: str | None = None,
    conv_type: ConversationType = ConversationType.standalone,
) -> Conversation:
    conversation = Conversation(
        project_id=None,
        type=conv_type,
        title=title,
        objective=objective,
        status=ConversationStatus.active,
    )
    db.add(conversation)
    await db.commit()
    await db.refresh(conversation)
    return conversation


async def confirm_project_plan(
    db: AsyncSession,
    project_id: str,
    user_id: str,
    nodes: list[dict[str, Any]],
) -> dict[str, Any]:
    project = await get_project(db, project_id, user_id)
    if not project:
        raise ValueError("Project not found")

    normalized_nodes: list[dict[str, Any]] = []
    for idx, node in enumerate(nodes):
        node_id = str(node.get("id") or f"node-{idx + 1}")
        title = str(node.get("title") or f"节点{idx + 1}")
        objective = str(node.get("objective") or "")
        status = "active" if idx == 0 else "pending"

        conversation = Conversation(
            project_id=project.id,
            type=ConversationType.learning,
            title=title,
            node_id=node_id,
            objective=objective,
            status=ConversationStatus.active,
        )
        db.add(conversation)
        await db.flush()
        normalized_nodes.append(
            {
                "id": node_id,
                "title": title,
                "objective": objective,
                "status": status,
                "conversation_id": str(conversation.id),
            }
        )

    project.learning_path = {"nodes": normalized_nodes}
    project.status = ProjectStatus.active
    if project.title.strip().lower() == UNTITLED_PROJECT_NAME and normalized_nodes:
        project.title = _derive_project_title_from_nodes(normalized_nodes)
    await db.commit()
    await db.refresh(project)
    return {"project_id": str(project.id), "learning_path": project.learning_path}


async def delete_project(
    db: AsyncSession,
    project_id: str,
    user_id: str,
) -> list[str]:
    """Delete a project and all its conversations (application-level cascade).

    Returns list of deleted conversation_id strings for in-memory cleanup.
    Raises ValueError if the project is not found.
    """
    project = await get_project(db, project_id, user_id)
    if not project:
        raise ValueError("Project not found")

    conversations = await db.scalars(
        select(Conversation).where(Conversation.project_id == _as_uuid(project_id))
    )
    deleted_conv_ids: list[str] = []
    for conv in conversations:
        deleted_conv_ids.append(str(conv.id))
        await db.delete(conv)

    await db.delete(project)
    await db.commit()
    return deleted_conv_ids


async def get_planning_context(conversation_id: str) -> str:
    """Return project-level planning prompt if this is a planning conversation, else ''."""
    try:
        conv_uuid = UUID(conversation_id)
    except ValueError:
        return ""
    async with SessionLocal() as db:
        conv = await db.scalar(
            select(Conversation).where(Conversation.id == conv_uuid)
        )
        if not conv or conv.type != ConversationType.planning or not conv.project_id:
            return ""
        project = await db.scalar(
            select(Project).where(Project.id == conv.project_id)
        )
        if not project:
            return ""
        title = project.title or ""
        desc = project.description or ""
        parts = [
            "当前处于**项目规划会话**。",
            "你的任务是与学生讨论学习目标，然后调用 `plan` 工具生成一份项目级学习路径草案。",
            "草案中的每个阶段会成为一个独立的学习会话，学生确认后系统会自动创建。",
            "因此每个阶段应该是一个完整的、可独立教学的主题单元。",
            "",
            "流程：",
            "1. 先和学生交流，了解他们想学什么、目前的水平、期望达到的目标",
            "2. 根据交流结果，调用 `plan` 工具输出分阶段的学习路径",
            "3. 如果学生对草案有修改意见，调整后重新调用 `plan`",
            "4. 学生满意后会在界面上确认，系统自动创建各阶段的学习会话",
        ]
        if title and title.strip().lower() != UNTITLED_PROJECT_NAME:
            parts.append(f"\n项目名称：{title}")
        if desc.strip():
            parts.append(f"项目描述：{desc}")
        return "\n".join(parts)
