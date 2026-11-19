"""Time-lapse frame counts on print_job

Frames themselves live on disk under TIMELAPSE_ROOT/<printer>/<job id>/
(app/timelapse.py); the row only keeps how many there are and their size, so
History can tell which prints have a time-lapse without touching the NAS.

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-19
"""
from alembic import op
import sqlalchemy as sa

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("print_job") as b:
        b.add_column(sa.Column("timelapse_frames", sa.Integer(), nullable=False, server_default="0"))
        b.add_column(sa.Column("timelapse_bytes", sa.Integer(), nullable=False, server_default="0"))


def downgrade() -> None:
    with op.batch_alter_table("print_job") as b:
        b.drop_column("timelapse_bytes")
        b.drop_column("timelapse_frames")
