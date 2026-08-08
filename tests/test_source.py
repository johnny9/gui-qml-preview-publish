import hashlib
import subprocess
import tempfile
import unittest
from pathlib import Path

from preview_publish.errors import PublisherError
from preview_publish.config import load_manifest
from preview_publish.layout import Layout
from preview_publish.source import (
    WORKSPACE_MARKER,
    _apply_patch,
    _clean_source,
    _verify_patched_tree,
)


class ApplyPatchTest(unittest.TestCase):
    def test_preview_patch_disables_qml_disk_cache_before_application_start(self) -> None:
        manifest = load_manifest()
        patch_path = manifest.repository_path(manifest.patches[0].path)
        patch_text = patch_path.read_text(encoding="utf-8")

        disable = '+    qputenv("QML_DISABLE_DISK_CACHE", "1");'
        application_start = " #ifdef WIN32"
        self.assertIn("+#ifdef GUI_QML_BUILD_VERSION", patch_text)
        self.assertIn(disable, patch_text)
        self.assertLess(patch_text.index(disable), patch_text.index(application_start))

    def test_clean_requires_tool_owned_workspace_marker(self) -> None:
        manifest = load_manifest()
        with tempfile.TemporaryDirectory() as directory:
            layout = Layout.create(Path(directory), manifest)
            layout.source.mkdir(parents=True)
            sentinel = layout.source / "sentinel"
            sentinel.write_text("keep\n", encoding="utf-8")

            with self.assertRaises(PublisherError):
                _clean_source(layout)
            self.assertTrue(sentinel.exists())

            (layout.work / ".gui-qml-preview-workspace").write_text(
                WORKSPACE_MARKER, encoding="utf-8"
            )
            _clean_source(layout)
            self.assertFalse(layout.source.exists())

    def test_patched_tree_rejects_untracked_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory) / "repo"
            repo.mkdir()
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
            subprocess.run(
                ["git", "config", "user.email", "test@example.invalid"],
                cwd=repo,
                check=True,
            )
            tracked = repo / "tracked.txt"
            tracked.write_text("tracked\n", encoding="utf-8")
            subprocess.run(["git", "add", "tracked.txt"], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-q", "-m", "base"], cwd=repo, check=True)
            expected = hashlib.sha256(b"").hexdigest()

            _verify_patched_tree(repo, expected)
            (repo / "unexpected.txt").write_text("unexpected\n", encoding="utf-8")

            with self.assertRaises(PublisherError):
                _verify_patched_tree(repo, expected)

    def test_apply_patch_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory) / "repo"
            repo.mkdir()
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
            subprocess.run(
                ["git", "config", "user.email", "test@example.invalid"],
                cwd=repo,
                check=True,
            )
            source = repo / "value.txt"
            source.write_text("main\n", encoding="utf-8")
            subprocess.run(["git", "add", "value.txt"], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-q", "-m", "base"], cwd=repo, check=True)
            patch = Path(directory) / "change.patch"
            patch.write_text(
                """diff --git a/value.txt b/value.txt
index ba2906d..7997caf 100644
--- a/value.txt
+++ b/value.txt
@@ -1 +1 @@
-main
+signet
""",
                encoding="utf-8",
            )

            _apply_patch(repo, patch)
            _apply_patch(repo, patch)

            self.assertEqual(source.read_text(encoding="utf-8"), "signet\n")


if __name__ == "__main__":
    unittest.main()
