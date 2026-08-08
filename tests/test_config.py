import unittest

from preview_publish.config import load_manifest


class ManifestTest(unittest.TestCase):
    def test_release_manifest_pins_reviewed_source(self) -> None:
        manifest = load_manifest()

        self.assertEqual(
            manifest.source.commit,
            "babcfe9a292ef15cce086f326be57b9a2c91cc27",
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
            "19e3ba90f0d1d41978e99ff1da5fa0a737f3a715bd6d94be03cea423934bf8dc",
        )
        self.assertEqual(
            manifest.source.patched_source_diff_sha256,
            "696ba9ab6bf2d3158c26fe75a791a84d2aba59ecf07fca2dc0bfeb116f4c6128",
        )
        self.assertEqual(
            manifest.source.patched_bitcoin_diff_sha256,
            "a0b33e1a97a283f76932e170a4fb0d424b905aee26b72ffe06ba3baede96a217",
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
