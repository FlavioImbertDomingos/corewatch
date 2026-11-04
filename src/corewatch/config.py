"""Load and validate the exporter configuration file."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .engines import ENGINES


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
    engine: str = "db2"

    @property
    def connect_database(self) -> str:
        """Db2: the database itself. Informix: sysmaster, which describes the server."""
        return ENGINES[self.engine].connect_database or self.name

    def dsn(self) -> str:
        parts = [
            f"DATABASE={self.connect_database}",
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
    def engines(self) -> list[str]:
        return sorted({db.engine for db in self.databases})

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


def _wanted_engines(engines: str | None) -> set[str]:
    wanted = {e.strip().lower() for e in (engines or "").split(",") if e.strip()}
    unknown = wanted - set(ENGINES)
    if unknown:
        raise ConfigError(f"unknown engine(s) {', '.join(sorted(unknown))}; use {', '.join(sorted(ENGINES))}")
    return wanted


def parse_config(raw: dict, env: dict[str, str] | None = None, engines: str | None = None) -> Config:
    """Validate the YAML. engines ("db2,informix") keeps only those targets; empty keeps all.

    Selection happens before passwords are read, so a Db2-only run does not need
    the Informix password set. The demo start script passes it through
    COREWATCH_ENGINES, so one config file can describe every target while only
    the running engines are monitored.
    """
    env = dict(os.environ) if env is None else env
    wanted = _wanted_engines(engines)
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
    if wanted:
        entries = [e for e in entries if str(e.get("engine", "db2")).lower() in wanted]
        if not entries:
            raise ConfigError(f"no targets left after selecting engines {', '.join(sorted(wanted))}")
    seen: set[tuple[str, str, str, int]] = set()
    for entry in entries:
        for key in ("name", "host", "user"):
            if key not in entry:
                raise ConfigError(f"database entry is missing {key!r}: {entry}")
        engine = str(entry.get("engine", "db2")).lower()
        if engine not in ENGINES:
            raise ConfigError(
                f"database {entry['name']!r}: unknown engine {engine!r}; use one of {', '.join(sorted(ENGINES))}"
            )
        db = DatabaseConfig(
            name=str(entry["name"]),
            host=str(entry["host"]),
            port=int(entry.get("port", ENGINES[engine].default_port)),
            user=str(entry["user"]),
            password=_read_password(entry, env),
            ssl=bool(entry.get("ssl", False)),
            labels={str(k): str(v) for k, v in (entry.get("labels") or {}).items()},
            engine=engine,
        )
        ident = (db.engine, db.name, db.host, db.port)
        if ident in seen:
            raise ConfigError(f"{db.engine} target {db.name} on {db.host}:{db.port} is listed twice")
        seen.add(ident)
        cfg.databases.append(db)
    return cfg


def load_config(path: str | os.PathLike, engines: str | None = None) -> Config:
    with open(path) as handle:
        return parse_config(yaml.safe_load(handle), engines=engines)
