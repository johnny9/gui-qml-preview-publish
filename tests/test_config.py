import unittest

from preview_publish.config import load_manifest


class ManifestTest(unittest.TestCase):
    def test_release_manifest_pins_reviewed_source(self) -> None:
        manifest = load_manifest()

        self.assertEqual(
            manifest.source.commit,
            "25e056671840ce1ae1d6db6307d20d2b1f68e445",
        )
        self.assertEqual(
            manifest.source.bitcoin_commit,
            "dc282ff31d1cc97507530a541d9cec8a8f6a6ef4",
        )
        self.assertEqual(
            manifest.source.repository,
            "https://github.com/johnny9/gui-qml.git",
        )
        self.assertEqual(
            manifest.source.reference,
            "https://github.com/johnny9/gui-qml/tree/v31",
        )
        self.assertEqual(manifest.source.fetch_ref, "refs/heads/v31")
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
