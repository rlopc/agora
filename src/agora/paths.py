"""Comparison of paths as Claude Code and Codex write them."""

from __future__ import annotations

import os


def same_path(a: str, b: str) -> bool:
    """Equal except for repeated separators and trailing slash."""
    return os.path.normpath(str(a)) == os.path.normpath(str(b))
