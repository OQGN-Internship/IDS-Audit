"""manage.py check_uploads: whether every upload's file is in the file store,
with the SHA-256 it was received with (ENG-01 7.2). restore.sh runs it once
the uploads volume is restored, since totals alone can't show a missing file.
"""

import hashlib

from django.core.management.base import BaseCommand, CommandError

from ids_audit.imports.models import Upload
from ids_audit.imports.storage import uploads_folder


def sha256_of(path) -> str:
    """Return the SHA-256 of the file's bytes, read in pieces."""
    digest = hashlib.sha256()
    with open(path, "rb") as file:
        for piece in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(piece)
    return digest.hexdigest()


class Command(BaseCommand):
    help = "Check that every upload's file is stored, unchanged, under its hash."

    def handle(self, *args, **options) -> None:
        folder = uploads_folder()
        uploads = Upload.objects.select_related("report").order_by("report", "number")
        # Several uploads can share a file, which is checked once.
        found: dict[str, str] = {}
        problems = []
        for upload in uploads:
            name = upload.file_sha256
            if name not in found:
                path = folder / name
                if not path.is_file():
                    found[name] = "is missing"
                elif sha256_of(path) != name:
                    found[name] = "doesn't match its hash"
                else:
                    found[name] = ""
            if found[name]:
                problems.append(
                    f"{upload.report}, upload {upload.number}: the file {name} "
                    f"{found[name]}."
                )
        for problem in problems:
            self.stderr.write(problem)
        if problems:
            raise CommandError(
                f"{len(problems)} upload{'s' if len(problems) != 1 else ''} "
                "without its file, unchanged."
            )
        self.stdout.write(
            f"Every upload's file is in place: {len(found)} file"
            f"{'s' if len(found) != 1 else ''} checked."
        )
