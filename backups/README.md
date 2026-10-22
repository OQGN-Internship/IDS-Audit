# Backups

`scripts/backup.sh` writes each backup into this folder, one sub-folder per backup (ENG-01 7.2).

A backup holds the client's real data. Git ignores everything here except this README (ENG-01 6.4), and `tests/test_client_data_folders.py` fails if Git ever tracks anything else here.

Never run `git clean -x` or `git clean -X`. They delete ignored files, and that includes every backup in this folder (CONTRIBUTING 7).
