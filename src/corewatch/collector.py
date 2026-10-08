"""Run the queries against each database and expose the results to Prometheus."""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable, Iterable

from prometheus_client.core import CounterMetricFamily, GaugeMetricFamily

from .config import Config, DatabaseConfig
from .db import Connection, connect
from .queries import COUNTER, METRICS, QUERIES, Query, Sample

log = logging.getLogger("corewatch")

EXPORTER_METRICS = {
    "db2_up": "1 if the exporter connected and ran a test query.",
    "db2_scrape_duration_seconds": "Time taken to collect from this database.",
    "db2_query_success": "1 if the named monitoring query ran, 0 if it failed.",
}


class Db2Collector:
    def __init__(
        self,
        config: Config,
        connector: Callable[[str], Connection] = connect,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.config = config
        self._connector = connector
        self._clock = clock
        self._connections: dict[str, Connection] = {}
        self._lock = threading.Lock()
        self._cache: list[Sample] | None = None
        self._cache_time = 0.0
        self.queries: list[Query] = [
            q for q in QUERIES if not q.optional or q.name in config.optional_queries
        ]

    # -- collection -------------------------------------------------------

    def _base_labels(self, db: DatabaseConfig) -> tuple[tuple[str, str], ...]:
        labels = [("database", db.name), ("db_host", db.host)]
        labels += [(name, db.labels.get(name, "")) for name in self.config.label_names]
        return tuple(labels)

    def _key(self, db: DatabaseConfig) -> str:
        return f"{db.host}:{db.port}/{db.name}"

    def _connection(self, db: DatabaseConfig) -> Connection:
        key = self._key(db)
        if key not in self._connections:
            self._connections[key] = self._connector(db.dsn())
        return self._connections[key]

    def _drop(self, db: DatabaseConfig) -> None:
        conn = self._connections.pop(self._key(db), None)
        if conn is not None:
            conn.close()

    def _collect_database(self, db: DatabaseConfig) -> Iterable[Sample]:
        base = self._base_labels(db)
        started = self._clock()
        try:
            conn = self._connection(db)
            conn.query("SELECT 1 FROM SYSIBM.SYSDUMMY1")
        except Exception as exc:  # the database is down or unreachable
            log.warning("cannot reach %s: %s", self._key(db), exc)
            self._drop(db)
            yield Sample("db2_up", 0.0, base)
            yield Sample("db2_scrape_duration_seconds", self._clock() - started, base)
            return
        yield Sample("db2_up", 1.0, base)
        for query in self.queries:
            ok = 1.0
            try:
                # materialize first so a failing query emits nothing partial
                samples = list(query.handler(conn.query(query.sql)))
            except Exception as exc:
                log.warning("query %s failed on %s: %s", query.name, self._key(db), exc)
                samples = []
                ok = 0.0
            for sample in samples:
                yield Sample(sample.name, sample.value, base + sample.labels)
            yield Sample("db2_query_success", ok, base + (("query", query.name),))
        yield Sample("db2_scrape_duration_seconds", self._clock() - started, base)

    def samples(self) -> list[Sample]:
        with self._lock:
            now = self._clock()
            if self._cache is None or now - self._cache_time >= self.config.cache_seconds:
                fresh: list[Sample] = []
                for db in self.config.databases:
                    fresh.extend(self._collect_database(db))
                self._cache = fresh
                self._cache_time = now
            return list(self._cache)

    # -- prometheus_client interface --------------------------------------

    def describe(self):
        return []

    def collect(self):
        families = {}
        for sample in self.samples():
            names = [name for name, _ in sample.labels]
            if sample.name not in families:
                if sample.name in METRICS:
                    kind, doc, _ = METRICS[sample.name]
                else:
                    kind, doc = "gauge", EXPORTER_METRICS[sample.name]
                if kind == COUNTER:
                    # prometheus_client adds the _total suffix itself
                    family = CounterMetricFamily(sample.name.removesuffix("_total"), doc, labels=names)
                else:
                    family = GaugeMetricFamily(sample.name, doc, labels=names)
                families[sample.name] = family
            families[sample.name].add_metric([value for _, value in sample.labels], sample.value)
        yield from families.values()
