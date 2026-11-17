"""Settings come from the environment (.env), printers from printers.yaml."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Only used by `python -m app` (in Docker the port is published on BIND_ADDRESS by compose).
    bind_address: str = "127.0.0.1"
    port: int = 8090
    data_dir: Path = Path("./data")
    library_root: Path = Path("./library")
    printers_file: Path = Path("./printers.yaml")
    tz: str = "UTC"
    import_token: str = ""
    tailscale_socket: str = ""
    # How long to wait between connection attempts to an offline printer.
    reconnect_seconds: float = 30.0
    # At most one camera handshake per printer this often.
    camera_min_interval: float = 5.0
    static_dir: Path | None = None

    @property
    def database_url(self) -> str:
        return f"sqlite:///{self.data_dir / 'printer.db'}"


PrinterType = Literal["moonraker", "octoprint", "prusalink", "bambu"]
CameraType = Literal["creality_webrtc", "mjpeg", "none"]

_SLUG = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")


class MoonrakerObjects(BaseModel):
    """Klipper object names to read. Defaults are a Creality K1C's (stock firmware)."""

    fans: dict[str, str] = Field(
        default_factory=lambda: {
            "Model fan": "fan_generic model_fan",
            "Auxiliary fan": "fan_generic side_fan",
            "Case fan": "fan_generic chassis_fan",
        }
    )
    light: str | None = "output_pin LED"


class PrinterConfig(BaseModel):
    id: str
    name: str
    type: PrinterType = "moonraker"
    host: str
    port: int = 7125
    camera: CameraType = "none"
    camera_port: int = 8000
    cfs: bool = False
    bed_size_mm: tuple[float, float, float] | None = None
    moonraker: MoonrakerObjects = Field(default_factory=MoonrakerObjects)

    @field_validator("id")
    @classmethod
    def _slug(cls, v: str) -> str:
        if not _SLUG.match(v):
            raise ValueError("id must be lowercase letters, digits, - or _ (max 40)")
        return v

    @field_validator("port", "camera_port")
    @classmethod
    def _port(cls, v: int) -> int:
        if not 0 < v < 65536:
            raise ValueError("port must be 1-65535")
        return v


class PrintersFile(BaseModel):
    printers: list[PrinterConfig] = Field(default_factory=list)

    @model_validator(mode="after")
    def _unique_ids(self) -> "PrintersFile":
        ids = [p.id for p in self.printers]
        dupes = {i for i in ids if ids.count(i) > 1}
        if dupes:
            raise ValueError(f"duplicate printer ids: {', '.join(sorted(dupes))}")
        return self


def load_printers(path: Path) -> list[PrinterConfig]:
    # A missing file (or the empty directory Docker creates for a missing bind mount) means no printers.
    if not path.is_file():
        return []
    raw = yaml.safe_load(path.read_text()) or {}
    return PrintersFile.model_validate(raw).printers
