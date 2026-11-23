"""
Logging configuration for the vector ingestion pipeline.
Provides high-level progress logging without verbose noise.
"""

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOG_FILE_MAX_BYTES = 1_000_000
LOG_FILE_BACKUPS = 3


def setup_logging(
    level: int = logging.INFO,
    log_file: str | Path | None = None,
    file_level: int = logging.INFO,
) -> logging.Logger:
    """
    Configure logging for the ingestion pipeline.

    Args:
        level: Console logging level (default: INFO for high-level progress)
        log_file: Optional path of a rotating log file (directory created if needed)
        file_level: Logging level for the log file (default: INFO), independent of
            the console, so e.g. the web app can keep the console quiet while the
            file records agent details

    Returns:
        Configured logger instance
    """
    logger = logging.getLogger("vector_ingestion")
    logger.setLevel(min(level, file_level) if log_file else level)

    # Only add handler if not already configured
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setLevel(level)

        # Format: [LEVEL] message
        formatter = logging.Formatter(
            fmt="[%(levelname)s] %(message)s",
            datefmt="%H:%M:%S"
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)

    if log_file:
        path = Path(log_file).resolve()
        already = any(
            isinstance(h, RotatingFileHandler) and Path(h.baseFilename) == path
            for h in logger.handlers
        )
        if not already:
            path.parent.mkdir(parents=True, exist_ok=True)
            file_handler = RotatingFileHandler(
                path, maxBytes=LOG_FILE_MAX_BYTES, backupCount=LOG_FILE_BACKUPS, encoding="utf-8"
            )
            file_handler.setLevel(file_level)
            file_handler.setFormatter(logging.Formatter(
                fmt="%(asctime)s [%(levelname)s] %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            ))
            logger.addHandler(file_handler)

    return logger


def get_logger() -> logging.Logger:
    """Get the configured logger for the ingestion pipeline."""
    return logging.getLogger("vector_ingestion")
