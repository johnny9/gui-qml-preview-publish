import unittest

from preview_publish.config import load_manifest


class ManifestTest(unittest.TestCase):
    def test_release_manifest_pins_reviewed_source(self) -> None:
        manifest = load_manifest()

        self.assertEqual(
            manifest.source.commit,
            "d63e9642c2cfbb5ee1abad80688fbac89597b61a",
        )
        self.assertEqual(
            manifest.source.bitcoin_commit,
            "6574cb40869b96b9ffc79c19dc8f4e467d60f321",
        )
        self.assertEqual(manifest.build.display_version, manifest.source.commit[:12])
        self.assertEqual(manifest.application.bundle_identifier, "org.bitcoincore.gui-qml.preview")
        self.assertEqual([patch.target for patch in manifest.patches], ["source", "bitcoin"])


if __name__ == "__main__":
    unittest.main()
