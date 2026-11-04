"""The database engines corewatch can monitor, and what differs between them.

Both engines share one driver (ibm_db: Db2 natively, Informix over DRDA), one
collector and one HTTP endpoint. What changes per engine is the metric prefix,
the label that names the monitored thing, the test query and the query list.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import informix_queries, queries
from .queries import Query


@dataclass(frozen=True)
class Engine:
    name: str
    prefix: str  # metric name prefix: db2_up, informix_up
    target_label: str  # label that names the monitored database or server
    default_port: int
    probe_sql: str
    queries: tuple[Query, ...]
    metrics: dict
    # Db2 monitors one database per connection; Informix monitors the whole
    # server through sysmaster.
    connect_database: str | None = None


ENGINES: dict[str, Engine] = {
    "db2": Engine(
        name="db2",
        prefix="db2",
        target_label="database",
        default_port=50000,
        probe_sql="SELECT 1 FROM SYSIBM.SYSDUMMY1",
        queries=tuple(queries.QUERIES),
        metrics=queries.METRICS,
    ),
    "informix": Engine(
        name="informix",
        prefix="informix",
        target_label="server",
        default_port=9089,
        probe_sql=informix_queries.PROBE_SQL,
        queries=tuple(informix_queries.QUERIES),
        metrics=informix_queries.METRICS,
        connect_database="sysmaster",
    ),
}
