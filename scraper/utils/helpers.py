"""Shared utility helpers."""

import re
import time
import random


def slugify(text: str) -> str:
    """Convert text to a URL/filename-safe slug."""
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_-]+", "_", text)
    return text


def random_delay(min_sec: float = 1.5, max_sec: float = 4.0):
    """Sleep for a random duration to mimic human browsing."""
    time.sleep(random.uniform(min_sec, max_sec))


def clean_text(text: str | None) -> str:
    """Strip and normalize whitespace from text."""
    if not text:
        return ""
    return re.sub(r"\s+", " ", text).strip()


def parse_count(text: str | None) -> int | None:
    """
    Parse a human-friendly count like '1.2K', '4.5M', '300' into an integer.
    Returns None if unparseable.
    """
    if not text:
        return None
    text = text.strip().replace(",", "")
    multipliers = {"K": 1_000, "M": 1_000_000, "B": 1_000_000_000}
    match = re.match(r"([\d.]+)\s*([KMB])?", text, re.IGNORECASE)
    if not match:
        return None
    number = float(match.group(1))
    suffix = (match.group(2) or "").upper()
    return int(number * multipliers.get(suffix, 1))
