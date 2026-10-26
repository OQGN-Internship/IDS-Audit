# Real export

Put the client's real SAP export in this folder, with SAP's own totals for it in a JSON file named after it: `FAGLL03 2026-09.xlsx` and `FAGLL03 2026-09.expected.json`. ENG-02 12.2 lists what that file holds. The overall total and three GL accounts are enough to start, as in this example, whose figures are made up:

```json
{
  "source": "SAP totals from the validator, 2 November 2026",
  "actual_spend": {"total": "1250400.000"},
  "by_gl": {"610100": "182300.500", "620100": "96200.000", "630100": "41000.250"}
}
```

`tests/test_real_export.py` uploads each export into a new report and checks every figure its file gives. While this folder holds no expected result, the test is skipped, and `uv run pytest` prints why. Run `IDS_REQUIRE_REAL_EXPORT=1 uv run pytest` once the export is here, so a misnamed file fails instead.

The export is OQGN's data. Git ignores everything here except this README (ENG-01 6.4), and `tests/test_client_data_folders.py` fails if Git ever tracks anything else here. Nothing taken from the export goes into Git either: not a copy, a sample of its rows, a screenshot, or its expected results (CONTRIBUTING 7).

Never run `git clean -x` or `git clean -X`. They delete ignored files, and that includes the export in this folder (CONTRIBUTING 7).
