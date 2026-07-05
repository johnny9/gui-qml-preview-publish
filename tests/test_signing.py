import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from preview_publish.config import load_manifest
from preview_publish.errors import PublisherError
from preview_publish.layout import Layout
from preview_publish.signing import (
    AppleCredentials,
    SIGNING_MARKER_CONTENT,
    SIGNING_MARKER_NAME,
    SigningContext,
    assert_static_bundle,
    cleanup_temporary_keychains,
    notarize_dmg,
)


class SigningTest(unittest.TestCase):
    @staticmethod
    def _write_unsigned_bundle(app: Path, executable_name: str) -> Path:
        executable = app / "Contents" / "MacOS" / executable_name
        resources = app / "Contents" / "Resources"
        executable.parent.mkdir(parents=True)
        resources.mkdir(parents=True)
        executable.write_bytes(b"\xcf\xfa\xed\xfe" + b"main")
        (app / "Contents" / "Info.plist").write_bytes(b"plist")
        (app / "Contents" / "PkgInfo").write_bytes(b"APPL????")
        (resources / "bitcoin.icns").write_bytes(b"icon")
        (resources / "qt.conf").write_bytes(b"[Paths]\n")
        return executable

    def test_cleanup_only_removes_marked_signing_directories(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            unowned = parent / "gui-qml-signing-unowned"
            unowned.mkdir()
            (unowned / "keep").write_text("keep\n", encoding="utf-8")
            owned = parent / "gui-qml-signing-owned"
            owned.mkdir()
            (owned / SIGNING_MARKER_NAME).write_text(
                SIGNING_MARKER_CONTENT, encoding="utf-8"
            )
            (owned / "developer-id.p12").write_bytes(b"secret")
            (owned / "AuthKey_ABCDEFGHIJ.p8").write_bytes(b"notary-key")

            cleanup_temporary_keychains(parent)

            self.assertTrue(unowned.is_dir())
            self.assertFalse(owned.exists())

    def test_cleanup_allows_keychain_sidecar_removed_by_security(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            owned = parent / "gui-qml-signing-owned"
            owned.mkdir()
            (owned / SIGNING_MARKER_NAME).write_text(
                SIGNING_MARKER_CONTENT, encoding="utf-8"
            )
            (owned / "developer-id.p12").write_bytes(b"secret")
            keychain = owned / "signing.keychain-db"
            keychain.write_bytes(b"keychain")
            sidecar = owned / ".flD3051040"
            sidecar.write_bytes(b"")

            def fake_security(_command, **_kwargs):
                keychain.unlink()
                sidecar.unlink()
                return subprocess.CompletedProcess(_command, 0)

            with patch("preview_publish.signing.shutil.which", return_value="security"), patch(
                "preview_publish.signing.subprocess.run", side_effect=fake_security
            ):
                cleanup_temporary_keychains(parent)

            self.assertFalse(owned.exists())

    def test_cleanup_refuses_special_marker_without_reading_it(self) -> None:
        if not hasattr(os, "mkfifo"):
            self.skipTest("mkfifo is unavailable")
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            unowned = parent / "gui-qml-signing-fifo"
            unowned.mkdir()
            os.mkfifo(unowned / SIGNING_MARKER_NAME)

            cleanup_temporary_keychains(parent)

            self.assertTrue(unowned.is_dir())

    def test_credentials_are_loaded_then_removed_from_environment(self) -> None:
        values = {
            "APPLE_CERTIFICATE_P12_BASE64": "Y2VydGlmaWNhdGU=",
            "APPLE_CERTIFICATE_PASSWORD": "certificate-password",
            "APPLE_SIGNING_IDENTITY": "Developer ID Application: Example (ABCDE12345)",
            "APPLE_API_KEY_ID": "ABCDEFGHIJ",
            "APPLE_API_ISSUER_ID": "12345678-1234-1234-1234-123456789abc",
            "APPLE_API_PRIVATE_KEY_BASE64": "LS0tLS1CRUdJTiBQUklWQVRFIEtFWS0tLS0tCg==",
        }
        with patch.dict(os.environ, values, clear=False):
            credentials = AppleCredentials.from_environment()

            self.assertEqual(credentials.certificate_bytes(), b"certificate")
            self.assertEqual(
                credentials.api_private_key_bytes(), b"-----BEGIN PRIVATE KEY-----\n"
            )
            self.assertTrue(all(name not in os.environ for name in values))
            self.assertNotIn("certificate-password", repr(credentials))

    def test_missing_credentials_fail_closed(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(PublisherError):
                AppleCredentials.from_environment()

    def test_static_bundle_rejects_nested_macho(self) -> None:
        manifest = load_manifest()
        with tempfile.TemporaryDirectory() as directory:
            app = Path(directory) / "Bitcoin-QML.app"
            self._write_unsigned_bundle(app, manifest.application.executable)

            assert_static_bundle(app, manifest)
            nested = app / "Contents" / "Frameworks" / "unexpected.dylib"
            nested.parent.mkdir(parents=True)
            nested.write_bytes(b"\xcf\xfa\xed\xfe" + b"nested")

            with self.assertRaises(PublisherError):
                assert_static_bundle(app, manifest)

    def test_static_bundle_rejects_non_macho_helper(self) -> None:
        manifest = load_manifest()
        with tempfile.TemporaryDirectory() as directory:
            app = Path(directory) / "Bitcoin-QML.app"
            self._write_unsigned_bundle(app, manifest.application.executable)
            helper = app / "Contents" / "Resources" / "post-install.sh"
            helper.write_text("#!/bin/sh\n", encoding="utf-8")

            with self.assertRaises(PublisherError):
                assert_static_bundle(app, manifest)

    @patch("preview_publish.signing.require_tool", side_effect=lambda name: name)
    @patch("preview_publish.signing.run")
    def test_notarization_requires_accepted_status(self, run_mock, _require_tool) -> None:
        manifest = load_manifest()
        signing = SigningContext(
            Path("keychain"),
            "identity",
            Path("AuthKey_ABCDEFGHIJ.p8"),
            "ABCDEFGHIJ",
            "12345678-1234-1234-1234-123456789abc",
        )
        with tempfile.TemporaryDirectory() as directory:
            layout = Layout.create(Path(directory), manifest)
            dmg = layout.dmg(manifest)
            dmg.parent.mkdir(parents=True)
            dmg.write_bytes(b"dmg")
            def fake_run(command, **_kwargs):
                if "submit" in command:
                    return subprocess.CompletedProcess(
                        command,
                        0,
                        stdout=json.dumps(
                            {
                                "id": "12345678-1234-1234-1234-123456789abc",
                                "status": "Accepted",
                            }
                        ),
                        stderr="",
                    )
                Path(command[4]).write_text('{"status":"Accepted"}\n', encoding="utf-8")
                return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

            run_mock.side_effect = fake_run

            notarize_dmg(dmg, layout, signing)

            submit_command = run_mock.call_args_list[0].args[0]
            self.assertIn("--wait", submit_command)
            self.assertIn("--key", submit_command)
            self.assertIn("--key-id", submit_command)
            self.assertIn("--issuer", submit_command)
            self.assertNotIn("--keychain-profile", submit_command)
            self.assertEqual(run_mock.call_count, 2)
            self.assertTrue(
                (
                    layout.artifacts
                    / "notary-12345678-1234-1234-1234-123456789abc.json"
                ).is_file()
            )


if __name__ == "__main__":
    unittest.main()
