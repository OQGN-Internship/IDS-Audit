# Real export

Put the client's real SAP export in this folder, with SAP's own totals for it: the overall total and the named GL accounts (ENG-02 12.2). The real-export test reads them from here, and is skipped while this folder holds no export.

The export is OQGN's data. Git ignores everything here except this README (ENG-01 6.4), and `tests/test_client_data_folders.py` fails if Git ever tracks anything else here. Nothing taken from the export goes into Git either: not a copy, a sample of its rows, a screenshot, or its expected results (CONTRIBUTING 7).

Never run `git clean -x` or `git clean -X`. They delete ignored files, and that includes the export in this folder (CONTRIBUTING 7).
