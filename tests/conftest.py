"""Pytest configuration for the arm101_hand test tree.

Registers a `hardware` marker and a `--port` CLI option so hardware-dependent
tests are skipped by default and only run when the user explicitly opts in.

Run patterns
------------

    uv run pytest                              # unit + integration only
    uv run pytest -m 'not hardware'            # same as above, explicit
    uv run pytest -m hardware --port=COM18     # hand bus only
    uv run pytest -m hardware --port=COM20     # arm bus only

See docs/conventions/04-testing-verification.md for the full testing rules and
docs/plans/01-unified-gui-spec.md §11 for the planned test breakdown.
"""

from __future__ import annotations

import pytest


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--port",
        action="store",
        default=None,
        help="COM port for hardware-marked tests (e.g., COM18 for hand, COM20 for arm).",
    )


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "hardware: tests that require a real servo bus on the COM port passed via --port",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if config.getoption("--port"):
        return
    skip_hw = pytest.mark.skip(reason="--port not provided; hardware tests skipped")
    for item in items:
        if "hardware" in item.keywords:
            item.add_marker(skip_hw)


@pytest.fixture
def port(request: pytest.FixtureRequest) -> str:
    """Resolve the --port option for hardware tests; skips if absent."""
    value = request.config.getoption("--port")
    if value is None:
        pytest.skip("--port not provided")
    return value
