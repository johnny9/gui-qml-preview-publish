from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .errors import PublisherError


SHA1_RE = re.compile(r"^[0-9a-f]{40}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def repository_root() -> Path:
    return Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class SourceConfig:
    repository: str
    reference: str
    fetch_ref: str
    commit: str
    bitcoin_commit: str
    depends_patch: str
    depends_patch_sha256: str
    patched_source_diff_sha256: str
    patched_bitcoin_diff_sha256: str


@dataclass(frozen=True)
class BuildConfig:
    build_type: str
    build_directory: str
    target: str
    minimum_macos: str
    architecture: str
    display_version: str


@dataclass(frozen=True)
class ApplicationConfig:
    bundle_name: str
    display_name: str
    executable: str
    bundle_identifier: str
    volume_name: str
    artifact_basename: str
    url_scheme: str
    short_version: str
    bundle_version: str


@dataclass(frozen=True)
class PatchConfig:
    path: str
    target: str


@dataclass(frozen=True)
class Manifest:
    path: Path
    root: Path
    source: SourceConfig
    build: BuildConfig
    application: ApplicationConfig
    patches: tuple[PatchConfig, ...]

    def repository_path(self, relative: str) -> Path:
        candidate = (self.root / relative).resolve()
        if not candidate.is_relative_to(self.root):
            raise PublisherError(f"Manifest path leaves the repository: {relative}")
        return candidate


def _table(document: dict[str, Any], name: str) -> dict[str, Any]:
    value = document.get(name)
    if not isinstance(value, dict):
        raise PublisherError(f"Manifest table [{name}] is missing")
    return value


def _construct(cls: type[Any], values: dict[str, Any], table: str) -> Any:
    try:
        return cls(**values)
    except TypeError as error:
        raise PublisherError(f"Invalid [{table}] manifest fields: {error}") from error


def load_manifest(path: Path | None = None) -> Manifest:
    root = repository_root()
    manifest_path = (path or root / "config" / "release.toml").resolve()
    try:
        with manifest_path.open("rb") as handle:
            document = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise PublisherError(f"Cannot load release manifest {manifest_path}: {error}") from error

    if document.get("schema_version") != 1:
        raise PublisherError("Unsupported or missing manifest schema_version")

    source = _construct(SourceConfig, _table(document, "source"), "source")
    build = _construct(BuildConfig, _table(document, "build"), "build")
    application = _construct(
        ApplicationConfig, _table(document, "application"), "application"
    )
    raw_patches = document.get("patches")
    if not isinstance(raw_patches, list) or not raw_patches:
        raise PublisherError("Manifest must contain at least one [[patches]] entry")
    patches = tuple(_construct(PatchConfig, item, "patches") for item in raw_patches)

    if not SHA1_RE.fullmatch(source.commit):
        raise PublisherError("source.commit must be a full lowercase Git SHA-1")
    if not SHA1_RE.fullmatch(source.bitcoin_commit):
        raise PublisherError("source.bitcoin_commit must be a full lowercase Git SHA-1")
    source_digests = {
        "source.depends_patch_sha256": source.depends_patch_sha256,
        "source.patched_source_diff_sha256": source.patched_source_diff_sha256,
        "source.patched_bitcoin_diff_sha256": source.patched_bitcoin_diff_sha256,
    }
    for name, digest in source_digests.items():
        if not SHA256_RE.fullmatch(digest):
            raise PublisherError(f"{name} must be lowercase SHA-256")
    if build.display_version != source.commit[:12]:
        raise PublisherError("build.display_version must be the pinned source short hash")
    if build.architecture not in {"arm64", "x86_64"}:
        raise PublisherError(f"Unsupported macOS architecture: {build.architecture}")
    if "/" in application.bundle_name or not application.bundle_name:
        raise PublisherError("application.bundle_name must be a non-empty filename")
    if application.bundle_identifier != "org.bitcoincore.gui-qml.preview":
        raise PublisherError("The preview bundle identifier must remain isolated")

    manifest = Manifest(
        path=manifest_path,
        root=root,
        source=source,
        build=build,
        application=application,
        patches=patches,
    )
    for relative in (source.depends_patch, *(item.path for item in patches)):
        # The PR-owned depends patch lives in the future checkout, not this repo.
        if relative == source.depends_patch:
            continue
        if not manifest.repository_path(relative).is_file():
            raise PublisherError(f"Configured patch does not exist: {relative}")
    return manifest
