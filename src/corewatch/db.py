"""A thin wrapper around ibm_db so the collector can be tested without Db2."""

from __future__ import annotations

from typing import Any, Protocol


class Connection(Protocol):
    def query(self, sql: str) -> list[dict[str, Any]]: ...
    def close(self) -> None: ...


class Db2Connection:
    """One connection to one database. Rows come back as dicts keyed by column name."""

    def __init__(self, dsn: str):
        import ibm_db  # imported here so tests do not need the driver

        self._ibm_db = ibm_db
        self._conn = ibm_db.connect(dsn, "", "")

    def query(self, sql: str) -> list[dict[str, Any]]:
        stmt = self._ibm_db.exec_immediate(self._conn, sql)
        rows: list[dict[str, Any]] = []
        row = self._ibm_db.fetch_assoc(stmt)
        while row:
            rows.append(row)
            row = self._ibm_db.fetch_assoc(stmt)
        self._ibm_db.free_result(stmt)
        return rows

    def close(self) -> None:
        try:
            self._ibm_db.close(self._conn)
        except Exception:
            pass


def connect(dsn: str) -> Connection:
    return Db2Connection(dsn)
