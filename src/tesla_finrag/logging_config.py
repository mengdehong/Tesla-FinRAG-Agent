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
_FONT_BBOX_WARNING = (
    "Could not get FontBBox from font descriptor because None cannot be parsed as 4 floats"
)

_configured = False
_pdfminer_filter_installed = False


class _PdfMinerNoiseFilter(logging.Filter):
    """Suppress known non-fatal pdfminer font metadata noise."""

    def filter(self, record: logging.LogRecord) -> bool:
        return _FONT_BBOX_WARNING not in record.getMessage()


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
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    _configured = True


def suppress_pdfminer_font_warnings() -> None:
    """Suppress the known non-fatal FontBBox warning from pdfminer."""
    global _pdfminer_filter_installed  # noqa: PLW0603
    if _pdfminer_filter_installed:
        return

    logging.getLogger("pdfminer.pdffont").addFilter(_PdfMinerNoiseFilter())
    _pdfminer_filter_installed = True


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


def configure_cli_logging(level: str | None = None) -> None:
    """Configure CLI logging and suppress known non-fatal parser noise."""
    # Import here to avoid circular imports at module level.
    from tesla_finrag.settings import settings  # noqa: PLC0415

    _configure_root(level or settings.log_level)
    suppress_pdfminer_font_warnings()
