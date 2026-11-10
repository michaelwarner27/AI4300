"""
Logging configuration for the vector ingestion pipeline.
Provides high-level progress logging without verbose noise.
"""

import logging
import sys


def setup_logging(level: int = logging.INFO) -> logging.Logger:
    """
    Configure logging for the ingestion pipeline.
    
    Args:
        level: Logging level (default: INFO for high-level progress)
    
    Returns:
        Configured logger instance
    """
    logger = logging.getLogger("vector_ingestion")
    logger.setLevel(level)
    
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
    
    return logger


def get_logger() -> logging.Logger:
    """Get the configured logger for the ingestion pipeline."""
    return logging.getLogger("vector_ingestion")
