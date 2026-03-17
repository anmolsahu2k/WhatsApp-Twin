"""Logging configuration for WhatsApp Twin."""

import logging
import sys


def setup_logging(level: int = logging.INFO) -> None:
    """Configure the root whatsapp_twin logger.

    Call once at app startup (menubar, terminal, or CLI).
    """
    logger = logging.getLogger("whatsapp_twin")
    if logger.handlers:
        return  # already configured

    logger.setLevel(level)

    handler = logging.StreamHandler(sys.stderr)
    handler.setLevel(level)
    formatter = logging.Formatter(
        "[%(levelname).1s] %(name)s: %(message)s"
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)


def get_logger(name: str) -> logging.Logger:
    """Get a child logger under the whatsapp_twin namespace.

    Usage:
        log = get_logger(__name__)
        log.info("Imported %d messages", count)
    """
    return logging.getLogger(f"whatsapp_twin.{name}")
