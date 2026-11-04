"""Command-line entry point: corewatch-exporter --config config.yml

Installed under two names, corewatch-exporter and the original corewatch-db2.
"""

from __future__ import annotations

import argparse
import logging
import os
import signal
import threading

from prometheus_client import start_http_server
from prometheus_client.core import CollectorRegistry

from . import __version__
from collections import Counter

from .collector import Collector
from .config import ConfigError, load_config


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Prometheus exporter for Db2 and Informix on AIX and Linux")
    parser.add_argument("--config", default="config.yml", help="path to the YAML config file")
    parser.add_argument(
        "--engines",
        default=os.environ.get("COREWATCH_ENGINES", ""),
        help="comma-separated engines to monitor, e.g. db2,informix (default: every target in the config; "
        "env COREWATCH_ENGINES)",
    )
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)

    logging.basicConfig(level=args.log_level.upper(), format="%(asctime)s %(levelname)s %(message)s")
    log = logging.getLogger("corewatch")
    try:
        config = load_config(args.config, engines=args.engines)
    except ConfigError as exc:
        log.error("configuration error: %s", exc)
        return 2

    registry = CollectorRegistry()
    registry.register(Collector(config))
    start_http_server(config.port, addr=config.address, registry=registry)
    counts = Counter(db.engine for db in config.databases)
    log.info(
        "serving %d target(s) (%s) on %s:%d/metrics",
        len(config.databases),
        ", ".join(f"{engine} {n}" for engine, n in sorted(counts.items())),
        config.address,
        config.port,
    )

    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    stop.wait()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
