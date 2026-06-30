import os
import plistlib
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from preview_publish.config import load_manifest
from preview_publish.layout import Layout
from preview_publish.package import assemble_bundle, write_checksums


class BundleTest(unittest.TestCase):
    @patch("preview_publish.package.validate_binary")
    def test_bundle_uses_qml_identity_and_arm64_compatible_plist(self, _validate) -> None:
        manifest = load_manifest()
        with tempfile.TemporaryDirectory() as directory:
            layout = Layout.create(Path(directory), manifest)
            binary = layout.executable(manifest)
            binary.parent.mkdir(parents=True)
            binary.write_bytes(b"binary")
            icon = layout.source / "bitcoin" / "src" / "qt" / "res" / "icons" / "bitcoin.icns"
            icon.parent.mkdir(parents=True)
            icon.write_bytes(b"icon")

            app = assemble_bundle(layout, manifest)
            with (app / "Contents" / "Info.plist").open("rb") as handle:
                info = plistlib.load(handle)

            self.assertEqual(info["CFBundleExecutable"], "bitcoin-core-app")
            self.assertEqual(info["CFBundleName"], "Bitcoin-QML")
            self.assertEqual(info["CFBundleIdentifier"], "org.bitcoincore.gui-qml.preview")
            self.assertEqual(info["LSMinimumSystemVersion"], "14.0")
            self.assertNotIn("LSArchitecturePriority", info)
            self.assertTrue((app / "Contents" / "MacOS" / "bitcoin-core-app").stat().st_mode & 0o111)

    def test_vendored_macdeploy_uses_plist_executable_and_bundle_name(self) -> None:
        manifest = load_manifest()
        vendor = manifest.root / "vendor" / "bitcoin-core" / "macdeploy" / "macdeployqtplus"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app = root / "Bitcoin-QML.app"
            executable = app / "Contents" / "MacOS" / "bitcoin-core-app"
            resources = app / "Contents" / "Resources"
            executable.parent.mkdir(parents=True)
            resources.mkdir(parents=True)
            executable.write_bytes(b"not-a-mach-o")
            executable.chmod(0o755)
            with (app / "Contents" / "Info.plist").open("wb") as handle:
                plistlib.dump({"CFBundleExecutable": "bitcoin-core-app"}, handle)
            output = root / "deployed"
            environment = {**os.environ, "OBJDUMP": "/usr/bin/true"}

            subprocess.run(
                [
                    os.environ.get("PYTHON", "python3"),
                    str(vendor),
                    str(app),
                    "-no-plugins",
                    "-no-strip",
                    "-output-dir",
                    str(output),
                ],
                cwd=root,
                env=environment,
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )

            deployed = output / "Bitcoin-QML.app"
            self.assertTrue((deployed / "Contents" / "MacOS" / "bitcoin-core-app").is_file())
            self.assertTrue((deployed / "Contents" / "Resources" / "qt.conf").is_file())

    def test_checksums_cover_artifacts(self) -> None:
        manifest = load_manifest()
        with tempfile.TemporaryDirectory() as directory:
            layout = Layout.create(Path(directory), manifest)
            layout.artifacts.mkdir(parents=True)
            (layout.artifacts / "preview.dmg").write_bytes(b"preview")

            checksum = write_checksums(layout).read_text(encoding="utf-8")

            self.assertIn("preview.dmg", checksum)
            self.assertEqual(len(checksum.split()[0]), 64)


if __name__ == "__main__":
    unittest.main()
