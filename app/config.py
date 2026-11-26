"""Validated, immutable settings without process-global mutation or DB access."""

from collections.abc import Mapping
from dataclasses import dataclass
import os
from pathlib import Path
import re

from dotenv.parser import parse_stream


PROJECT_ROOT = Path(__file__).resolve().parent.parent
_HOST_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", re.ASCII)


class SettingsError(ValueError):
    """Invalid startup configuration; messages never include supplied values."""


def _host(value: str, field: str) -> str:
    if not isinstance(value, str):
        raise SettingsError(f"{field}: expected an ASCII hostname without a port or path")
    host = value.strip().lower().removesuffix(".")
    if not host or len(host) > 253 or not all(
        _HOST_LABEL.fullmatch(label) for label in host.split(".")
    ):
        raise SettingsError(f"{field}: expected an ASCII hostname without a port or path")
    return host


@dataclass(frozen=True)
class Settings:
    db_path: Path = PROJECT_ROOT / "data" / "roomshare.db"
    timezone: str = "Asia/Seoul"
    public_host: str = "room.example.com"
    admin_host: str = "admin-room.example.com"
    booking_horizon_days: int = 90
    admin_access: str = "deny"

    def __post_init__(self) -> None:
        if not isinstance(self.db_path, (str, Path)):
            raise SettingsError("ROOMSHARE_DB_PATH: expected a non-empty file path")
        path_text = str(self.db_path)
        if not path_text.strip() or any(ord(char) < 32 for char in path_text):
            raise SettingsError("ROOMSHARE_DB_PATH: expected a non-empty file path")
        path = Path(path_text)
        try:
            path = (PROJECT_ROOT / path).resolve()
            if path.is_dir():
                raise SettingsError("ROOMSHARE_DB_PATH: expected a file, not a directory")
        except (OSError, RuntimeError):
            raise SettingsError("ROOMSHARE_DB_PATH: unable to resolve file path") from None
        object.__setattr__(self, "db_path", path)

        public = _host(self.public_host, "ROOMSHARE_PUBLIC_HOST")
        admin = _host(self.admin_host, "ROOMSHARE_ADMIN_HOST")
        if public == admin:
            raise SettingsError("ROOMSHARE_PUBLIC_HOST and ROOMSHARE_ADMIN_HOST must differ")
        object.__setattr__(self, "public_host", public)
        object.__setattr__(self, "admin_host", admin)

        if self.timezone != "Asia/Seoul":
            raise SettingsError("ROOMSHARE_TIMEZONE: only Asia/Seoul is supported")
        if self.admin_access != "deny":
            raise SettingsError("ROOMSHARE_ADMIN_ACCESS: only deny is supported")

        horizon = self.booking_horizon_days
        if isinstance(horizon, str) and re.fullmatch(r"[0-9]+", horizon.strip()):
            try:
                horizon = int(horizon.strip())
            except ValueError:
                raise SettingsError("ROOMSHARE_BOOKING_HORIZON_DAYS: expected a positive integer") from None
        if type(horizon) is not int or horizon <= 0:
            raise SettingsError("ROOMSHARE_BOOKING_HORIZON_DAYS: expected a positive integer")
        object.__setattr__(self, "booking_horizon_days", horizon)


def load_settings(
    *,
    env_file: str | Path | None = PROJECT_ROOT / ".env",
    environ: Mapping[str, str] | None = None,
) -> Settings:
    """Load process > optional dotenv > defaults, with no interpolation/cache."""
    file_values: dict[str, str | None] = {}
    if env_file is not None:
        try:
            path = Path(env_file)
            if path.exists():
                if not path.is_file():
                    raise SettingsError("dotenv: expected a readable file")
                # The pinned dotenv parser exposes errors that dotenv_values
                # would otherwise log and skip. Keep values literal (no expansion).
                with path.open(encoding="utf-8") as stream:
                    for binding in parse_stream(stream):
                        if binding.error:
                            raise SettingsError("dotenv: invalid assignment syntax")
                        if binding.key is not None:
                            file_values[binding.key] = binding.value
        except (OSError, UnicodeError):
            raise SettingsError("dotenv: unable to read configuration file") from None
    process = os.environ if environ is None else environ
    values = {}
    for field in Settings.__dataclass_fields__:
        key = f"ROOMSHARE_{field.upper()}"
        if key in process:
            values[field] = process[key]
        elif key in file_values:
            values[field] = file_values[key]
    return Settings(**values)
