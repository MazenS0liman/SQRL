"""Import smoke test for every module under ``squirrel``.

A module that cannot be imported is broken for every caller, so this is a
cheap, broad guard against syntax errors, circular imports, and missing
optional-dependency fallbacks. Individual behaviours are covered by the
focused test modules.
"""
from __future__ import annotations

import importlib
from pathlib import Path

import pytest

PACKAGE_DIR = Path(__file__).resolve().parents[1] / "squirrel"


def _module_names() -> list[str]:
    names: list[str] = []
    for path in sorted(PACKAGE_DIR.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        relative = path.relative_to(PACKAGE_DIR.parent).with_suffix("")
        parts = list(relative.parts)
        if parts[-1] == "__init__":
            parts.pop()
        names.append(".".join(parts))
    return names


MODULES = _module_names()


@pytest.mark.parametrize("module_name", MODULES)
def test_module_imports(module_name: str) -> None:
    assert importlib.import_module(module_name) is not None
