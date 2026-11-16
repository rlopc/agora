from pathlib import Path

import pytest

from agora.agents.prepare import agents, check_folders, load, prepare
from agora.tools import TOOLS

EXAMPLES = sorted((Path(__file__).parents[1] / "examples").glob("*/agora.toml"))


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: p.parent.name)
def test_example_is_valid(path):
    config = load(path)
    found = agents(config, path.parent, TOOLS)
    check_folders(found, TOOLS)
    assert all((p.folder / "AGENTS.md").is_file() for p in found)
    # Nothing is written into the repository.
    assert all(changes for changes in prepare(config, path.parent, TOOLS, write=False).values())


def test_there_are_examples():
    assert EXAMPLES
