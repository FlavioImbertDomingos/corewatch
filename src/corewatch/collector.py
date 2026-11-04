"""Run the queries against each target and expose the results to Prometheus.

A target is a Db2 database or an Informix server. Both go through the same
steps: connect (or reuse the connection), run the engine's test query, then run
each monitoring query on its own so one failure cannot hide the others.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable, Iterable

from prometheus_client.core import CounterMetricFamily, GaugeMetricFamily

from . import __version__
from .config import Config, DatabaseConfig
from .db import Connection, connect
from .engines import ENGINES, Engine
from .queries import COUNTER, Query, Sample

log = logging.getLogger("corewatch")

# Metrics every engine reports about the collection itself, by suffix.
EXPORTER_METRICS = {
    "up": "1 if the exporter connected and ran a test query.",
    "scrape_duration_seconds": "Time taken to collect from this target.",
    "query_success": "1 if the named monitoring query ran, 0 if it failed.",
}
GLOBAL_METRICS = {
    "corewatch_engine_enabled": "1 for each engine this exporter is configured to monitor.",
    "corewatch_build_info": "Exporter version.",
}
ALL_METRICS = {name: spec for engine in ENGINES.values() for name, spec in engine.metrics.items()}


class Collector:
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
        self.queries: dict[str, list[Query]] = {
            name: [q for q in engine.queries if not q.optional or q.name in config.optional_queries]
            for name, engine in ENGINES.items()
        }

    # -- collection -------------------------------------------------------

    def _base_labels(self, db: DatabaseConfig, engine: Engine) -> tuple[tuple[str, str], ...]:
        labels = [(engine.target_label, db.name), ("db_host", db.host)]
        labels += [(name, db.labels.get(name, "")) for name in self.config.label_names]
        return tuple(labels)

    def _key(self, db: DatabaseConfig) -> str:
        return f"{db.engine}://{db.host}:{db.port}/{db.name}"

    def _connection(self, db: DatabaseConfig) -> Connection:
        key = self._key(db)
        if key not in self._connections:
            self._connections[key] = self._connector(db.dsn())
        return self._connections[key]

    def _drop(self, db: DatabaseConfig) -> None:
        conn = self._connections.pop(self._key(db), None)
        if conn is not None:
            conn.close()

    def _collect_target(self, db: DatabaseConfig) -> Iterable[Sample]:
        engine = ENGINES[db.engine]
        p = engine.prefix
        base = self._base_labels(db, engine)
        started = self._clock()
        try:
            conn = self._connection(db)
            conn.query(engine.probe_sql)
        except Exception as exc:  # the target is down or unreachable
            log.warning("cannot reach %s: %s", self._key(db), exc)
            self._drop(db)
            yield Sample(f"{p}_up", 0.0, base)
            yield Sample(f"{p}_scrape_duration_seconds", self._clock() - started, base)
            return
        yield Sample(f"{p}_up", 1.0, base)
        for query in self.queries[db.engine]:
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
            yield Sample(f"{p}_query_success", ok, base + (("query", query.name),))
        yield Sample(f"{p}_scrape_duration_seconds", self._clock() - started, base)

    def samples(self) -> list[Sample]:
        with self._lock:
            now = self._clock()
            if self._cache is None or now - self._cache_time >= self.config.cache_seconds:
                fresh: list[Sample] = [Sample("corewatch_build_info", 1.0, (("version", __version__),))]
                for name in self.config.engines:
                    fresh.append(Sample("corewatch_engine_enabled", 1.0, (("engine", name),)))
                for db in self.config.databases:
                    fresh.extend(self._collect_target(db))
                self._cache = fresh
                self._cache_time = now
            return list(self._cache)

    # -- prometheus_client interface --------------------------------------

    @staticmethod
    def _describe(name: str) -> tuple[str, str]:
        if name in ALL_METRICS:
            kind, doc, _ = ALL_METRICS[name]
            return kind, doc
        if name in GLOBAL_METRICS:
            return "gauge", GLOBAL_METRICS[name]
        suffix = name.split("_", 1)[1]
        return "gauge", EXPORTER_METRICS[suffix]

    def describe(self):
        return []

    def collect(self):
        families = {}
        for sample in self.samples():
            names = [name for name, _ in sample.labels]
            if sample.name not in families:
                kind, doc = self._describe(sample.name)
                if kind == COUNTER:
                    # prometheus_client adds the _total suffix itself
                    family = CounterMetricFamily(sample.name.removesuffix("_total"), doc, labels=names)
                else:
                    family = GaugeMetricFamily(sample.name, doc, labels=names)
                families[sample.name] = family
            families[sample.name].add_metric([value for _, value in sample.labels], sample.value)
        yield from families.values()


# The original name, kept so existing imports and deployments keep working.
Db2Collector = Collector
