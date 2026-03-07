"""Logging helpers for the Tesla FinRAG system.

Provides a single ``get_logger`` factory that all modules use so that the
log format, level, and handler configuration is defined in one place.

Usage::

    from tesla_finrag.logging_config import get_logger

    logger = get_logger(__name__)
    logger.info("Processing filing %s", doc_id)
"""

from __future__ import annotations

import logging
import sys

_LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
_DATE_FORMAT = "%Y-%m-%dT%H:%M:%S"

_configured = False


def _configure_root(level: str) -> None:
    """Idempotently configure the root logger with a stream handler."""
    global _configured  # noqa: PLW0603 — intentional module-level flag
    if _configured:
        return
    logging.basicConfig(
        level=level.upper(),
        format=_LOG_FORMAT,
        datefmt=_DATE_FORMAT,
        stream=sys.stdout,
        force=False,
    )
    _configured = True


def get_logger(name: str, level: str | None = None) -> logging.Logger:
    """Return a named logger, bootstrapping root config on first call.

    Args:
        name: Typically ``__name__`` of the calling module.
        level: Override log level for this logger only.  Defaults to the
            application setting (``settings.log_level``).

    Returns:
        A standard :class:`logging.Logger` instance.
    """
    # Import here to avoid circular imports at module level.
    from tesla_finrag.settings import settings  # noqa: PLC0415

    _configure_root(settings.log_level)
    logger = logging.getLogger(name)
    if level is not None:
        logger.setLevel(level.upper())
    return logger
