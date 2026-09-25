"""
Zip upload ingest.

ingest_zip(upload: UploadFile) -> tuple[Path, Path]
    Streams the upload to a temp file, validates it, extracts it.
    Returns (tmp_dir, extract_root) where tmp_dir is the top-level temp
    directory that owns everything (archive + extracted tree).

The caller is responsible for cleaning up tmp_dir on failure.
On success, pass tmp_dir to RunStore.create so TTL expiry removes everything.
"""

import logging
import shutil
import tempfile
from pathlib import Path

from fastapi import UploadFile

from app.config import MAX_ZIP_SIZE_BYTES
from app.errors import MedusaError
from app.ingest.safe_extract import extract_zip

log = logging.getLogger(__name__)

_CHUNK = 65_536  # 64 KiB


async def ingest_zip(upload: UploadFile) -> tuple[Path, Path]:
    """
    Stream *upload* to a temp file (size-capped), then safe-extract.

    Returns (tmp_dir, extract_root).
    *tmp_dir* is the top-level temp directory; caller must delete it on any
    failure, and pass it to RunStore so TTL cleanup removes everything.
    Raises MedusaError on any validation failure (tmp_dir already deleted).
    """
    tmp_dir = Path(tempfile.mkdtemp(prefix="medusa_zip_"))
    zip_path = tmp_dir / "upload.zip"

    try:
        # Stream with hard byte cap — do NOT trust Content-Length
        received = 0
        with open(zip_path, "wb") as fh:  # noqa: ASYNC230
            while True:
                chunk = await upload.read(_CHUNK)
                if not chunk:
                    break
                received += len(chunk)
                if received > MAX_ZIP_SIZE_BYTES:
                    raise MedusaError(
                        413,
                        f"Upload exceeds the {MAX_ZIP_SIZE_BYTES // (1024 * 1024)} MB limit. "
                        "Please upload a smaller zip.",
                    )
                fh.write(chunk)

        if received == 0:
            raise MedusaError(400, "Uploaded file is empty.")

        extract_dir = tmp_dir / "extracted"
        extract_dir.mkdir()

        repo_root = extract_zip(zip_path, extract_dir)
        return tmp_dir, repo_root

    except MedusaError:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise
    except Exception as exc:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        log.exception("Unexpected error during zip ingest")
        raise MedusaError(
            400,
            "Unexpected error while scanning. Please try again or use a smaller repo.",
        ) from exc
