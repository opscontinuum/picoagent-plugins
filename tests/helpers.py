"""Bridge to picoagent's test fixtures.

These tests run against a picoagent checkout: its core is the runtime under test, and its
``tests/helpers.py`` carries the shared fixtures (``ScriptedProvider``, ``make_runtime``,
``temp_dir``...). Point ``PICOAGENT_ROOT`` at a checkout, or keep one as a sibling directory
named ``picoagent``. Re-exporting rather than copying keeps one definition of every fixture;
the price is the checkout, which the error below spells out.
"""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path


def _find_picoagent() -> Path:
    candidates = []
    if os.environ.get("PICOAGENT_ROOT"):
        candidates.append(Path(os.environ["PICOAGENT_ROOT"]).expanduser())
    here = Path(__file__).resolve()
    candidates += [here.parents[2] / "picoagent", here.parents[3] / "picoagent"]
    for root in candidates:
        if (root / "picoagent" / "__init__.py").is_file() and (root / "tests" / "helpers.py").is_file():
            return root.resolve()
    raise RuntimeError(
        "no picoagent checkout found: set PICOAGENT_ROOT to one, or clone "
        "https://github.com/opscontinuum/picoagent beside this repository")


PICOAGENT_ROOT = _find_picoagent()
_spec = importlib.util.spec_from_file_location("_picoagent_test_helpers",
                                               PICOAGENT_ROOT / "tests" / "helpers.py")
_mod = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _mod
_spec.loader.exec_module(_mod)
globals().update({name: value for name, value in vars(_mod).items() if not name.startswith("__")})
