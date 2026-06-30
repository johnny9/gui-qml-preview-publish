from __future__ import annotations

import hashlib
import os
import plistlib
import shutil
import stat
from pathlib import Path

from .build import validate_binary
from .commands import require_tool, run
from .config import Manifest
from .errors import PublisherError
from .layout import Layout


def _replace_directory(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True)


def assemble_bundle(layout: Layout, manifest: Manifest) -> Path:
    binary = layout.executable(manifest)
    validate_binary(binary, manifest)
    app = layout.staged_app(manifest)
    if layout.stage.exists():
        shutil.rmtree(layout.stage)
    macos = app / "Contents" / "MacOS"
    resources = app / "Contents" / "Resources"
    macos.mkdir(parents=True)
    resources.mkdir(parents=True)

    destination = macos / manifest.application.executable
    shutil.copy2(binary, destination)
    destination.chmod(destination.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    icon = layout.source / "bitcoin" / "src" / "qt" / "res" / "icons" / "bitcoin.icns"
    if not icon.is_file():
        raise PublisherError(f"Bitcoin Core app icon is missing: {icon}")
    shutil.copy2(icon, resources / "bitcoin.icns")

    info = {
        "CFBundleDevelopmentRegion": "en",
        "CFBundleDisplayName": manifest.application.display_name,
        "CFBundleExecutable": manifest.application.executable,
        "CFBundleIconFile": "bitcoin.icns",
        "CFBundleIdentifier": manifest.application.bundle_identifier,
        "CFBundleInfoDictionaryVersion": "6.0",
        "CFBundleName": manifest.application.bundle_name,
        "CFBundlePackageType": "APPL",
        "CFBundleShortVersionString": manifest.application.short_version,
        "CFBundleSupportedPlatforms": ["MacOSX"],
        "CFBundleURLTypes": [
            {
                "CFBundleTypeRole": "Editor",
                "CFBundleURLName": "org.bitcoin.BitcoinPayment",
                "CFBundleURLSchemes": [manifest.application.url_scheme],
            }
        ],
        "CFBundleVersion": manifest.application.bundle_version,
        "LSApplicationCategoryType": "public.app-category.finance",
        "LSMinimumSystemVersion": manifest.build.minimum_macos,
        "NSHighResolutionCapable": True,
        "NSPrincipalClass": "NSApplication",
    }
    with (app / "Contents" / "Info.plist").open("wb") as handle:
        plistlib.dump(info, handle, fmt=plistlib.FMT_XML, sort_keys=False)
    (app / "Contents" / "PkgInfo").write_bytes(b"APPL????")
    return app


def validate_bundle(app: Path, manifest: Manifest) -> None:
    plist = app / "Contents" / "Info.plist"
    run([require_tool("plutil"), "-lint", plist])
    with plist.open("rb") as handle:
        info = plistlib.load(handle)
    expected = {
        "CFBundleExecutable": manifest.application.executable,
        "CFBundleIdentifier": manifest.application.bundle_identifier,
        "CFBundleName": manifest.application.bundle_name,
        "LSMinimumSystemVersion": manifest.build.minimum_macos,
    }
    for key, value in expected.items():
        if info.get(key) != value:
            raise PublisherError(f"Unexpected {key} in deployed app: {info.get(key)!r}")
    binary = app / "Contents" / "MacOS" / manifest.application.executable
    validate_binary(binary, manifest)
    if not (app / "Contents" / "Resources" / "qt.conf").is_file():
        raise PublisherError("macdeployqtplus did not create qt.conf")


def package(layout: Layout, manifest: Manifest) -> Path:
    staged = assemble_bundle(layout, manifest)
    vendor = (
        manifest.root
        / "vendor"
        / "bitcoin-core"
        / "macdeploy"
        / "macdeployqtplus"
    )
    if not vendor.is_file():
        raise PublisherError(f"Vendored macdeployqtplus is missing: {vendor}")
    layout.deploy.mkdir(parents=True, exist_ok=True)
    run(
        [
            require_tool("python3"),
            vendor,
            staged,
            "-no-plugins",
            "-output-dir",
            layout.deploy,
        ],
        cwd=layout.work,
    )
    deployed = layout.deployed_app(manifest)
    validate_bundle(deployed, manifest)
    print(f"Packaged {deployed}")
    return deployed


def write_checksums(layout: Layout) -> Path:
    artifacts = sorted(path for path in layout.artifacts.iterdir() if path.is_file() and path.name != "SHA256SUMS")
    lines = []
    for path in artifacts:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        lines.append(f"{digest.hexdigest()}  {path.name}")
    checksum_file = layout.artifacts / "SHA256SUMS"
    checksum_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return checksum_file


def create_dmg(layout: Layout, manifest: Manifest) -> Path:
    app = layout.deployed_app(manifest)
    validate_bundle(app, manifest)
    require_tool("hdiutil")
    dmg_root = layout.work / "dmg-root"
    _replace_directory(dmg_root)
    shutil.copytree(app, dmg_root / app.name, symlinks=True)
    os.symlink("/Applications", dmg_root / "Applications")

    layout.artifacts.mkdir(parents=True, exist_ok=True)
    dmg = layout.dmg(manifest)
    if dmg.exists():
        dmg.unlink()
    run(
        [
            require_tool("hdiutil"),
            "create",
            "-volname",
            manifest.application.volume_name,
            "-srcfolder",
            dmg_root,
            "-ov",
            "-format",
            "UDZO",
            dmg,
        ]
    )
    run([require_tool("hdiutil"), "verify", dmg])
    print(f"Created {dmg}")
    return dmg
