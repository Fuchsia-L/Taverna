from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select

from core.project_manager import (
    confirm_project_plan,
    create_project,
    create_standalone_conversation,
    get_project,
    list_project_conversations,
    list_projects,
)
from db.models import Conversation, ConversationStatus, Project, User
from db.session import SessionLocal
from models.project import ConfirmProjectPlanRequest, CreateConversationRequest, CreateProjectRequest

router = APIRouter()


async def _get_default_user_id() -> str:
    async with SessionLocal() as db:
        user = await db.scalar(select(User).where(User.name == "default"))
        if user:
            return str(user.id)
        new_user = User(name="default")
        db.add(new_user)
        await db.commit()
        await db.refresh(new_user)
        return str(new_user.id)


@router.post("/projects")
async def create_project_api(req: CreateProjectRequest):
    user_id = await _get_default_user_id()
    async with SessionLocal() as db:
        project, planning = await create_project(db, user_id, req.title, req.description)
        return {
            "project": {
                "id": str(project.id),
                "title": project.title,
                "description": project.description,
                "status": project.status.value,
                "learning_path": project.learning_path,
            },
            "planning_conversation": {
                "id": str(planning.id),
                "type": planning.type.value,
                "title": planning.title,
                "objective": planning.objective,
                "status": planning.status.value,
            },
        }


@router.get("/projects")
async def list_projects_api():
    user_id = await _get_default_user_id()
    async with SessionLocal() as db:
        return {"items": await list_projects(db, user_id)}


@router.get("/projects/{project_id}")
async def get_project_api(project_id: str):
    user_id = await _get_default_user_id()
    async with SessionLocal() as db:
        project = await get_project(db, project_id, user_id)
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")
        return {
            "id": str(project.id),
            "title": project.title,
            "description": project.description,
            "status": project.status.value,
            "learning_path": project.learning_path or {"nodes": []},
            "created_at": project.created_at.isoformat() if project.created_at else None,
            "updated_at": project.updated_at.isoformat() if project.updated_at else None,
        }


@router.post("/projects/{project_id}/confirm-plan")
async def confirm_plan_api(project_id: str, req: ConfirmProjectPlanRequest):
    user_id = await _get_default_user_id()
    async with SessionLocal() as db:
        try:
            result = await confirm_project_plan(db, project_id, user_id, req.nodes)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return result


@router.get("/projects/{project_id}/conversations")
async def project_conversations_api(project_id: str):
    async with SessionLocal() as db:
        items = await list_project_conversations(db, project_id)
        return {
            "items": [
                {
                    "id": str(item.id),
                    "type": item.type.value,
                    "title": item.title,
                    "objective": item.objective,
                    "node_id": item.node_id,
                    "status": item.status.value,
                    "created_at": item.created_at.isoformat() if item.created_at else None,
                }
                for item in items
            ]
        }


@router.post("/conversations")
async def create_conversation_api(req: CreateConversationRequest):
    async with SessionLocal() as db:
        conversation = await create_standalone_conversation(
            db,
            title=req.title,
            objective=req.objective,
        )
        return {
            "id": str(conversation.id),
            "type": conversation.type.value,
            "title": conversation.title,
            "objective": conversation.objective,
            "status": conversation.status.value,
        }


@router.get("/conversations")
async def list_conversations_api(project_id: str | None = Query(default=None)):
    async with SessionLocal() as db:
        query = select(Conversation)
        if project_id:
            query = query.where(Conversation.project_id == UUID(project_id))
        else:
            query = query.where(Conversation.project_id.is_(None))
        rows = await db.scalars(query.order_by(Conversation.created_at.asc()))
        items = list(rows)
        return {
            "items": [
                {
                    "id": str(item.id),
                    "type": item.type.value,
                    "title": item.title,
                    "objective": item.objective,
                    "node_id": item.node_id,
                    "status": item.status.value,
                }
                for item in items
            ]
        }


@router.post("/conversations/{conversation_id}/end")
async def end_conversation_api(conversation_id: str):
    async with SessionLocal() as db:
        try:
            conv_uuid = UUID(conversation_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Invalid conversation_id") from exc
        conversation = await db.scalar(select(Conversation).where(Conversation.id == conv_uuid))
        if not conversation:
            raise HTTPException(status_code=404, detail="Conversation not found")
        conversation.status = ConversationStatus.completed
        await db.commit()
        return {"status": "ok", "conversation_id": str(conversation.id)}
