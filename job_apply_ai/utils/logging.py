"""Single place that configures logging for the package."""

from __future__ import annotations

import logging

from job_apply_ai.config import get_config

_CONFIGURED = False


def configure_logging(level: str | None = None) -> None:
    """Install a root handler. Safe to call many times — only runs once."""
    global _CONFIGURED
    if _CONFIGURED:
        return

    resolved = (level or get_config().log_level).upper()
    logging.basicConfig(
        level=getattr(logging, resolved, logging.INFO),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    """Return a module-scoped logger, ensuring logging is configured."""
    configure_logging()
    return logging.getLogger(name)
