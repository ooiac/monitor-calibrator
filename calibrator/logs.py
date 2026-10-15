"""A small log file next to the backups. The window has no console when started by double-click."""
from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler

log = logging.getLogger("calibrator")


def setup() -> None:
    from . import backup  # imported here so any module can use `log` without import cycles

    if log.handlers:
        return
    log.setLevel(logging.INFO)
    try:
        backup.DATA_DIR.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(backup.DATA_DIR / "app.log", maxBytes=512_000, backupCount=2, encoding="utf-8")
    except OSError:
        handler = logging.NullHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.addHandler(handler)
