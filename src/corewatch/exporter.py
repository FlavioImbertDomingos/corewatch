"""Command-line entry point: corewatch-db2 --config config.yml"""

from __future__ import annotations

import argparse
import logging
import signal
import threading

from prometheus_client import start_http_server
from prometheus_client.core import CollectorRegistry

from . import __version__
from .collector import Db2Collector
from .config import load_config


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Prometheus exporter for Db2 on AIX and Linux")
    parser.add_argument("--config", default="config.yml", help="path to the YAML config file")
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)

    logging.basicConfig(level=args.log_level.upper(), format="%(asctime)s %(levelname)s %(message)s")
    config = load_config(args.config)

    registry = CollectorRegistry()
    registry.register(Db2Collector(config))
    start_http_server(config.port, addr=config.address, registry=registry)
    logging.getLogger("corewatch").info(
        "serving %d database(s) on %s:%d/metrics", len(config.databases), config.address, config.port
    )

    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    stop.wait()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
