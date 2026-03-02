import enum
import uuid

from sqlalchemy import DateTime, Enum, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db.base import Base


class ConversationType(str, enum.Enum):
    planning = "planning"
    learning = "learning"
    review = "review"
    standalone = "standalone"


class ConversationStatus(str, enum.Enum):
    active = "active"
    completed = "completed"
    abandoned = "abandoned"


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="SET NULL"), nullable=True, index=True
    )
    parent_conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("conversations.id", ondelete="SET NULL"), nullable=True
    )
    type: Mapped[ConversationType] = mapped_column(
        Enum(ConversationType, name="conversation_type"), nullable=False, default=ConversationType.standalone
    )
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    node_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    objective: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[ConversationStatus] = mapped_column(
        Enum(ConversationStatus, name="conversation_status"), nullable=False, default=ConversationStatus.active
    )
    created_at: Mapped[str] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    compressed_before_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("messages.id", ondelete="SET NULL"), nullable=True
    )
    updated_at: Mapped[str] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    project = relationship("Project", back_populates="conversations")
    parent_conversation = relationship("Conversation", remote_side=[id])
    messages = relationship(
        "Message",
        back_populates="conversation",
        cascade="all, delete-orphan",
        foreign_keys="Message.conversation_id",
    )
    teaching_plan = relationship("TeachingPlan", back_populates="conversation", uselist=False, cascade="all, delete-orphan")
