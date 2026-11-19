"""SQLite through SQLModel; the schema is managed by Alembic (migrations/)."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import JSON, Column
from sqlmodel import Field, Session, SQLModel, create_engine

MIGRATIONS = Path(__file__).resolve().parent.parent / "migrations"


class Printer(SQLModel, table=True):
    id: str = Field(primary_key=True)
    name: str
    type: str
    address: str
    settings: dict = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    last_seen: datetime | None = None


class ActionLog(SQLModel, table=True):
    """Every control pressed in the app, including refused ones."""

    __tablename__ = "action_log"
    id: int | None = Field(default=None, primary_key=True)
    printer_id: str = Field(index=True)
    action: str
    values: dict = Field(default_factory=dict, sa_column=Column("values", JSON, nullable=False))
    command: str | None = None
    result: str  # sent | refused | failed
    detail: str | None = None
    device: str | None = None
    at: datetime


class Label(SQLModel, table=True):
    """A name the user sorts captured files under."""

    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(unique=True)


class StoredFile(SQLModel, table=True):
    """One captured print file, stored once by content (sha256). No label = in the Inbox."""

    __tablename__ = "stored_file"
    id: int | None = Field(default=None, primary_key=True)
    sha256: str = Field(unique=True, index=True)
    filename: str  # the latest name it was seen under
    size: int
    path: str  # relative to LIBRARY_ROOT
    label_id: int | None = Field(default=None, foreign_key="label.id", index=True)
    source: str = "printer"  # printer | print (the once-per-print backstop) | claude
    printer_id: str | None = None
    meta: dict = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    first_seen: datetime
    last_seen: datetime


class PrintJob(SQLModel, table=True):
    """One print the app watched, from start to its end state."""

    __tablename__ = "print_job"
    id: int | None = Field(default=None, primary_key=True)
    printer_id: str = Field(index=True)
    filename: str
    file_id: int | None = Field(default=None, foreign_key="stored_file.id", index=True)
    started_at: datetime
    ended_at: datetime | None = None
    result: str = "printing"  # printing | complete | cancelled | error | unknown
    print_duration: float | None = None
    # Time-lapse frames saved for this print (app/timelapse.py); 0 = no time-lapse.
    timelapse_frames: int = 0
    timelapse_bytes: int = 0


def make_engine(database_url: str):
    return create_engine(database_url, connect_args={"check_same_thread": False})


def migrate(database_url: str) -> None:
    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS))
    cfg.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(cfg, "head")


__all__ = ["ActionLog", "Label", "PrintJob", "Printer", "Session", "StoredFile", "make_engine", "migrate"]
