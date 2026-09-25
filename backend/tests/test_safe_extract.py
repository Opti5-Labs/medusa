"""
Tests for ingest/safe_extract.py — zip-slip, symlinks, size caps, etc.
All tests are fully offline; no network calls.
"""

import struct
import tempfile
import zipfile
from pathlib import Path

import pytest

from app.errors import MedusaError
from app.ingest.safe_extract import extract_zip

# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_zip(members: dict[str, bytes], *, encrypted: bool = False) -> Path:
    """Create a zip in a temp dir. members = {name: content}."""
    tmp = Path(tempfile.mkdtemp())
    zpath = tmp / "test.zip"
    with zipfile.ZipFile(zpath, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return zpath


def _make_zip_with_symlink(dest_path: Path) -> Path:
    """Craft a zip with a symlink entry (unix external attr)."""

    zpath = dest_path / "sym.zip"
    with zipfile.ZipFile(zpath, "w") as zf:
        info = zipfile.ZipInfo("link.txt")
        # Unix mode for symlink: 0xA1FF
        info.external_attr = 0xA1FF << 16
        zf.writestr(info, "/etc/passwd")
    return zpath


# ── Zip-slip tests ────────────────────────────────────────────────────────────


def test_zip_slip_dotdot():
    """Entry with '../evil' must be rejected."""
    zpath = _make_zip({"../evil.txt": b"pwned"})
    with tempfile.TemporaryDirectory() as dest_s:
        dest = Path(dest_s) / "extract"
        dest.mkdir()
        with pytest.raises(MedusaError) as exc_info:
            extract_zip(zpath, dest)
        assert exc_info.value.status in (400, 413)


def test_zip_slip_absolute_path():
    """Entry with absolute path must be rejected."""
    zpath = _make_zip({"/etc/passwd": b"pwned"})
    with tempfile.TemporaryDirectory() as dest_s:
        dest = Path(dest_s) / "extract"
        dest.mkdir()
        with pytest.raises(MedusaError) as exc_info:
            extract_zip(zpath, dest)
        assert exc_info.value.status in (400, 413)


def test_zip_slip_backslash():
    """Entry with backslash path traversal must be rejected."""
    zpath = _make_zip({"..\\evil.txt": b"pwned"})
    with tempfile.TemporaryDirectory() as dest_s:
        dest = Path(dest_s) / "extract"
        dest.mkdir()
        with pytest.raises(MedusaError) as exc_info:
            extract_zip(zpath, dest)
        assert exc_info.value.status in (400, 413)


def test_zip_symlink_rejected():
    with tempfile.TemporaryDirectory() as tmp_s:
        zpath = _make_zip_with_symlink(Path(tmp_s))
        dest = Path(tmp_s) / "extract"
        dest.mkdir()
        with pytest.raises(MedusaError) as exc_info:
            extract_zip(zpath, dest)
        assert exc_info.value.status == 400
        assert "symlink" in exc_info.value.message.lower()


def test_zip_encrypted_rejected():
    """Encrypted zip entries must be rejected."""
    # Build a minimal zip with an encrypted entry by setting flag bit 0
    zpath = _make_zip({"file.txt": b"secret"})
    # Patch the flag bits to set encryption bit
    data = bytearray(zpath.read_bytes())
    # Find local file header signature 0x04034b50
    sig = b"PK\x03\x04"
    idx = data.find(sig)
    assert idx >= 0
    # Flag bytes are at offset +6 from local header start
    flag_offset = idx + 6
    flags = struct.unpack_from("<H", data, flag_offset)[0]
    flags |= 0x1  # set encryption flag
    struct.pack_into("<H", data, flag_offset, flags)
    # Also patch central directory entry flag
    cd_sig = b"PK\x01\x02"
    cd_idx = data.find(cd_sig)
    if cd_idx >= 0:
        cd_flag_offset = cd_idx + 8
        cd_flags = struct.unpack_from("<H", data, cd_flag_offset)[0]
        cd_flags |= 0x1
        struct.pack_into("<H", data, cd_flag_offset, cd_flags)
    zpath.write_bytes(bytes(data))

    with tempfile.TemporaryDirectory() as dest_s:
        dest = Path(dest_s) / "extract"
        dest.mkdir()
        with pytest.raises(MedusaError) as exc_info:
            extract_zip(zpath, dest)
        assert exc_info.value.status == 400


def test_zip_non_zip_file():
    """A renamed .txt file must be rejected as not a valid zip."""
    tmp = Path(tempfile.mkdtemp())
    not_a_zip = tmp / "fake.zip"
    not_a_zip.write_bytes(b"this is not a zip file at all")
    dest = tmp / "extract"
    dest.mkdir()
    with pytest.raises(MedusaError) as exc_info:
        extract_zip(not_a_zip, dest)
    assert exc_info.value.status == 415


def test_zip_too_many_files(monkeypatch):
    """Zip with more entries than MAX_ZIP_FILES must be rejected (preflight)."""
    monkeypatch.setattr("app.ingest.safe_extract.MAX_ZIP_FILES", 2)
    zpath = _make_zip({"a.txt": b"a", "b.txt": b"b", "c.txt": b"c"})
    with tempfile.TemporaryDirectory() as dest_s:
        dest = Path(dest_s) / "extract"
        dest.mkdir()
        with pytest.raises(MedusaError) as exc_info:
            extract_zip(zpath, dest)
        assert exc_info.value.status == 413


def test_zip_uncompressed_cap(monkeypatch):
    """Zip that expands beyond MAX_UNCOMPRESSED_BYTES must be rejected."""
    monkeypatch.setattr("app.ingest.safe_extract.MAX_UNCOMPRESSED_BYTES", 100)
    zpath = _make_zip({"big.txt": b"x" * 200})
    with tempfile.TemporaryDirectory() as dest_s:
        dest = Path(dest_s) / "extract"
        dest.mkdir()
        with pytest.raises(MedusaError) as exc_info:
            extract_zip(zpath, dest)
        assert exc_info.value.status == 413


def test_zip_single_top_folder_root():
    """Zip with single top-level dir: root should be that dir."""
    zpath = _make_zip(
        {"myrepo/README.md": b"# hi", "myrepo/src/main.py": b"print('hi')"}
    )
    with tempfile.TemporaryDirectory() as dest_s:
        dest = Path(dest_s) / "extract"
        dest.mkdir()
        root = extract_zip(zpath, dest)
        assert root.name == "myrepo"


def test_zip_happy_path():
    """A clean zip should extract correctly."""
    zpath = _make_zip({"README.md": b"# Test", "src/main.py": b"print('hello')"})
    with tempfile.TemporaryDirectory() as dest_s:
        dest = Path(dest_s) / "extract"
        dest.mkdir()
        root = extract_zip(zpath, dest)
        assert (root / "README.md").read_bytes() == b"# Test"
        assert (root / "src" / "main.py").read_bytes() == b"print('hello')"


def test_zip_empty():
    """Empty zip should extract without error (empty root returned)."""
    zpath = _make_zip({})
    with tempfile.TemporaryDirectory() as dest_s:
        dest = Path(dest_s) / "extract"
        dest.mkdir()
        root = extract_zip(zpath, dest)
        # Root is the dest itself (no single top-level dir)
        assert root.is_dir()


# ── Temp dir cleanup via ingest_zip ──────────────────────────────────────────


async def test_ingest_zip_no_temp_leak_on_slip():
    """After a zip-slip rejection, no medusa_zip_* dir remains."""
    import io
    import zipfile

    from fastapi import UploadFile

    from app.ingest.zip_upload import ingest_zip

    # Build a zip with a path-traversal entry
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("../evil.txt", "pwned")
    buf.seek(0)

    before = set(Path(tempfile.gettempdir()).glob("medusa_zip_*"))
    upload = UploadFile(filename="test.zip", file=buf)
    with pytest.raises(MedusaError):
        await ingest_zip(upload)

    after = set(Path(tempfile.gettempdir()).glob("medusa_zip_*"))
    assert after == before, f"Leaked temp dirs: {after - before}"


async def test_ingest_zip_no_temp_leak_on_oversize(monkeypatch):
    """After a size rejection, no medusa_zip_* dir remains."""
    import io

    from fastapi import UploadFile

    from app.ingest.zip_upload import ingest_zip

    monkeypatch.setattr("app.ingest.zip_upload.MAX_ZIP_SIZE_BYTES", 10)

    buf = io.BytesIO(b"X" * 100)
    before = set(Path(tempfile.gettempdir()).glob("medusa_zip_*"))
    upload = UploadFile(filename="test.zip", file=buf)
    with pytest.raises(MedusaError) as exc_info:
        await ingest_zip(upload)
    assert exc_info.value.status == 413

    after = set(Path(tempfile.gettempdir()).glob("medusa_zip_*"))
    assert after == before, f"Leaked temp dirs: {after - before}"
