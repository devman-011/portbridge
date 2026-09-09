"""Rotating file logger + `portbridge logs` tail/follow support."""

from __future__ import annotations

import logging
import logging.handlers
import time

from portbridge import paths

_LOGGER_NAME = "portbridge"


def get_logger(level: str = "INFO", max_bytes: int = 5_242_880, backup_count: int = 3) -> logging.Logger:
    paths.ensure_dirs()
    logger = logging.getLogger(_LOGGER_NAME)
    if logger.handlers:
        return logger
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    handler = logging.handlers.RotatingFileHandler(
        paths.log_file(), maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8"
    )
    handler.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%Y-%m-%d %H:%M:%S")
    )
    logger.addHandler(handler)
    logger.propagate = False
    return logger


def tail_lines(n: int = 50) -> list[str]:
    path = paths.log_file()
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        lines = fh.readlines()
    return [line.rstrip("\n") for line in lines[-n:]]


def follow() -> None:
    path = paths.log_file()
    paths.ensure_dirs()
    if not path.exists():
        path.touch()
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        fh.seek(0, 2)  # seek to end
        try:
            while True:
                line = fh.readline()
                if not line:
                    time.sleep(0.5)
                    # Handle log rotation / truncation transparently.
                    if fh.tell() > path.stat().st_size:
                        fh.seek(0)
                    continue
                print(line.rstrip("\n"))
        except KeyboardInterrupt:
            print()
