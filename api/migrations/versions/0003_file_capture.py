"""label, stored_file and print_job tables (file capture)

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-17
"""
from alembic import op
import sqlalchemy as sa

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "label",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(), nullable=False, unique=True),
    )
    op.create_table(
        "stored_file",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("sha256", sa.String(), nullable=False, unique=True, index=True),
        sa.Column("filename", sa.String(), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.Column("path", sa.String(), nullable=False),
        sa.Column("label_id", sa.Integer(), sa.ForeignKey("label.id"), nullable=True, index=True),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("printer_id", sa.String(), nullable=True),
        sa.Column("meta", sa.JSON(), nullable=False),
        sa.Column("first_seen", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "print_job",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("printer_id", sa.String(), nullable=False, index=True),
        sa.Column("filename", sa.String(), nullable=False),
        sa.Column("file_id", sa.Integer(), sa.ForeignKey("stored_file.id"), nullable=True, index=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("result", sa.String(), nullable=False),
        sa.Column("print_duration", sa.Float(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("print_job")
    op.drop_table("stored_file")
    op.drop_table("label")
