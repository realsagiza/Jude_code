"""Jude Code - Your AI coding assistant on terminal."""

import logging

__version__ = "0.1.0"

# ── Initialize root logger on first import ──
from judecode.utils.logger import setup_logger

setup_logger("judecode", level=logging.INFO)
