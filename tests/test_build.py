import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from preview_publish.build import (
    STATIC_QML_PLUGIN_CLASSES,
    _contains_display_version,
    _missing_static_qml_plugins,
    configure_command,
)
from preview_publish.config import load_manifest
from preview_publish.layout import Layout


class BuildCommandTest(unittest.TestCase):
    def test_detects_utf16_qstring_literal_version(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            binary = Path(directory) / "app"
            binary.write_bytes(b"prefix" + "e5a893c991a3".encode("utf-16le") + b"suffix")

            self.assertTrue(_contains_display_version(binary, "e5a893c991a3"))
            self.assertFalse(_contains_display_version(binary, "6574cb40869b"))

    def test_detects_missing_static_qml_plugins(self) -> None:
        all_symbols = "\n".join(
            f"qt_plugin_instance_{plugin}" for plugin in STATIC_QML_PLUGIN_CLASSES
        )
        self.assertEqual(_missing_static_qml_plugins(all_symbols), ())
        self.assertEqual(
            _missing_static_qml_plugins("qt_plugin_instance_QtQuick2Plugin"),
            (
                "QtQuickControls2Plugin",
                "QtQuickLayoutsPlugin",
                "QmlSettingsPlugin",
            ),
        )

    @patch("preview_publish.build.require_tool", side_effect=lambda name: name)
    def test_configure_matches_pr_depends_build(self, _require_tool) -> None:
        manifest = load_manifest()
        with tempfile.TemporaryDirectory() as directory:
            layout = Layout.create(Path(directory), manifest)
            command = configure_command(layout, manifest, "aarch64-apple-darwin24.6.0")

        self.assertIn("-DBUILD_APP_TESTS=OFF", command)
        self.assertIn("-DBUILD_GUI=ON", command)
        self.assertIn("-DENABLE_WALLET=ON", command)
        self.assertIn("-DENABLE_IPC=OFF", command)
        self.assertIn("-DGUI_QML_BUILD_VERSION=e5a893c991a3", command)
        self.assertTrue(
            any(value.endswith("aarch64-apple-darwin24.6.0/toolchain.cmake") for value in command)
        )


if __name__ == "__main__":
    unittest.main()
