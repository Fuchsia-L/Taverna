from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import Conversation, ConversationStatus, ConversationType, Project, ProjectStatus


def _as_uuid(value: str) -> UUID:
    return UUID(value)


async def create_project(db: AsyncSession, user_id: str, title: str, description: str | None = None) -> tuple[Project, Conversation]:
    project = Project(
        user_id=_as_uuid(user_id),
        title=title.strip(),
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
    await db.commit()
    await db.refresh(project)
    return {"project_id": str(project.id), "learning_path": project.learning_path}
