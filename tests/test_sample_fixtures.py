"""The generated sample files (ENG-02 12.3, Tested by)."""

import json
from collections.abc import Iterable
from decimal import Decimal
from pathlib import Path

import pytest

from tests.fixtures import make_fixtures

FIXTURES_DIR = make_fixtures.FIXTURES_DIR
EXPECTED_RESULTS = ["sample.expected.json", "sample_semicolon.expected.json"]


def test_running_the_script_again_changes_no_fixture(tmp_path: Path) -> None:
    """Verify rerunning the generator leaves fixture files unchanged."""
    changed = [
        fresh.name
        for fresh in make_fixtures.write_all(tmp_path)
        if fresh.read_bytes() != (FIXTURES_DIR / fresh.name).read_bytes()
    ]
    assert changed == [], "Run: uv run python -m tests.fixtures.make_fixtures"


def add_up(amounts: Iterable[str]) -> Decimal:
    """Return the sum of the given monetary amounts."""
    return sum((Decimal(amount) for amount in amounts), Decimal("0.000"))


@pytest.mark.parametrize("name", EXPECTED_RESULTS)
def test_expected_totals_add_up(name: str) -> None:
    """Verify each expected breakdown sums to the actual spend total."""
    result = json.loads((FIXTURES_DIR / name).read_text(encoding="utf-8"))
    spend = result["actual_spend"]
    total = Decimal(spend["total"])

    for breakdown in ("by_gl", "by_ci", "by_department", "by_month"):
        assert add_up(result[breakdown].values()) == total, breakdown
    assert add_up([spend["inside_ids"], spend["outside_ids"]]) == total
    assert result["reconciliation"]["difference"] == "0.000"
