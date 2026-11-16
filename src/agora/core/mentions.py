"""Extraction of `@id` mentions from a message's text."""

from __future__ import annotations

import re
from collections.abc import Collection

_MENTION = re.compile(r"(?<![\w@])@([a-z0-9][a-z0-9_-]*)", re.IGNORECASE)


def extract_mentions(text: str, known: Collection[str]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Returns (valid mentions, unknown mentions), without duplicates and in order of appearance."""
    valid: list[str] = []
    unknown: list[str] = []
    for match in _MENTION.finditer(text):
        name = match.group(1).lower()
        target = valid if name in known else unknown
        if name not in target:
            target.append(name)
    return tuple(valid), tuple(unknown)
