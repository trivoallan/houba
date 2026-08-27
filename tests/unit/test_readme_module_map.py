"""Drift test for the README layer module map.

Parses the ``knock/`` tree block in README.md and asserts that each layer lists
exactly what exists on disk, and that ``cli/`` lists exactly the verbs the Typer
app registers (``cli/`` also holds ``render`` and ``_di``, which are not verbs).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from knock.cli.main import app

REPO_ROOT = Path(__file__).parents[2]
PACKAGE_LAYERS = ("domain", "ports", "adapters", "use_cases")


def _readme_module_map() -> dict[str, set[str]]:
    """Parse README's ``knock/`` tree block into ``{layer: {entry, ...}}``."""
    block = re.search(
        r"^```\nknock/\n(.*?)^```",
        REPO_ROOT.joinpath("README.md").read_text(),
        re.S | re.M,
    )
    assert block is not None, "README.md no longer contains a ```knock/ ...``` tree block"

    entries: dict[str, set[str]] = {}
    layer: str | None = None
    for line in block.group(1).splitlines():
        head = re.match(r"[├└]── (\w+)/\s+.*?— (.*)", line)
        if head:
            layer, rest = head.group(1), head.group(2)
            entries[layer] = set()
        else:
            assert layer is not None, f"continuation line before any layer: {line!r}"
            rest = line.lstrip("│ ")
        entries[layer] |= {name.rstrip("/") for name in rest.replace(",", " ").split() if name}
    return entries


def _modules_on_disk(layer: str) -> set[str]:
    return {
        path.name.removesuffix(".py")
        for path in REPO_ROOT.joinpath("knock", layer).iterdir()
        if not path.name.startswith((".", "__")) and (path.is_dir() or path.suffix == ".py")
    }


def _registered_verbs() -> set[str]:
    commands = {cmd.name or cmd.callback.__name__ for cmd in app.registered_commands}
    return commands | {group.name for group in app.registered_groups if group.name}


@pytest.mark.parametrize("layer", PACKAGE_LAYERS)
def test_layer_lists_every_module(layer: str) -> None:
    assert _readme_module_map()[layer] == _modules_on_disk(layer)


def test_cli_lists_every_registered_verb() -> None:
    assert _readme_module_map()["cli"] == _registered_verbs()
