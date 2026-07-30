import tempfile
import unittest
from pathlib import Path

from preview_publish.config import load_manifest
from preview_publish.errors import PublisherError
from preview_publish.refresh import _render_manifest, _write_refreshed_manifest


class RefreshManifestTest(unittest.TestCase):
    def test_renders_and_validates_an_exact_source_manifest(self) -> None:
        template = load_manifest()
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "release.toml"
            refreshed = _write_refreshed_manifest(
                template,
                output,
                source_commit="a" * 40,
                bitcoin_commit="b" * 40,
                depends_patch_sha256="c" * 64,
                source_diff_sha256="d" * 64,
                bitcoin_diff_sha256="e" * 64,
            )

            self.assertEqual(refreshed.source.commit, "a" * 40)
            self.assertEqual(refreshed.source.bitcoin_commit, "b" * 40)
            self.assertEqual(refreshed.source.depends_patch_sha256, "c" * 64)
            self.assertEqual(
                refreshed.source.patched_source_diff_sha256, "d" * 64
            )
            self.assertEqual(
                refreshed.source.patched_bitcoin_diff_sha256, "e" * 64
            )
            self.assertEqual(refreshed.build.display_version, "a" * 12)
            self.assertEqual(refreshed.linux, template.linux)
            self.assertEqual(refreshed.application, template.application)
            self.assertEqual(refreshed.patches, template.patches)

    def test_render_fails_when_a_required_field_is_missing(self) -> None:
        with self.assertRaises(PublisherError):
            _render_manifest(
                '[source]\ncommit = "old"\n',
                {("source", "bitcoin_commit"): "new"},
            )

    def test_refuses_to_overwrite_checked_in_manifest(self) -> None:
        template = load_manifest()
        with self.assertRaises(PublisherError):
            _write_refreshed_manifest(
                template,
                template.path,
                source_commit="a" * 40,
                bitcoin_commit="b" * 40,
                depends_patch_sha256="c" * 64,
                source_diff_sha256="d" * 64,
                bitcoin_diff_sha256="e" * 64,
            )


if __name__ == "__main__":
    unittest.main()
