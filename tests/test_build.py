import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from preview_publish.build import (
    STATIC_QML_PLUGIN_CLASSES,
    _contains_display_version,
    _linux_needed_libraries,
    _missing_static_qml_plugins,
    configure_command,
    export_linux_binary,
    validate_linux_binary,
)
from preview_publish.config import load_manifest
from preview_publish.errors import PublisherError
from preview_publish.layout import Layout


class BuildCommandTest(unittest.TestCase):
    def test_detects_utf16_qstring_literal_version(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            binary = Path(directory) / "app"
            binary.write_bytes(b"prefix" + "25e056671840".encode("utf-16le") + b"suffix")

            self.assertTrue(_contains_display_version(binary, "25e056671840"))
            self.assertFalse(_contains_display_version(binary, "dc282ff31d1c"))

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

    def test_extracts_linux_dynamic_dependencies(self) -> None:
        dynamic_section = """
 0x0000000000000001 (NEEDED)             Shared library: [libm.so.6]
 0x0000000000000001 (NEEDED)             Shared library: [libc.so.6]
"""

        self.assertEqual(
            _linux_needed_libraries(dynamic_section),
            ("libm.so.6", "libc.so.6"),
        )

    @patch("preview_publish.build.require_tool", side_effect=lambda name: name)
    @patch("preview_publish.build.output")
    def test_linux_validation_rejects_dynamic_qt(
        self, output_mock, _require_tool
    ) -> None:
        manifest = load_manifest()
        output_mock.side_effect = [
            "Class: ELF64\nMachine: Advanced Micro Devices X86-64\n",
            "(NEEDED) Shared library: [libQt6Core.so.6]\n",
        ]
        with tempfile.TemporaryDirectory() as directory:
            binary = Path(directory) / "bitcoin-core-app"
            binary.write_bytes(b"ELF preview")

            with self.assertRaisesRegex(PublisherError, "libQt6Core"):
                validate_linux_binary(binary, manifest)

    @patch("preview_publish.build.platform.system", return_value="Linux")
    @patch("preview_publish.build.validate_linux_binary")
    def test_exports_raw_linux_executable(self, _validate, _system) -> None:
        manifest = load_manifest()
        with tempfile.TemporaryDirectory() as directory:
            layout = Layout.create(Path(directory), manifest)
            binary = layout.executable(manifest)
            binary.parent.mkdir(parents=True)
            binary.write_bytes(b"ELF preview")

            exported = export_linux_binary(layout, manifest)

            self.assertEqual(exported, layout.linux_binary(manifest))
            self.assertEqual(exported.read_bytes(), b"ELF preview")
            self.assertTrue(exported.stat().st_mode & 0o111)

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
        self.assertIn("-DGUI_QML_BUILD_VERSION=25e056671840", command)
        self.assertTrue(
            any(value.endswith("aarch64-apple-darwin24.6.0/toolchain.cmake") for value in command)
        )


if __name__ == "__main__":
    unittest.main()
