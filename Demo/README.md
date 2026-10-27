# Demo files

Files to upload into IDS Spend Audit for a demo. Every value in them is made up. The spend files are laid out like the test sample (ENG-02 12.3): a Transactions sheet below two title lines, ending in a total row that matches its lines. To make them again, run `uv run python Demo/make_demo.py` from the repository root.

## The order

1. In a new report, upload `00 Mapping.xlsx` with **Upload a mapping**, and approve it. It's the GL and cost center mappings on their own (ENG-02 8.5), so its preview has no rows to reconcile.
2. Upload `01` to `05` with **Upload a file**, approving each before the next. Approving `01` chooses the report's duplicate mode: to try both, make two reports, choose **Replace by period** in one and **Line key** in the other, and upload the same files to both. The samples have no mapping sheets, so their lines take `00`'s mapping. Every line is mapped to a budget line and a department, so they add no exception, and the Exceptions screen stays empty.
3. Upload `06` to `10` the same way. Each fills one list of the Exceptions screen, and its preview shows it before approval.

**Both modes.** Each sample covers three months, and starts with the month the one before it ended with, listed again in full and unchanged. By period, a sample replaces the postings in its months. By line key, it adds only the lines not already in the report and counts the rest as duplicates, with the same amounts. Both reports therefore end each step with the same figures.

## The files

Each figure assumes the report holds every file above it. Amounts are in OMR.

| File | Posting dates | Lines | Control total | Replaced (by period) | Duplicates (line key) | Actual spend after approval |
| --- | --- | --- | --- | --- | --- | --- |
| `00 Mapping.xlsx` | None: a mapping | 0 | None | 0, 0.000 | 0, 0.000 | 0.000 |
| `01 Sample - Jul to Sep 2025.xlsx` | 02.07.2025 to 26.09.2025 | 23 | 478,022.050 | 0, 0.000 | 0, 0.000 | 478,022.050 |
| `02 Sample - Sep to Nov 2025.xlsx` | 04.09.2025 to 26.11.2025 | 23 | 209,620.300 | 8, 71,495.850 | 8, 71,495.850 | 616,146.500 |
| `03 Sample - Nov 2025 to Jan 2026.xlsx` | 04.11.2025 to 26.01.2026 | 23 | 176,802.839 | 7, 31,620.350 | 7, 31,620.350 | 761,328.989 |
| `04 Sample - Jan to Mar 2026.xlsx` | 04.01.2026 to 26.03.2026 | 22 | 212,251.575 | 9, 96,118.225 | 9, 96,118.225 | 877,462.339 |
| `05 Sample - Mar to May 2026.xlsx` | 04.03.2026 to 27.05.2026 | 22 | 227,457.050 | 7, 54,881.475 | 7, 54,881.475 | 1,050,037.914 |
| `06 Exceptions - No budget line - Jun 2026.xlsx` | 04.06.2026 to 26.06.2026 | 8 | 54,324.150 | 0, 0.000 | 0, 0.000 | 1,104,362.064 |
| `07 Exceptions - Outside IDS - Jul 2026.xlsx` | 04.07.2026 to 27.07.2026 | 11 | 103,295.600 | 0, 0.000 | 0, 0.000 | 1,207,657.664 |
| `08 Exceptions - No department - Aug 2026.xlsx` | 04.08.2026 to 26.08.2026 | 7 | 42,230.600 | 0, 0.000 | 0, 0.000 | 1,249,888.264 |
| `09 Exceptions - Mapping changes - Sep 2026.xlsx` | 04.09.2026 to 26.09.2026 | 6 | 38,818.600 | 0, 0.000 | 0, 0.000 | 1,288,706.864 |
| `10 Exceptions - Skipped rows - Oct 2026.xlsx` | 04.10.2026 to 26.10.2026 | 11 | 96,213.057 | 0, 0.000 | 0, 0.000 | 1,375,258.089 |

## What each exception file shows

- `06`, GL accounts with no budget line: `640200` IT contractors is missing from the GL mapping, with 23,600.000 OMR on 2 lines, and `650200` Conferences and travel is listed without a commitment item, with 4,725.300 OMR on 2 lines.
- `07`, cost centers outside IDS: `3100` with 15,050.500 OMR on 2 lines, and `3200` with 2,980.750 OMR on 2 lines. Neither is on the cost center mapping.
- `08`, a cost center with no department: `4570` Digital innovation lab is on the cost center mapping with no department code, with 16,062.750 OMR on 3 lines.
- `09`, changes between files: a spend file with its mapping sheets inside, which are `00`'s with two changes. `610300` Cloud services moves from `2101` to `2201`, moving 96,148.125 OMR, 89,308.625 OMR of it already approved. `4540` Network moves from `INF` to `SEC`, moving 189,614.400 OMR, 168,361.550 OMR of it already approved. Its mapping then applies to every line in the report. The Exceptions screen lists each change under `00` and `09`, the two files whose mapping lists the code, each with its own lines on it that still count: none for `00`, which has no lines, and September's for `09`.
- `10`, skipped rows, each with a readable amount, so the file still reconciles and can be approved:
  - Row 7, Repeats row 6: 6,900.875 OMR
  - Row 9, Unreadable code: 870.000 OMR
  - Row 14, More than 3 decimal places: 1,250.4567 OMR
  - Row 15, Invalid date: 640.500 OMR

The mapping lists `650200` and `4570` from the start, so `06` and `08` need no mapping of their own. No sample posts to them, so they show only once `06` and `08` do.
