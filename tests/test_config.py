import unittest

from preview_publish.config import load_manifest


class ManifestTest(unittest.TestCase):
    def test_release_manifest_pins_reviewed_source(self) -> None:
        manifest = load_manifest()

        self.assertEqual(
            manifest.source.commit,
            "d567e3eb2497df3be5d63f4dc1f554b3bf51f59f",
        )
        self.assertEqual(
            manifest.source.bitcoin_commit,
            "dc282ff31d1cc97507530a541d9cec8a8f6a6ef4",
        )
        self.assertEqual(
            manifest.source.repository,
            "https://github.com/bitcoin-core/gui-qml.git",
        )
        self.assertEqual(
            manifest.source.reference,
            "https://github.com/bitcoin-core/gui-qml/tree/qt6",
        )
        self.assertEqual(manifest.source.fetch_ref, "refs/heads/qt6")
        self.assertEqual(
            manifest.source.depends_patch,
            "patches/depends-Add-Qt-Qml-and-Qt-Quick-modules.patch",
        )
        self.assertEqual(
            manifest.source.depends_patch_sha256,
            "4a82165a9cebfa0ec500c3abb6b2294b4bfe6edf34ab76003f06a7acba4646b9",
        )
        self.assertEqual(
            manifest.source.patched_source_diff_sha256,
            "acefdb02acfd7a41a3dc94159bf0ff8eea7578790fb99e3e8d8fba0d988b7e31",
        )
        self.assertEqual(
            manifest.source.patched_bitcoin_diff_sha256,
            "3e2c30d7bacae07f7420980b1c757a1ce7347d817819e35872129bf0c56bf39a",
        )
        self.assertEqual(manifest.build.display_version, manifest.source.commit[:12])
        self.assertEqual(manifest.linux.host, "x86_64-pc-linux-gnu")
        self.assertEqual(manifest.linux.architecture, "x86_64")
        self.assertEqual(
            manifest.linux.artifact_name,
            "bitcoin-core-app-signet-x86_64-linux-gnu",
        )
        self.assertEqual(manifest.application.bundle_identifier, "org.bitcoincore.gui-qml.preview")
        self.assertEqual([patch.target for patch in manifest.patches], ["source", "bitcoin"])


if __name__ == "__main__":
    unittest.main()
