"""Merge print_job rows that are one print split in two

Before the fix in this release, a restart of the app during a print closed the
running job as "unknown" and opened a second row for the same print. Rows for
the same printer and file whose start times are under 5 minutes apart, where the
earlier one ended "unknown", are merged into the later one.

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-17
"""
from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

SAME_PRINT_SECONDS = 300


def _ts(v) -> float:
    if isinstance(v, str):
        v = datetime.fromisoformat(v)
    if v.tzinfo is None:
        v = v.replace(tzinfo=timezone.utc)
    return v.timestamp()


def upgrade() -> None:
    conn = op.get_bind()
    rows = conn.execute(sa.text(
        "SELECT id, printer_id, filename, file_id, started_at, result FROM print_job ORDER BY printer_id, id")).all()
    prev = None
    for row in rows:
        if (prev is not None and prev.printer_id == row.printer_id and prev.filename == row.filename
                and prev.result == "unknown" and prev.started_at and row.started_at
                and abs(_ts(prev.started_at) - _ts(row.started_at)) < SAME_PRINT_SECONDS):
            started = min(prev.started_at, row.started_at, key=_ts)
            conn.execute(sa.text("UPDATE print_job SET started_at = :s, file_id = COALESCE(file_id, :f) WHERE id = :id"),
                         {"s": started, "f": prev.file_id, "id": row.id})
            conn.execute(sa.text("DELETE FROM print_job WHERE id = :id"), {"id": prev.id})
        prev = row


def downgrade() -> None:
    pass  # the split rows aren't worth putting back
