"""action_log table

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-17
"""
from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "action_log",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("printer_id", sa.String(), nullable=False, index=True),
        sa.Column("action", sa.String(), nullable=False),
        sa.Column("values", sa.JSON(), nullable=False),
        sa.Column("command", sa.String(), nullable=True),
        sa.Column("result", sa.String(), nullable=False),
        sa.Column("detail", sa.String(), nullable=True),
        sa.Column("device", sa.String(), nullable=True),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("action_log")
