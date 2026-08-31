"""Layering contract (design doc §2.2 / §8).

No adapter module may import the pipeline layers directly — they go through
``signals_app.service`` only. This is the rule that stops the two-assembly
drift described in §1.1 from recurring.

Adapters covered: the FastAPI routes, the ``signals`` CLI. (The MCP server and
``scan_universe.py`` join this list in later build steps.)
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parent.parent / "src" / "signals_app"

# Modules that must only reach the engine via signals_app.service.
_ADAPTER_MODULES = [
    _SRC / "api" / "routes.py",
    _SRC / "cli" / "main.py",
]

# The layers an adapter is forbidden from importing directly.
_FORBIDDEN_SUBPACKAGES = {"detection", "scoring", "synthesis", "indicators", "data"}


def _imported_signals_app_modules(source: str) -> set[str]:
    """Return the ``signals_app.<sub>`` sub-packages a source file imports."""
    tree = ast.parse(source)
    hits: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            parts = node.module.split(".")
            if parts[:1] == ["signals_app"] and len(parts) >= 2:
                hits.add(parts[1])
        elif isinstance(node, ast.Import):
            for alias in node.names:
                parts = alias.name.split(".")
                if parts[:1] == ["signals_app"] and len(parts) >= 2:
                    hits.add(parts[1])
    return hits


@pytest.mark.parametrize("module_path", _ADAPTER_MODULES, ids=lambda p: p.as_posix())
def test_adapter_does_not_import_pipeline_layers(module_path: Path) -> None:
    """An adapter importing detection/scoring/synthesis/indicators/data fails here."""
    assert module_path.exists(), f"adapter module missing: {module_path}"
    imported = _imported_signals_app_modules(module_path.read_text())
    leaked = imported & _FORBIDDEN_SUBPACKAGES
    assert not leaked, (
        f"{module_path.name} imports pipeline layer(s) {sorted(leaked)} directly — "
        f"it must go through signals_app.service instead (design doc §2.2)."
    )


def test_service_is_importable_and_exposes_the_surface() -> None:
    """The seam exists and exports every function the adapters rely on."""
    from signals_app import service

    for name in (
        "analyze",
        "analyze_many",
        "backtest",
        "backtest_many",
        "history",
        "detectors",
        "health",
        "SignalsError",
        "SymbolNotFound",
        "InsufficientData",
        "InvalidPeriod",
        "UpstreamUnavailable",
    ):
        assert hasattr(service, name), f"signals_app.service is missing {name}"
