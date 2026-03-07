"""create pending_tool_calls table

Revision ID: 0003_create_pending_tool_calls
Revises: 0002_add_compressed_before_id
Create Date: 2026-03-03 12:00:00
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0003_create_pending_tool_calls"
down_revision = "0002_add_compressed_before_id"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "pending_tool_calls",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tool_call_id", sa.String(length=120), nullable=False),
        sa.Column("tool_name", sa.String(length=64), nullable=False),
        sa.Column("messages_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("input_request", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("conversation_id"),
    )
    op.create_index("ix_pending_tool_calls_conversation_id", "pending_tool_calls", ["conversation_id"])


def downgrade() -> None:
    op.drop_index("ix_pending_tool_calls_conversation_id", table_name="pending_tool_calls")
    op.drop_table("pending_tool_calls")

