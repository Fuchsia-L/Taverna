from pydantic import BaseModel, Field


class CreateProjectRequest(BaseModel):
    title: str
    description: str | None = None


class ConfirmProjectPlanRequest(BaseModel):
    nodes: list[dict] = Field(default_factory=list)


class CreateConversationRequest(BaseModel):
    title: str | None = None
    objective: str | None = None
    type: str = "standalone"
