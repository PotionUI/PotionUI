import io
import os
import stat
import zipfile

import pytest

from src.features.plugins import archive
from src.features.plugins.archive import ArchiveError, unpack_plugin_archive

MANIFEST = b"id: demo\nversion: 1.0.0\n"


def _zip(*entries) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as bundle:
        for entry in entries:
            if isinstance(entry, zipfile.ZipInfo):
                bundle.writestr(entry, b"x")
            else:
                name, content = entry
                bundle.writestr(name, content)
    return buffer.getvalue()


def _plugin(*extra) -> bytes:
    return _zip(("demo/", b""), ("demo/manifest.yml", MANIFEST), ("demo/frontend/dist/Page.js", b"export {}"), *extra)


def test_a_plugin_archive_unpacks_into_its_folder(tmp_path):
    folder = unpack_plugin_archive(_plugin(), "demo", tmp_path / "out")

    assert folder == (tmp_path / "out" / "demo").resolve()
    assert (folder / "manifest.yml").read_bytes() == MANIFEST
    assert (folder / "frontend" / "dist" / "Page.js").read_text() == "export {}"


@pytest.mark.parametrize(
    "name",
    [
        "../demo/escape.py",
        "demo/../../escape.py",
        "/etc/escape.py",
        "demo/a/../../../escape.py",
        "demo\\..\\escape.py",
        "C:/escape.py",
        "demo/file.py:stream",
        "demo//double.py",
        "demo/./dot.py",
        "other/manifest.yml",
        "demo/CON",
        "demo/nul.txt",
        "demo/trailing.",
        "demo/trailing ",
        "demo/tab\tname",
    ],
)
def test_an_entry_outside_the_plugin_folder_or_with_an_unsafe_name_refuses_the_whole_archive(tmp_path, name):
    with pytest.raises(ArchiveError):
        unpack_plugin_archive(_plugin((name, b"x")), "demo", tmp_path / "out")

    assert not (tmp_path / "out").exists()
    assert not (tmp_path / "escape.py").exists()


def test_the_metadata_folder_the_macos_finder_adds_is_skipped(tmp_path):
    bundle = _plugin(
        ("__MACOSX/", b""),
        ("__MACOSX/._demo", b"apple-double"),
        ("__MACOSX/demo/._manifest.yml", b"apple-double"),
        ("__MACOSX/demo/frontend/dist/._Page.js", b"apple-double"),
    )

    folder = unpack_plugin_archive(bundle, "demo", tmp_path / "out")

    assert sorted(path.name for path in (tmp_path / "out").iterdir()) == ["demo"]
    assert (folder / "manifest.yml").read_bytes() == MANIFEST
    assert not any(path.name.startswith("._") for path in folder.rglob("*"))


def test_a_skipped_macos_metadata_entry_that_climbs_out_or_is_a_link_writes_nothing(tmp_path):
    link = zipfile.ZipInfo("__MACOSX/demo/link")
    link.external_attr = (stat.S_IFLNK | 0o777) << 16
    bundle = _plugin(("__MACOSX/../escape.py", b"x"), ("__MACOSX/../../escape.py", b"x"), link)

    folder = unpack_plugin_archive(bundle, "demo", tmp_path / "out" / "inner")

    assert sorted(path.name for path in (tmp_path / "out" / "inner").iterdir()) == ["demo"]
    assert not (tmp_path / "out" / "escape.py").exists()
    assert not (tmp_path / "escape.py").exists()
    assert not (folder / "link").exists()
    assert not list(tmp_path.rglob("__MACOSX"))


def test_a_macos_metadata_folder_inside_the_plugin_folder_is_checked_and_unpacked_like_any_other(tmp_path):
    folder = unpack_plugin_archive(_plugin(("demo/__MACOSX/notes.txt", b"kept")), "demo", tmp_path / "out")

    assert (folder / "__MACOSX" / "notes.txt").read_bytes() == b"kept"
    with pytest.raises(ArchiveError):
        unpack_plugin_archive(_plugin(("demo/__MACOSX/../../escape.py", b"x")), "demo", tmp_path / "again")


def test_an_absolute_path_is_refused_as_such(tmp_path):
    with pytest.raises(ArchiveError, match="is absolute"):
        unpack_plugin_archive(_plugin(("/demo/escape.py", b"x")), "demo", tmp_path / "out")


@pytest.mark.skipif(os.sep == "\\", reason="zipfile reads a backslash in a name as a separator on Windows")
def test_a_backslash_inside_the_plugin_folder_refuses_the_archive(tmp_path):
    entry = zipfile.ZipInfo("demo/placeholder")
    entry.filename = "demo/sub\\file.py"

    with pytest.raises(ArchiveError, match="not a plain relative path"):
        unpack_plugin_archive(_plugin(entry), "demo", tmp_path / "out")
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize("kind", [stat.S_IFLNK, stat.S_IFCHR, stat.S_IFIFO])
def test_a_link_or_special_file_refuses_the_whole_archive(tmp_path, kind):
    link = zipfile.ZipInfo("demo/link")
    link.external_attr = (kind | 0o777) << 16

    with pytest.raises(ArchiveError, match="link or a special file"):
        unpack_plugin_archive(_plugin(link), "demo", tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_two_entries_that_differ_only_by_case_refuse_the_archive(tmp_path):
    with pytest.raises(ArchiveError, match="more than once"):
        unpack_plugin_archive(_plugin(("demo/Readme.md", b"a"), ("demo/README.md", b"b")), "demo", tmp_path / "out")


def test_a_file_used_as_a_folder_refuses_the_archive(tmp_path):
    with pytest.raises(ArchiveError, match="as a folder"):
        unpack_plugin_archive(_plugin(("demo/data", b"a"), ("demo/data/inner.txt", b"b")), "demo", tmp_path / "out")


def test_an_archive_without_the_manifest_of_the_plugin_is_refused(tmp_path):
    with pytest.raises(ArchiveError, match="no demo/manifest.yml"):
        unpack_plugin_archive(_zip(("demo/frontend/dist/Page.js", b"x")), "demo", tmp_path / "out")


def test_a_file_that_is_not_a_zip_is_refused(tmp_path):
    with pytest.raises(ArchiveError, match="not a zip"):
        unpack_plugin_archive(b"PK\x03\x04 nothing", "demo", tmp_path / "out")


def test_an_encrypted_entry_refuses_the_archive(tmp_path):
    data = bytearray(_plugin(("demo/secret.py", b"x")))
    with zipfile.ZipFile(io.BytesIO(bytes(data))) as bundle:
        local_header = bundle.getinfo("demo/secret.py").header_offset
    central_header = data.rindex(b"demo/secret.py") - 46
    data[local_header + 6] |= 0x1
    data[central_header + 8] |= 0x1

    with pytest.raises(ArchiveError, match="encrypted"):
        unpack_plugin_archive(bytes(data), "demo", tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_too_many_entries_refuse_the_archive(tmp_path, monkeypatch):
    monkeypatch.setattr(archive, "MAX_ARCHIVE_ENTRIES", 3)

    with pytest.raises(ArchiveError, match="more than 3 entries"):
        unpack_plugin_archive(_plugin(("demo/extra.txt", b"x")), "demo", tmp_path / "out")


def test_an_archive_that_declares_more_than_the_unpacked_limit_is_refused_before_writing(tmp_path, monkeypatch):
    monkeypatch.setattr(archive, "MAX_UNPACKED_BYTES", 100)

    with pytest.raises(ArchiveError, match="more than 100 bytes"):
        unpack_plugin_archive(_plugin(("demo/big.bin", b"\0" * 200)), "demo", tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_a_corrupted_entry_refuses_the_archive(tmp_path):
    data = bytearray(_zip(("demo/manifest.yml", MANIFEST), ("demo/payload.txt", b"payload" * 100)))
    start = data.index(b"demo/payload.txt") + len("demo/payload.txt")
    data[start + 5] ^= 0xFF

    with pytest.raises(ArchiveError, match="cannot be read"):
        unpack_plugin_archive(bytes(data), "demo", tmp_path / "out")
