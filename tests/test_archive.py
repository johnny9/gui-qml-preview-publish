import io
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from preview_publish.archive import extract_app_archive
from preview_publish.config import load_manifest
from preview_publish.errors import PublisherError
from preview_publish.layout import Layout


def _write_archive(path: Path, member_name: str) -> None:
    content = b"content"
    info = tarfile.TarInfo(member_name)
    info.size = len(content)
    info.mode = 0o755
    with tarfile.open(path, "w:gz") as archive:
        archive.addfile(info, io.BytesIO(content))


class ArchiveTest(unittest.TestCase):
    @patch("preview_publish.archive.validate_unsigned_bundle_inventory")
    @patch("preview_publish.archive.validate_bundle")
    def test_safe_extract_preserves_executable_mode(
        self, _validate, _validate_inventory
    ) -> None:
        manifest = load_manifest()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            layout = Layout.create(root / "work", manifest)
            archive = root / "unsigned.tar.gz"
            relative = (
                f"{manifest.application.bundle_name}.app/Contents/MacOS/"
                f"{manifest.application.executable}"
            )
            _write_archive(archive, relative)

            app = extract_app_archive(archive, layout, manifest)

            executable = app / "Contents" / "MacOS" / manifest.application.executable
            self.assertEqual(executable.read_bytes(), b"content")
            self.assertTrue(executable.stat().st_mode & 0o111)

    @patch("preview_publish.archive.validate_unsigned_bundle_inventory")
    @patch("preview_publish.archive.validate_bundle")
    def test_extract_rejects_parent_traversal(
        self, _validate, _validate_inventory
    ) -> None:
        manifest = load_manifest()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            layout = Layout.create(root / "work", manifest)
            archive = root / "unsafe.tar.gz"
            _write_archive(
                archive,
                f"{manifest.application.bundle_name}.app/../../escaped",
            )

            with self.assertRaises(PublisherError):
                extract_app_archive(archive, layout, manifest)

            self.assertFalse((root / "escaped").exists())
            self.assertFalse(layout.deploy.exists())


if __name__ == "__main__":
    unittest.main()
