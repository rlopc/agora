"""Update configuration files touching only our own keys.

Whatever is there is read (with or without BOM), the changes are applied and it is only written if the
result differs: this keeps comments, order and whatever each tool adds, and repeating changes nothing.
"""

from __future__ import annotations

import copy
import json
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import tomlkit
from tomlkit.items import Table


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        return ""


def _save(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def update_toml(path: Path, changes: Callable[[tomlkit.TOMLDocument], None], write: bool) -> bool:
    before = _read(path)
    document = tomlkit.parse(before)
    changes(document)
    after = tomlkit.dumps(document)
    if after == before:
        return False
    if write:
        _save(path, after)
    return True


def update_json(path: Path, changes: Callable[[dict[str, Any]], None], write: bool) -> bool:
    text = _read(path)
    before = json.loads(text) if text.strip() else {}
    data = copy.deepcopy(before)
    changes(data)
    if data == before and text:
        return False
    if write:
        _save(path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    return True


def update_text(path: Path, text: str, write: bool) -> bool:
    """For files that are entirely ours."""
    if _read(path) == text:
        return False
    if write:
        _save(path, text)
    return True


def table(container: Any, key: str) -> Table:
    """Subtable `key` of `container`, creating it if missing."""
    if key not in container:
        container[key] = tomlkit.table()
    return container[key]


def put(container: Any, key: str, value: Any) -> None:
    """Set `key` only if it changes, so as not to reformat what is already fine."""
    current = container.get(key)
    if hasattr(current, "unwrap"):
        current = current.unwrap()
    if current != value:
        container[key] = value


def at_path(container: Any, keys: Sequence[str]) -> Table:
    for key in keys:
        container = table(container, key)
    return container
