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


def make_engine(database_url: str):
    return create_engine(database_url, connect_args={"check_same_thread": False})


def migrate(database_url: str) -> None:
    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS))
    cfg.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(cfg, "head")


__all__ = ["ActionLog", "Printer", "Session", "make_engine", "migrate"]
