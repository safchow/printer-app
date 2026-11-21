"""Where each captured file lives on its printer

stored_file.filename is only the last part of the name; printing a file from
the app needs the path inside the printer's G-code folder. Older rows have
none and fall back to their latest print's file name, then their own name.

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-21
"""
from alembic import op
import sqlalchemy as sa

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("stored_file") as b:
        b.add_column(sa.Column("printer_path", sa.String(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("stored_file") as b:
        b.drop_column("printer_path")
