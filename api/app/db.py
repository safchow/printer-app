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


def make_engine(database_url: str):
    return create_engine(database_url, connect_args={"check_same_thread": False})


def migrate(database_url: str) -> None:
    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS))
    cfg.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(cfg, "head")


__all__ = ["Printer", "Session", "make_engine", "migrate"]
