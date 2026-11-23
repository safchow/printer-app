"""Print queue: files waiting to be printed later, and per-printer queue state

See app/queue.py for the rules.

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-23
"""
from alembic import op
import sqlalchemy as sa

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "print_queue",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("printer_id", sa.String(), nullable=False, index=True),
        sa.Column("file_id", sa.Integer(), sa.ForeignKey("stored_file.id"), nullable=False, index=True),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("mode", sa.String(), nullable=False),
        sa.Column("start_at", sa.DateTime(), nullable=True),
        sa.Column("window_minutes", sa.Integer(), nullable=True),
        sa.Column("gap_minutes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("after_id", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(), nullable=False, server_default="scheduled", index=True),
        sa.Column("note", sa.String(), nullable=True),
        sa.Column("command", sa.String(), nullable=True),
        sa.Column("job_id", sa.Integer(), sa.ForeignKey("print_job.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
    )
    op.create_table(
        "queue_state",
        sa.Column("printer_id", sa.String(), primary_key=True),
        sa.Column("paused", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("note", sa.String(), nullable=True),
        sa.Column("plate_confirmed_at", sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("queue_state")
    op.drop_table("print_queue")
