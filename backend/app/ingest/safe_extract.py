"""
Hardened archive extractor shared by zip_upload.py and github.py.

Public API:
    extract_zip(src: Path, dest: Path) -> Path
        Safely extracts a zip file, returns the effective repo root.

    extract_tarball(src: Path, dest: Path) -> Path
        Safely extracts a .tar.gz tarball (GitHub format), returns repo root.

Security guarantees (both formats):
    - No absolute paths or drive letters
    - No '..' path components
    - No symlinks or hardlinks
    - Every resolved destination is inside dest
    - Declared sizes checked before extraction; per-member streaming byte counter
    - Member count and total uncompressed size caps
    - Encrypted zip entries are rejected
    - No device files (tar)
"""

import tarfile
import zipfile
from pathlib import Path

from app.config import MAX_UNCOMPRESSED_BYTES, MAX_ZIP_FILES
from app.errors import MedusaError

# ── Shared helpers ────────────────────────────────────────────────────────────

_JUNK_PREFIXES = ("__MACOSX/", ".DS_Store")
_CHUNK = 65_536  # 64 KiB read chunks


def _safe_rel_path(member_name: str) -> str | None:
    """
    Return a sanitised relative path string, or None if the entry is unsafe.
    Rejects: absolute paths, drive letters, '..' components, NUL bytes, backslash tricks.
    """
    name = member_name.replace("\\", "/")
    if "\x00" in name:
        return None
    # Reject absolute / drive-letter paths
    if name.startswith("/") or (len(name) >= 2 and name[1] == ":"):
        return None
    parts = name.split("/")
    if ".." in parts:
        return None
    # Remove empty leading parts (e.g. "./foo")
    parts = [p for p in parts if p and p != "."]
    if not parts:
        return None
    return "/".join(parts)


def _resolve_safe(dest: Path, rel: str) -> Path | None:
    """Resolve dest/rel and verify it stays inside dest."""
    try:
        target = (dest / rel).resolve()
        dest_resolved = dest.resolve()
        # is_relative_to handles both / and \ on all platforms (Python 3.9+)
        if target != dest_resolved and not target.is_relative_to(dest_resolved):
            return None
        return target
    except (OSError, ValueError):
        return None


def _single_top_folder(dest: Path) -> Path:
    """
    If the extraction produced a single top-level directory, return it as the
    effective repo root (GitHub tarballs always do this; zips sometimes do).
    """
    entries = [p for p in dest.iterdir() if not p.name.startswith(".")]
    if len(entries) == 1 and entries[0].is_dir():
        return entries[0]
    return dest


# ── Zip extraction ────────────────────────────────────────────────────────────


def extract_zip(src: Path, dest: Path) -> Path:
    """
    Safely extract *src* zip into *dest*. Returns effective repo root.
    Raises MedusaError on any security or size violation.
    """
    if not zipfile.is_zipfile(src):
        raise MedusaError(415, "Uploaded file is not a valid zip archive.")

    with zipfile.ZipFile(src, "r") as zf:
        members = zf.infolist()

        # Pre-flight: count
        real_members = [
            m
            for m in members
            if not any(m.filename.startswith(j) for j in _JUNK_PREFIXES)
        ]
        if len(real_members) > MAX_ZIP_FILES:
            raise MedusaError(
                413,
                f"Zip contains {len(real_members):,} files; maximum is {MAX_ZIP_FILES:,}.",
            )

        # Pre-flight: encrypted entries
        for m in real_members:
            if m.flag_bits & 0x1:
                raise MedusaError(
                    400, "Zip contains encrypted entries, which are not supported."
                )

        # Pre-flight: declared uncompressed total
        declared_total = sum(m.file_size for m in real_members)
        if declared_total > MAX_UNCOMPRESSED_BYTES:
            mb = declared_total // (1024 * 1024)
            cap_mb = MAX_UNCOMPRESSED_BYTES // (1024 * 1024)
            raise MedusaError(
                413,
                f"Zip declares {mb} MB uncompressed; maximum is {cap_mb} MB.",
            )

        # Per-entry extraction with path validation and streaming byte counter
        total_extracted = 0
        for m in members:
            # Skip junk
            if any(m.filename.startswith(j) for j in _JUNK_PREFIXES):
                continue
            if m.filename.endswith("/"):
                # Directory entry
                rel = _safe_rel_path(m.filename.rstrip("/"))
                if rel is None:
                    raise MedusaError(400, f"Zip contains unsafe path: {m.filename!r}")
                target = _resolve_safe(dest, rel)
                if target is None:
                    raise MedusaError(
                        400, f"Zip path escapes extract directory: {m.filename!r}"
                    )
                target.mkdir(parents=True, exist_ok=True)
                continue

            # Reject symlinks (unix external attr: upper 16 bits = mode; 0xA000 = symlink)
            unix_mode = (m.external_attr >> 16) & 0xFFFF
            if unix_mode and (unix_mode & 0xF000) == 0xA000:
                raise MedusaError(400, f"Zip contains a symlink: {m.filename!r}")

            rel = _safe_rel_path(m.filename)
            if rel is None:
                raise MedusaError(400, f"Zip contains unsafe path: {m.filename!r}")
            target = _resolve_safe(dest, rel)
            if target is None:
                raise MedusaError(
                    400, f"Zip path escapes extract directory: {m.filename!r}"
                )

            target.parent.mkdir(parents=True, exist_ok=True)

            # Stream with live byte counter
            with zf.open(m) as src_fh, open(target, "wb") as dst_fh:
                while True:
                    chunk = src_fh.read(_CHUNK)
                    if not chunk:
                        break
                    total_extracted += len(chunk)
                    if total_extracted > MAX_UNCOMPRESSED_BYTES:
                        raise MedusaError(
                            413,
                            f"Zip uncompressed content exceeds {MAX_UNCOMPRESSED_BYTES // (1024 * 1024)} MB limit.",
                        )
                    dst_fh.write(chunk)

    return _single_top_folder(dest)


# ── Tarball extraction ────────────────────────────────────────────────────────


def extract_tarball(src: Path, dest: Path) -> Path:
    """
    Safely extract a .tar.gz tarball into *dest*. Returns effective repo root.
    Raises MedusaError on any security or size violation.
    """
    try:
        tf = tarfile.open(src, "r:gz")  # noqa: SIM115
    except tarfile.TarError as exc:
        raise MedusaError(400, f"Could not open tarball: {exc}") from exc

    with tf:
        members = tf.getmembers()

        real_members = [
            m for m in members if not any(m.name.startswith(j) for j in _JUNK_PREFIXES)
        ]
        if len(real_members) > MAX_ZIP_FILES:
            raise MedusaError(
                413,
                f"Archive contains {len(real_members):,} entries; maximum is {MAX_ZIP_FILES:,}.",
            )

        # Pre-flight declared size
        declared_total = sum(m.size for m in real_members if m.isfile())
        if declared_total > MAX_UNCOMPRESSED_BYTES:
            mb = declared_total // (1024 * 1024)
            cap_mb = MAX_UNCOMPRESSED_BYTES // (1024 * 1024)
            raise MedusaError(
                413,
                f"Archive declares {mb} MB uncompressed; maximum is {cap_mb} MB.",
            )

        total_extracted = 0
        for m in members:
            if any(m.name.startswith(j) for j in _JUNK_PREFIXES):
                continue

            # Reject non-regular types
            if m.issym() or m.islnk():
                raise MedusaError(
                    400, f"Archive contains a symlink/hardlink: {m.name!r}"
                )
            if m.isdev() or m.isfifo():
                raise MedusaError(
                    400, f"Archive contains a device/FIFO entry: {m.name!r}"
                )

            rel = _safe_rel_path(m.name)
            if rel is None:
                raise MedusaError(400, f"Archive contains unsafe path: {m.name!r}")
            target = _resolve_safe(dest, rel)
            if target is None:
                raise MedusaError(
                    400, f"Archive path escapes extract directory: {m.name!r}"
                )

            if m.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue

            if not m.isfile():
                continue  # skip other types silently

            target.parent.mkdir(parents=True, exist_ok=True)

            fobj = tf.extractfile(m)
            if fobj is None:
                continue
            with fobj, open(target, "wb") as dst_fh:
                while True:
                    chunk = fobj.read(_CHUNK)
                    if not chunk:
                        break
                    total_extracted += len(chunk)
                    if total_extracted > MAX_UNCOMPRESSED_BYTES:
                        raise MedusaError(
                            413,
                            f"Archive uncompressed content exceeds {MAX_UNCOMPRESSED_BYTES // (1024 * 1024)} MB limit.",
                        )
                    dst_fh.write(chunk)

    return _single_top_folder(dest)
