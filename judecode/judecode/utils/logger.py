"""
Logging configuration for Jude Code.

ตั้งค่า logging ให้เขียนทั้ง console และ file (judecode.log)
เพื่อให้เวลาเกิด error สามารถตรวจสอบจาก log ย้อนหลังได้
"""

import os
import sys
import logging
from pathlib import Path
from logging.handlers import RotatingFileHandler

# ── Default log path ──
# ใช้ XDG_DATA_HOME, ~/.judecode/logs, หรือ current directory
_XDG_DATA_HOME = os.environ.get("XDG_DATA_HOME", "")
if _XDG_DATA_HOME:
    _LOG_DIR = Path(_XDG_DATA_HOME) / "judecode" / "logs"
else:
    _LOG_DIR = Path.home() / ".judecode" / "logs"

_LOG_DIR.mkdir(parents=True, exist_ok=True)
_LOG_FILE = _LOG_DIR / "judecode.log"

# ── Log format ──
_FILE_FORMAT = (
    "[%(asctime)s] %(levelname)-8s %(name)s | %(message)s"
)
_CONSOLE_FORMAT = "%(levelname)s: %(message)s"

# ── Formatters ──
_file_formatter = logging.Formatter(
    _FILE_FORMAT,
    datefmt="%Y-%m-%d %H:%M:%S",
)
_console_formatter = logging.Formatter(_CONSOLE_FORMAT)


def setup_logger(
    name: str = "judecode",
    level: int = logging.INFO,
    log_file: str | None = None,
    max_bytes: int = 10 * 1024 * 1024,   # 10 MB
    backup_count: int = 5,
    console_debug: bool = False,
) -> logging.Logger:
    """Set up and return a logger with file + optional console handler.

    Args:
        name: Logger name (default: 'judecode')
        level: Logging level (default: logging.INFO)
        log_file: Path to log file (default: ~/.judecode/logs/judecode.log)
        max_bytes: Max log file size before rotation
        backup_count: Number of rotated backup files to keep
        console_debug: If True, also log to stderr at the same level

    Returns:
        Configured logger instance
    """
    logger = logging.getLogger(name)
    logger.setLevel(level)

    # ── Prevent duplicate handlers on repeated calls ──
    if logger.handlers:
        return logger

    # ── File handler (rotating) ──
    log_path = Path(log_file) if log_file else _LOG_FILE
    try:
        file_handler = RotatingFileHandler(
            log_path,
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
        )
        file_handler.setFormatter(_file_formatter)
        file_handler.setLevel(level)
        logger.addHandler(file_handler)
    except (OSError, PermissionError) as e:
        # ถ้าเขียน log file ไม่ได้ → fallback ใช้ stderr
        fallback_handler = logging.StreamHandler(sys.stderr)
        fallback_handler.setFormatter(_console_formatter)
        fallback_handler.setLevel(logging.WARNING)
        logger.addHandler(fallback_handler)
        logger.warning(f"Cannot open log file {log_path}: {e}. Falling back to stderr.")

    # ── Optional console handler (for debug mode) ──
    if console_debug:
        console_handler = logging.StreamHandler(sys.stderr)
        console_handler.setFormatter(_console_formatter)
        console_handler.setLevel(level)
        logger.addHandler(console_handler)

    return logger


# ── Convenience: default logger ──
_default_logger: logging.Logger | None = None


def get_logger(name: str = "judecode") -> logging.Logger:
    """Get the default application logger (lazy-init)."""
    global _default_logger
    if _default_logger is None:
        _default_logger = setup_logger(name)
    # รับ child logger ตาม name ที่ขอ
    return logging.getLogger(name)


def log_error_details(
    logger: logging.Logger,
    message: str,
    exc_info: bool = True,
    extra: dict | None = None,
) -> None:
    """Log an error with full details including traceback and extra context.

    Args:
        logger: Logger instance
        message: Error message
        exc_info: Include exception traceback (default: True)
        extra: Optional dict of extra context to include
    """
    if extra:
        extra_str = " | ".join(f"{k}={v!r}" for k, v in extra.items())
        full_message = f"{message} | {extra_str}"
    else:
        full_message = message
    logger.error(full_message, exc_info=exc_info)


# ── Clean up on module reload ──
def reset_logger(name: str = "judecode") -> None:
    """Remove all handlers from a logger (useful for testing)."""
    logger = logging.getLogger(name)
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
    global _default_logger
    if name == "judecode":
        _default_logger = None
