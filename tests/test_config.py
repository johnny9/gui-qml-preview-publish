import unittest

from preview_publish.config import load_manifest


class ManifestTest(unittest.TestCase):
    def test_release_manifest_pins_reviewed_source(self) -> None:
        manifest = load_manifest()

        self.assertEqual(
            manifest.source.commit,
            "e5a893c991a3d7779b4d30c8765b76c623fa0b89",
        )
        self.assertEqual(
            manifest.source.bitcoin_commit,
            "6574cb40869b96b9ffc79c19dc8f4e467d60f321",
        )
        self.assertEqual(
            manifest.source.repository,
            "https://github.com/johnny9/BitcoinCoreAppDevelopment.git",
        )
        self.assertEqual(
            manifest.source.reference,
            "https://github.com/johnny9/BitcoinCoreAppDevelopment/tree/qt6",
        )
        self.assertEqual(manifest.source.fetch_ref, "refs/heads/qt6")
        self.assertEqual(manifest.build.display_version, manifest.source.commit[:12])
        self.assertEqual(manifest.application.bundle_identifier, "org.bitcoincore.gui-qml.preview")
        self.assertEqual([patch.target for patch in manifest.patches], ["source", "bitcoin"])


if __name__ == "__main__":
    unittest.main()
