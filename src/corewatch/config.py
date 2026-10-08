"""Load and validate the exporter configuration file."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml


class ConfigError(Exception):
    pass


@dataclass
class DatabaseConfig:
    name: str
    host: str
    port: int
    user: str
    password: str
    ssl: bool = False
    labels: dict[str, str] = field(default_factory=dict)

    def dsn(self) -> str:
        parts = [
            f"DATABASE={self.name}",
            f"HOSTNAME={self.host}",
            f"PORT={self.port}",
            "PROTOCOL=TCPIP",
            f"UID={self.user}",
            f"PWD={self.password}",
            "ConnectTimeout=10",
        ]
        if self.ssl:
            parts.append("SECURITY=SSL")
        return ";".join(parts) + ";"


@dataclass
class Config:
    address: str = "0.0.0.0"
    port: int = 9953
    cache_seconds: float = 30.0
    optional_queries: list[str] = field(default_factory=list)
    databases: list[DatabaseConfig] = field(default_factory=list)

    @property
    def label_names(self) -> list[str]:
        """Every database carries the same label names, so series line up."""
        names: set[str] = set()
        for db in self.databases:
            names.update(db.labels)
        return sorted(names)


def _read_password(entry: dict, env: dict[str, str]) -> str:
    """A password comes from an environment variable or a file, never the YAML."""
    if "password" in entry:
        raise ConfigError(
            f"database {entry.get('name')!r}: put the password in password_env or "
            "password_file, not in the config file"
        )
    if "password_env" in entry:
        var = entry["password_env"]
        if var not in env:
            raise ConfigError(f"environment variable {var} is not set")
        return env[var]
    if "password_file" in entry:
        return Path(entry["password_file"]).read_text().strip()
    raise ConfigError(f"database {entry.get('name')!r}: set password_env or password_file")


def parse_config(raw: dict, env: dict[str, str] | None = None) -> Config:
    env = dict(os.environ) if env is None else env
    raw = raw or {}
    listen = raw.get("listen", {})
    cfg = Config(
        address=listen.get("address", "0.0.0.0"),
        port=int(listen.get("port", 9953)),
        cache_seconds=float(raw.get("cache_seconds", 30)),
        optional_queries=list(raw.get("optional_queries", [])),
    )
    entries = raw.get("databases") or []
    if not entries:
        raise ConfigError("config lists no databases")
    seen: set[tuple[str, str, int]] = set()
    for entry in entries:
        for key in ("name", "host", "user"):
            if key not in entry:
                raise ConfigError(f"database entry is missing {key!r}: {entry}")
        db = DatabaseConfig(
            name=str(entry["name"]),
            host=str(entry["host"]),
            port=int(entry.get("port", 50000)),
            user=str(entry["user"]),
            password=_read_password(entry, env),
            ssl=bool(entry.get("ssl", False)),
            labels={str(k): str(v) for k, v in (entry.get("labels") or {}).items()},
        )
        ident = (db.name, db.host, db.port)
        if ident in seen:
            raise ConfigError(f"database {db.name} on {db.host}:{db.port} is listed twice")
        seen.add(ident)
        cfg.databases.append(db)
    return cfg


def load_config(path: str | os.PathLike) -> Config:
    with open(path) as handle:
        return parse_config(yaml.safe_load(handle))
