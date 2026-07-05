from __future__ import annotations

import base64
import binascii
import json
import os
import re
import secrets
import shutil
import stat
import subprocess
import tempfile
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from pathlib import Path

from .commands import require_tool, run
from .config import Manifest
from .errors import PublisherError
from .layout import Layout
from .package import (
    create_dmg,
    validate_bundle,
    validate_unsigned_bundle_inventory,
    write_checksums,
)


SECRET_NAMES = (
    "APPLE_CERTIFICATE_P12_BASE64",
    "APPLE_CERTIFICATE_PASSWORD",
    "APPLE_SIGNING_IDENTITY",
    "APPLE_API_KEY_ID",
    "APPLE_API_ISSUER_ID",
    "APPLE_API_PRIVATE_KEY_BASE64",
)
API_KEY_ID_RE = re.compile(r"^[A-Z0-9]{10}$")
API_ISSUER_ID_RE = re.compile(
    r"^[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}$"
)

MACHO_MAGICS = {
    b"\xfe\xed\xfa\xce",
    b"\xce\xfa\xed\xfe",
    b"\xfe\xed\xfa\xcf",
    b"\xcf\xfa\xed\xfe",
    b"\xca\xfe\xba\xbe",
    b"\xbe\xba\xfe\xca",
    b"\xca\xfe\xba\xbf",
    b"\xbf\xba\xfe\xca",
}
SIGNING_MARKER_NAME = ".gui-qml-signing-owner"
SIGNING_MARKER_CONTENT = "gui-qml-preview-publisher signing workspace v1\n"


@dataclass(frozen=True)
class AppleCredentials:
    certificate_base64: str = field(repr=False)
    certificate_password: str = field(repr=False)
    signing_identity: str = field(repr=False)
    api_key_id: str = field(repr=False)
    api_issuer_id: str = field(repr=False)
    api_private_key_base64: str = field(repr=False)

    @classmethod
    def from_environment(cls) -> "AppleCredentials":
        values = {name: os.environ.pop(name, "") for name in SECRET_NAMES}
        missing = [name for name, value in values.items() if not value]
        if missing:
            raise PublisherError(
                "Apple release credentials are not configured: " + ", ".join(missing)
            )
        identity = values["APPLE_SIGNING_IDENTITY"]
        if not identity.startswith("Developer ID Application: "):
            raise PublisherError(
                "APPLE_SIGNING_IDENTITY must be a Developer ID Application identity label"
            )
        api_key_id = values["APPLE_API_KEY_ID"]
        if not API_KEY_ID_RE.fullmatch(api_key_id):
            raise PublisherError("APPLE_API_KEY_ID must be a 10-character App Store Connect key ID")
        api_issuer_id = values["APPLE_API_ISSUER_ID"]
        if not API_ISSUER_ID_RE.fullmatch(api_issuer_id):
            raise PublisherError("APPLE_API_ISSUER_ID must be an App Store Connect issuer UUID")
        return cls(
            certificate_base64=values["APPLE_CERTIFICATE_P12_BASE64"],
            certificate_password=values["APPLE_CERTIFICATE_PASSWORD"],
            signing_identity=identity,
            api_key_id=api_key_id,
            api_issuer_id=api_issuer_id,
            api_private_key_base64=values["APPLE_API_PRIVATE_KEY_BASE64"],
        )

    @staticmethod
    def _decode_base64(value: str, name: str) -> bytes:
        compact = "".join(value.split())
        try:
            decoded = base64.b64decode(compact, validate=True)
        except (ValueError, binascii.Error) as error:
            raise PublisherError(f"{name} is not valid base64") from error
        if not decoded:
            raise PublisherError(f"{name} decodes to an empty file")
        return decoded

    def certificate_bytes(self) -> bytes:
        return self._decode_base64(
            self.certificate_base64, "APPLE_CERTIFICATE_P12_BASE64"
        )

    def api_private_key_bytes(self) -> bytes:
        key = self._decode_base64(
            self.api_private_key_base64, "APPLE_API_PRIVATE_KEY_BASE64"
        )
        if not re.match(br"-----BEGIN [A-Z ]*PRIVATE KEY-----", key):
            raise PublisherError(
                "APPLE_API_PRIVATE_KEY_BASE64 must decode to a PEM private key"
            )
        return key


@dataclass(frozen=True)
class SigningContext:
    keychain: Path
    identity: str
    notary_key: Path
    notary_key_id: str
    notary_issuer_id: str


class TemporaryAppleKeychain(AbstractContextManager[SigningContext]):
    def __init__(self, credentials: AppleCredentials, *, parent: Path | None = None):
        self.credentials = credentials
        self.parent = parent
        self._temporary: tempfile.TemporaryDirectory[str] | None = None
        self._keychain: Path | None = None

    def __enter__(self) -> SigningContext:
        if self.parent is not None:
            self.parent.mkdir(parents=True, exist_ok=True)
        self._temporary = tempfile.TemporaryDirectory(
            prefix="gui-qml-signing-", dir=self.parent
        )
        directory = Path(self._temporary.name)
        (directory / SIGNING_MARKER_NAME).write_text(
            SIGNING_MARKER_CONTENT, encoding="utf-8"
        )
        try:
            return self._configure(directory)
        except BaseException:
            self._cleanup()
            raise

    def _configure(self, directory: Path) -> SigningContext:
        security = require_tool("security")
        certificate_path = directory / "developer-id.p12"
        certificate_path.write_bytes(self.credentials.certificate_bytes())
        certificate_path.chmod(0o600)
        authkey_path = directory / f"AuthKey_{self.credentials.api_key_id}.p8"
        authkey_path.write_bytes(self.credentials.api_private_key_bytes())
        authkey_path.chmod(0o600)
        self._keychain = directory / "signing.keychain-db"
        keychain_password = secrets.token_urlsafe(32)

        run(
            [security, "create-keychain", "-p", keychain_password, self._keychain],
            redacted=(3,),
        )
        run(
            [security, "set-keychain-settings", "-lut", "21600", self._keychain]
        )
        run(
            [security, "unlock-keychain", "-p", keychain_password, self._keychain],
            redacted=(3,),
        )
        run(
            [
                security,
                "import",
                certificate_path,
                "-k",
                self._keychain,
                "-P",
                self.credentials.certificate_password,
                "-T",
                "/usr/bin/codesign",
                "-T",
                "/usr/bin/security",
            ],
            redacted=(6,),
        )
        run(
            [
                security,
                "set-key-partition-list",
                "-S",
                "apple-tool:,apple:,codesign:",
                "-s",
                "-k",
                keychain_password,
                self._keychain,
            ],
            redacted=(6,),
        )

        identity = self._find_identity()
        return SigningContext(
            self._keychain,
            identity,
            authkey_path,
            self.credentials.api_key_id,
            self.credentials.api_issuer_id,
        )

    def _find_identity(self) -> str:
        assert self._keychain is not None
        result = run(
            [
                require_tool("security"),
                "find-identity",
                "-v",
                "-p",
                "codesigning",
                self._keychain,
            ],
            capture=True,
        )
        identities = re.findall(
            r'^\s*\d+\)\s+[0-9A-Fa-f]{40}\s+"(Developer ID Application:[^"]+)"',
            result.stdout,
            re.MULTILINE,
        )
        if len(identities) != 1:
            raise PublisherError(
                "The P12 must contain exactly one valid Developer ID Application identity"
            )
        label = identities[0]
        if label != self.credentials.signing_identity:
            raise PublisherError(
                "Developer ID identity in the P12 does not match APPLE_SIGNING_IDENTITY"
            )
        return label

    def _cleanup(self) -> None:
        security = shutil.which("security")
        if self._keychain is not None and security is not None:
            subprocess.run(
                [security, "delete-keychain", self._keychain],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
        self._keychain = None
        if self._temporary is not None:
            self._temporary.cleanup()
        self._temporary = None

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self._cleanup()
        return None


def _macho_files(app: Path) -> tuple[Path, ...]:
    matches = []
    for path in app.rglob("*"):
        if not path.is_file() or path.is_symlink():
            continue
        with path.open("rb") as handle:
            if handle.read(4) in MACHO_MAGICS:
                matches.append(path)
    return tuple(matches)


def assert_static_bundle(app: Path, manifest: Manifest) -> None:
    validate_unsigned_bundle_inventory(app, manifest)
    expected = app / "Contents" / "MacOS" / manifest.application.executable
    macho_files = _macho_files(app)
    if macho_files != (expected,):
        relative = [str(path.relative_to(app)) for path in macho_files]
        raise PublisherError(
            "Expected exactly one Mach-O executable in the static app; found: "
            + (", ".join(relative) or "none")
        )


def sign_app(
    app: Path, manifest: Manifest, signing: SigningContext
) -> None:
    validate_bundle(app, manifest)
    assert_static_bundle(app, manifest)
    run(
        [
            require_tool("codesign"),
            "--force",
            "--sign",
            signing.identity,
            "--keychain",
            signing.keychain,
            "--options",
            "runtime",
            "--timestamp",
            app,
        ]
    )
    run(
        [
            require_tool("codesign"),
            "--verify",
            "--deep",
            "--strict",
            "--verbose=4",
            app,
        ]
    )
    details = run(
        [require_tool("codesign"), "--display", "--verbose=4", app],
        capture=True,
    )
    if "runtime" not in details.stderr:
        raise PublisherError("Signed app is missing the hardened runtime flag")


def sign_dmg(dmg: Path, signing: SigningContext) -> None:
    run(
        [
            require_tool("codesign"),
            "--force",
            "--sign",
            signing.identity,
            "--keychain",
            signing.keychain,
            "--timestamp",
            dmg,
        ]
    )
    run([require_tool("codesign"), "--verify", "--verbose=4", dmg])


def _notary_arguments(signing: SigningContext) -> list[str | Path]:
    return [
        "--key",
        signing.notary_key,
        "--key-id",
        signing.notary_key_id,
        "--issuer",
        signing.notary_issuer_id,
    ]


def notarize_dmg(dmg: Path, layout: Layout, signing: SigningContext) -> None:
    result = run(
        [
            require_tool("xcrun"),
            "notarytool",
            "submit",
            dmg,
            *_notary_arguments(signing),
            "--wait",
            "--timeout",
            "2h",
            "--no-progress",
            "--output-format",
            "json",
        ],
        capture=True,
        check=False,
    )
    try:
        response = json.loads(result.stdout)
    except json.JSONDecodeError:
        response = {}
    if not isinstance(response, dict):
        response = {}
    submission_id = response.get("id")
    if not isinstance(submission_id, str) or not re.fullmatch(
        r"[0-9A-Fa-f-]{36}", submission_id
    ):
        submission_id = None
    status = response.get("status")
    layout.artifacts.mkdir(parents=True, exist_ok=True)
    log_path = layout.artifacts / f"notary-{submission_id or 'submit-error'}.json"
    if result.returncode != 0 or status != "Accepted" or not submission_id:
        if submission_id:
            _download_notary_log(submission_id, log_path, signing)
        else:
            log_path.write_text(
                f"stdout:\n{result.stdout}\n\nstderr:\n{result.stderr}\n",
                encoding="utf-8",
            )
        raise PublisherError(
            f"Apple notarization was not accepted (status={status!r}, "
            f"submission={submission_id!r}); details: {log_path}"
        )
    if not _download_notary_log(submission_id, log_path, signing):
        raise PublisherError(
            f"Notarization was accepted but its log could not be preserved: {log_path}"
        )
    print(f"Apple notarization accepted: {submission_id}")


def _download_notary_log(
    submission_id: str, log_path: Path, signing: SigningContext
) -> bool:
    log_result = run(
        [
            require_tool("xcrun"),
            "notarytool",
            "log",
            submission_id,
            log_path,
            *_notary_arguments(signing),
        ],
        capture=True,
        check=False,
    )
    valid = False
    if log_result.returncode == 0 and log_path.is_file() and log_path.stat().st_size:
        try:
            valid = isinstance(json.loads(log_path.read_text(encoding="utf-8")), dict)
        except (OSError, json.JSONDecodeError):
            valid = False
    if not valid:
        log_path.write_text(
            f"stdout:\n{log_result.stdout}\n\nstderr:\n{log_result.stderr}\n",
            encoding="utf-8",
        )
    return valid


def verify_finalized_dmg(dmg: Path) -> None:
    xcrun = require_tool("xcrun")
    run([xcrun, "stapler", "validate", dmg])
    run([require_tool("codesign"), "--verify", "--verbose=4", dmg])
    run([require_tool("hdiutil"), "verify", dmg])
    run(
        [
            require_tool("spctl"),
            "--assess",
            "--type",
            "open",
            "--context",
            "context:primary-signature",
            "--verbose=4",
            dmg,
        ]
    )


def staple_and_verify(dmg: Path) -> None:
    run([require_tool("xcrun"), "stapler", "staple", dmg])
    verify_finalized_dmg(dmg)


def check_credentials() -> None:
    credentials = AppleCredentials.from_environment()
    with TemporaryAppleKeychain(credentials) as signing:
        run([require_tool("xcrun"), "notarytool", "--version"], capture=True)
        print("Validated the requested Developer ID Application identity and notary API key")


def finalize(layout: Layout, manifest: Manifest) -> Path:
    credentials = AppleCredentials.from_environment()
    app = layout.deployed_app(manifest)
    with TemporaryAppleKeychain(credentials, parent=layout.work) as signing:
        sign_app(app, manifest, signing)
        dmg = create_dmg(layout, manifest)
        sign_dmg(dmg, signing)
        notarize_dmg(dmg, layout, signing)
        staple_and_verify(dmg)
    write_checksums(layout, (dmg,))
    print(f"Finalized signed and notarized artifact: {dmg}")
    return dmg


def cleanup_temporary_keychains(parent: Path) -> None:
    security = shutil.which("security")
    if not parent.is_dir():
        return
    for directory in parent.glob("gui-qml-signing-*"):
        if not directory.is_dir() or directory.is_symlink():
            continue
        marker = directory / SIGNING_MARKER_NAME
        try:
            marker_stat = marker.lstat()
            if (
                not stat.S_ISREG(marker_stat.st_mode)
                or marker_stat.st_size != len(SIGNING_MARKER_CONTENT.encode("utf-8"))
                or marker.read_text(encoding="utf-8") != SIGNING_MARKER_CONTENT
            ):
                continue
        except OSError:
            continue

        children = list(directory.iterdir())
        unsafe = []
        for child in children:
            try:
                mode = child.lstat().st_mode
            except OSError:
                unsafe.append(child.name)
                continue
            if not stat.S_ISREG(mode):
                unsafe.append(child.name)
        if unsafe:
            raise PublisherError(
                f"Refusing to clean signing workspace with unsafe contents: {directory}"
            )

        keychain = directory / "signing.keychain-db"
        if keychain.is_file() and security is not None:
            subprocess.run(
                [security, "delete-keychain", keychain],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )

        # `security create-keychain` creates a hidden `.fl…` sidecar on macOS.
        # `security delete-keychain` removes it; only exact publisher files may
        # remain before the directory is removed manually.
        allowed_names = {
            SIGNING_MARKER_NAME,
            "developer-id.p12",
            "signing.keychain-db",
        }
        remaining = list(directory.iterdir())
        unexpected = [
            child.name
            for child in remaining
            if child.name not in allowed_names
            and not re.fullmatch(r"AuthKey_[A-Z0-9]{10}\.p8", child.name)
        ]
        if unexpected:
            raise PublisherError(
                f"Refusing to clean signing workspace with unexpected contents: {directory}"
            )
        for child in remaining:
            child.unlink()
        directory.rmdir()
