"""The file store: every received file, unchanged, under its SHA-256 hash
(ENG-01 2 and 5.1), in the folder the UPLOADS_DIR setting names (ENG-01 7.5).
"""

import os
import tempfile
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured


def uploads_folder() -> Path:
    """Return the folder UPLOADS_DIR names, or refuse if it isn't set."""
    if not settings.UPLOADS_DIR:
        raise ImproperlyConfigured("UPLOADS_DIR is not set (ENG-01 7.5).")
    return Path(settings.UPLOADS_DIR)


def store_file(file_sha256: str, data: bytes) -> Path:
    """Save the bytes as UPLOADS_DIR/<file_sha256>, and return its path.

    They're written under a temporary name first, then renamed, so a file under
    a hash name is always complete. Storing the same bytes again writes the
    same file.
    """
    folder = uploads_folder()
    # A name no other request can be using, in the same folder, so the rename
    # below never moves the file to another disk.
    handle, temporary = tempfile.mkstemp(
        dir=folder, prefix=f"{file_sha256}.", suffix=".tmp"
    )
    try:
        with os.fdopen(handle, "wb") as file:
            file.write(data)
        # A rename is all or nothing, and replaces a file of the same name.
        os.replace(temporary, folder / file_sha256)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
    return folder / file_sha256


def read_stored_file(file_sha256: str) -> bytes:
    """Return the bytes stored under a file's hash."""
    return (uploads_folder() / file_sha256).read_bytes()
