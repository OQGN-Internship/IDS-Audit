"""The backup and restore commands and scripts, without Docker (ENG-01 7.2).
The restore itself is tested by the Phase 4 gate, in test_backup_restore.py.
"""

import hashlib
import os
from pathlib import Path

import pytest
from django.core.management import CommandError, call_command

from ids_audit.audit.models import AuditEvent
from ids_audit.imports.services import receive_file
from tests.factories import make_report
from tests.files import LINE, csv_file, total_row
from tests.scripts import git_bash, posix, run_script

PARTS = {"db.dump": b"dump", "uploads.tar": b"tar", "totals.json": b'{"reports": []}'}


@pytest.fixture
def bash() -> str:
    found = git_bash()
    if found is None:
        pytest.skip("Bash isn't installed.")
    return found


@pytest.fixture
def no_docker(tmp_path) -> tuple[dict, Path]:
    """An environment whose docker only notes that it was called, so a
    script that got past its checks would change nothing, and the test can
    tell.
    """
    tools = tmp_path / "tools"
    tools.mkdir()
    called = tmp_path / "docker-called"
    (tools / "docker").write_text(
        f'#!/usr/bin/env bash\necho "$@" >> "{posix(called)}"\nexit 1\n',
        encoding="utf-8",
        newline="\n",
    )
    env = {
        **os.environ,
        "PATH": f"{tools}{os.pathsep}{os.environ['PATH']}",
        "COMPOSE_PROJECT_NAME": "ids-audit-test-nothing",
    }
    return env, called


def backup_folder(folder: Path) -> Path:
    """A backup folder whose three files match its SHA256SUMS."""
    folder.mkdir(parents=True)
    lines = []
    for name, data in PARTS.items():
        (folder / name).write_bytes(data)
        lines.append(f"{hashlib.sha256(data).hexdigest()}  {name}\n")
    (folder / "SHA256SUMS").write_text("".join(lines), newline="\n")
    return folder


@pytest.mark.parametrize("damage", ["changed", "missing", "unlisted"])
def test_a_damaged_backup_is_refused_before_anything_changes(
    bash, no_docker, tmp_path, damage
) -> None:
    """Verify restore.sh refuses a backup with a changed byte, a missing
    file, or a file SHA256SUMS doesn't list, before it calls Docker at all.
    """
    env, called = no_docker
    folder = backup_folder(tmp_path / "backups" / "2026-10-25_140531")
    if damage == "changed":
        (folder / "totals.json").write_bytes(b'{"reports": [1]}')
    elif damage == "missing":
        (folder / "uploads.tar").unlink()
    else:
        sums = (folder / "SHA256SUMS").read_text().splitlines(keepends=True)
        (folder / "SHA256SUMS").write_text("".join(sums[:2]), newline="\n")
    result = run_script(bash, "restore.sh", posix(folder), env=env)
    assert result.returncode == 1
    assert "nothing was restored" in result.stderr
    assert not called.exists()


def test_backup_stops_when_the_database_isn_t_running(
    bash, no_docker, tmp_path
) -> None:
    """Verify backup.sh writes nothing when it can't find the database."""
    env, _ = no_docker
    result = run_script(bash, "backup.sh", posix(tmp_path / "backups"), env=env)
    assert result.returncode == 1
    assert "The database isn't running" in result.stderr
    assert not (tmp_path / "backups").exists()


@pytest.mark.django_db
def test_check_uploads_finds_a_missing_or_changed_file(uploads_dir) -> None:
    """Verify check_uploads passes when every upload's file is stored
    unchanged, and names the upload whose file is missing or changed.
    """
    report = make_report()
    upload = receive_file(
        report, report.created_by, "s.csv", csv_file(LINE, total_row("1000.000"))
    )
    call_command("check_uploads")
    stored = uploads_dir / upload.file_sha256
    stored.write_bytes(b"changed")
    with pytest.raises(CommandError, match="1 upload without its file"):
        call_command("check_uploads")
    stored.unlink()
    with pytest.raises(CommandError, match="1 upload without its file"):
        call_command("check_uploads")


@pytest.mark.django_db
def test_check_uploads_says_which_upload_is_missing_its_file(
    uploads_dir, capsys
) -> None:
    """Verify each problem names the report, the upload and what's wrong."""
    report = make_report()
    upload = receive_file(
        report, report.created_by, "s.csv", csv_file(LINE, total_row("1000.000"))
    )
    (uploads_dir / upload.file_sha256).write_bytes(b"changed")
    with pytest.raises(CommandError):
        call_command("check_uploads")
    assert (
        f"{report}, upload 1: the file {upload.file_sha256} doesn't match its hash."
        in capsys.readouterr().err
    )


@pytest.mark.django_db
def test_record_restore_writes_database_restored() -> None:
    """Verify record_restore writes one database_restored event, as a
    command-line action, with the backup and whether each part matched.
    """
    call_command(
        "record_restore",
        "2026-10-25_140531",
        "--totals-match",
        "yes",
        "--files-match",
        "no",
    )
    (event,) = AuditEvent.objects.all()
    assert (event.action, event.user) == ("database_restored", None)
    assert event.details == {
        "backup": "2026-10-25_140531",
        "totals_match": True,
        "files_match": False,
        "command": "restore.sh",
    }
