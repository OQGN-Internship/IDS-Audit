"""The real export against SAP's own figures (ENG-02 12.2): the plan's Phase 1
gate and Acceptance, once the export is in real-export/.

Then the checks themselves, on the sample file in a folder of its own, since
the real export is never in the repository.
"""

import json
import shutil
import tomllib
from pathlib import Path

import pytest

from tests import real_export
from tests.files import HEADER, csv_file, line, total_row

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def real_exports() -> list:
    """Return a parameter for each expected result in real-export/, or one
    that skips the test when there's none.
    """
    found = real_export.expected_results(real_export.REAL_EXPORT_DIR)
    return [
        pytest.param(path, id=path.name[: -len(real_export.EXPECTED)]) for path in found
    ] or [pytest.param(None, id="none")]


@pytest.mark.django_db
@pytest.mark.usefixtures("uploads_dir")
@pytest.mark.parametrize("expected_path", real_exports())
def test_the_real_export_gives_its_expected_figures(expected_path) -> None:
    """Verify each real export, uploaded into a new report, reconciles and
    gives every figure its expected result holds, to the baisa.
    """
    if expected_path is None:
        real_export.skip_or_fail(real_export.REAL_EXPORT_DIR)
    assert real_export.check(expected_path) == []


def test_an_empty_folder_still_runs_the_test_to_skip_it(monkeypatch, tmp_path) -> None:
    """Verify a folder with no expected result gives the test one run, which
    skips with its reason, rather than no run at all, which pytest wouldn't
    mention.
    """
    monkeypatch.setattr(real_export, "REAL_EXPORT_DIR", tmp_path)
    (param,) = real_exports()
    assert param.values == (None,)


def test_pytest_prints_why_a_test_was_skipped() -> None:
    """Verify pytest's options print skip reasons, so the real-export test's
    reason shows in every run (ENG-02 12.2).
    """
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    with open(pyproject, "rb") as file:
        options = tomllib.load(file)["tool"]["pytest"]["ini_options"]["addopts"]
    assert "-rs" in options.split()


def test_every_real_export_has_its_expected_result() -> None:
    """Verify no export in real-export/ goes unchecked."""
    assert real_export.exports_without_expected(real_export.REAL_EXPORT_DIR) == []


# The checks, on made-up files


def sample_folder(folder: Path, **changes) -> Path:
    """Copy the sample into the folder as an export, with an expected result
    made from its own, changed by changes, and return the expected result.
    """
    shutil.copy(FIXTURES_DIR / "sample.xlsx", folder / "Sample export.xlsx")
    with open(FIXTURES_DIR / "sample.expected.json", encoding="utf-8") as file:
        sample = json.load(file)
    expected = {"source": "The sample's own expected result"}
    expected |= {key: sample[key] for key in real_export.FIGURES}
    return write(folder / "Sample export.expected.json", {**expected, **changes})


def write(path: Path, expected: dict) -> Path:
    """Write an expected result and return its path."""
    path.write_text(json.dumps(expected), encoding="utf-8")
    return path


def csv_export(folder: Path, data: bytes, **expected) -> Path:
    """Put a CSV export in the folder, with its expected result."""
    (folder / "spend.csv").write_bytes(data)
    return write(folder / "spend.expected.json", {"source": "Made up", **expected})


@pytest.mark.django_db
@pytest.mark.usefixtures("uploads_dir")
class TestTheCheck:
    def test_the_sample_gives_its_own_figures(self, tmp_path) -> None:
        """Verify the sample, with its expected figures, passes."""
        assert real_export.check(sample_folder(tmp_path)) == []

    def test_a_figure_a_baisa_out_is_named(self, tmp_path) -> None:
        """Verify a figure changed by 0.001, and a code the report hasn't got,
        are each named with both amounts.
        """
        expected = sample_folder(
            tmp_path,
            actual_spend={"total": "1627363.452"},
            by_gl={"610100": "1195697.539", "999999": "1.000"},
        )
        assert real_export.check(expected) == [
            "actual_spend total: the report gives 1627363.451, the expected "
            "result 1627363.452.",
            "by_gl 999999: the report gives nothing, the expected result 1.000.",
        ]

    def test_only_the_figures_given_are_checked(self, tmp_path) -> None:
        """Verify an overall total and one GL account, as SAP gives them, are
        enough, and an amount written as a JSON number is read exactly.
        """
        expected = write(
            tmp_path / "Sample export.expected.json",
            {"source": "SAP", "actual_spend": {"total": 1627363.451}},
        )
        shutil.copy(FIXTURES_DIR / "sample.xlsx", tmp_path / "Sample export.xlsx")
        assert real_export.check(expected) == []

    def test_settings_and_a_typed_total_are_used(self, tmp_path) -> None:
        """Verify a CSV whose amount header only a changed ruleset recognises,
        with no total row, passes with its settings and SAP's typed total, and
        not without them.
        """
        header = ("Betrag" if h == "Amount in LC" else h for h in HEADER)
        data = csv_file(line(), header=tuple(header))
        figures = {"actual_spend": {"total": "1000.000"}}
        expected = csv_export(
            tmp_path,
            data,
            settings={"header_names": {"amount": ["Betrag"]}},
            control_total="1000.000",
            **figures,
        )
        assert real_export.check(expected) == []
        without_total = csv_export(
            tmp_path, data, settings={"header_names": {"amount": ["Betrag"]}}
        )
        assert real_export.check(without_total) == [
            "Approval is blocked: no_control_total, None."
        ]
        (problem,) = real_export.check(csv_export(tmp_path, data, **figures))
        assert problem.startswith("The upload needs columns:")

    def test_a_blocking_exception_is_named(self, tmp_path) -> None:
        """Verify a file whose rows don't add up to its total fails, naming
        the exception and its amount.
        """
        expected = csv_export(tmp_path, csv_file(line(), total_row("999.000")))
        assert real_export.check(expected) == [
            "Approval is blocked: difference_not_zero, -1.000."
        ]

    def test_a_skipped_row_must_be_listed(self, tmp_path) -> None:
        """Verify a row skipped while reading fails unless the expected result
        lists it.
        """
        data = csv_file(
            line(),
            line(DocumentNo="2", Pstng_Date="31.02.2026", Amount_in_LC="5.000"),
            total_row("1005.000"),
        )
        assert real_export.check(csv_export(tmp_path, data)) == [
            "Rows skipped: [{'row': 3, 'reason': 'Invalid date'}]. Rows listed: []."
        ]
        listed = [{"row": 3, "reason": "Invalid date"}]
        assert real_export.check(csv_export(tmp_path, data, skipped_rows=listed)) == []

    def test_an_expected_result_without_its_export_or_source(self, tmp_path) -> None:
        """Verify an expected result needs its source and one export named
        like it, and a key it doesn't use is named.
        """
        expected = write(tmp_path / "Lost.expected.json", {"by_GL": {}})
        assert real_export.check(expected) == [
            "The expected result has a key it doesn't use: by_GL.",
            "The expected result doesn't say its source.",
            "There must be one export named Lost.",
        ]


def test_a_folder_with_no_expected_result_skips_or_fails(tmp_path, monkeypatch) -> None:
    """Verify the skip names the folder, and fails instead with
    IDS_REQUIRE_REAL_EXPORT=1.
    """
    monkeypatch.delenv(real_export.REQUIRE, raising=False)
    with pytest.raises(pytest.skip.Exception, match="holds no expected result"):
        real_export.skip_or_fail(tmp_path)
    monkeypatch.setenv(real_export.REQUIRE, "1")
    with pytest.raises(pytest.fail.Exception) as failed:
        real_export.skip_or_fail(tmp_path)
    assert str(tmp_path) in str(failed.value)


def test_an_export_without_its_expected_result_is_found(tmp_path) -> None:
    """Verify each export with no expected result is named, and that the
    README, notes and an export with its expected result aren't.
    """
    for name in ("FAGLL03 Sep.xlsx", "FAGLL03 Oct.XLSX", "lines.csv", "README.md"):
        (tmp_path / name).write_bytes(b"")
    (tmp_path / "notes.txt.md").write_bytes(b"")
    write(tmp_path / "FAGLL03 Sep.expected.json", {"source": "SAP"})
    assert real_export.exports_without_expected(tmp_path) == [
        "FAGLL03 Oct.XLSX",
        "lines.csv",
    ]
