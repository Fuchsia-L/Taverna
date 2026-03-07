from pydantic import BaseModel, Field, field_validator

_MAX_IMAGES = 9
_MAX_IMAGE_CHARS = 2_800_000  # ~2 MB base64


def _check_image_sizes(v: list[str]) -> list[str]:
    for i, item in enumerate(v):
        if len(item) > _MAX_IMAGE_CHARS:
            raise ValueError(f"Image {i} exceeds maximum allowed size")
    return v


class ChatRequest(BaseModel):
    message: str
    images: list[str] = Field(default_factory=list, max_length=_MAX_IMAGES)
    model: str | None = None
    conversation_id: str | None = None
    thinking: bool = False

    @field_validator("images")
    @classmethod
    def validate_image_sizes(cls, v: list[str]) -> list[str]:
        return _check_image_sizes(v)


class RetryRequest(BaseModel):
    conversation_id: str
    model: str | None = None
    thinking: bool = False


class RewindRequest(BaseModel):
    conversation_id: str
    target_user_turn: int
    replacement_message: str
    images: list[str] = Field(default_factory=list, max_length=_MAX_IMAGES)
    model: str | None = None
    thinking: bool = False

    @field_validator("images")
    @classmethod
    def validate_image_sizes(cls, v: list[str]) -> list[str]:
        return _check_image_sizes(v)


class ChatResponse(BaseModel):
    reply: str


class ChatOrToolResponse(BaseModel):
    reply: str
    tool_input_required: dict | None = None
    plan_card: dict | None = None


class ToolResponseRequest(BaseModel):
    conversation_id: str
    tool_call_id: str
    answers: list[dict]
    model: str | None = None
    thinking: bool = False
