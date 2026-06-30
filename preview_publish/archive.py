from __future__ import annotations

import shutil
import tarfile
from pathlib import Path, PurePosixPath

from .config import Manifest
from .errors import PublisherError
from .layout import Layout
from .package import validate_bundle, validate_unsigned_bundle_inventory


MAX_ARCHIVE_BYTES = 1024 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = 10_000


def _normalized_tar_info(info: tarfile.TarInfo) -> tarfile.TarInfo:
    info.uid = 0
    info.gid = 0
    info.uname = ""
    info.gname = ""
    info.mtime = 0
    return info


def archive_app(layout: Layout, manifest: Manifest, output: Path) -> Path:
    app = layout.deployed_app(manifest)
    validate_bundle(app, manifest)
    validate_unsigned_bundle_inventory(app, manifest)
    symlinks = [path for path in app.rglob("*") if path.is_symlink()]
    if symlinks:
        raise PublisherError(
            "Unsigned static app contains unexpected symlinks: "
            + ", ".join(str(path.relative_to(app)) for path in symlinks)
        )
    output = output.resolve()
    if output.is_relative_to(app.resolve()):
        raise PublisherError("Unsigned app archive output cannot be inside the app bundle")
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        output.unlink()
    with tarfile.open(output, "w:gz", format=tarfile.PAX_FORMAT) as archive:
        archive.add(
            app,
            arcname=app.name,
            recursive=True,
            filter=_normalized_tar_info,
        )
    print(f"Archived unsigned app: {output}")
    return output


def _validated_members(
    archive: tarfile.TarFile, expected_root: str
) -> tuple[tarfile.TarInfo, ...]:
    members = archive.getmembers()
    if len(members) > MAX_ARCHIVE_MEMBERS:
        raise PublisherError("Unsigned app archive contains too many entries")
    total_size = 0
    names: set[str] = set()
    for member in members:
        path = PurePosixPath(member.name)
        if (
            path.is_absolute()
            or not path.parts
            or path.parts[0] != expected_root
            or ".." in path.parts
        ):
            raise PublisherError(f"Unsafe path in unsigned app archive: {member.name}")
        normalized = path.as_posix()
        if normalized in names:
            raise PublisherError(f"Duplicate path in unsigned app archive: {member.name}")
        names.add(normalized)
        if not (member.isdir() or member.isfile()):
            raise PublisherError(
                f"Links and special files are forbidden in unsigned app archive: {member.name}"
            )
        total_size += member.size
        if total_size > MAX_ARCHIVE_BYTES:
            raise PublisherError("Unsigned app archive exceeds the 1 GiB safety limit")
    return tuple(members)


def extract_app_archive(
    archive_path: Path, layout: Layout, manifest: Manifest
) -> Path:
    if layout.deploy.exists():
        raise PublisherError(f"Refusing to overwrite extraction directory: {layout.deploy}")
    layout.deploy.mkdir(parents=True)
    expected_root = f"{manifest.application.bundle_name}.app"
    try:
        with tarfile.open(archive_path, "r:gz") as archive:
            members = _validated_members(archive, expected_root)
            for member in members:
                destination = layout.deploy.joinpath(*PurePosixPath(member.name).parts)
                if member.isdir():
                    destination.mkdir(parents=True, exist_ok=True)
                    destination.chmod(member.mode & 0o777)
                    continue
                destination.parent.mkdir(parents=True, exist_ok=True)
                source = archive.extractfile(member)
                if source is None:
                    raise PublisherError(f"Cannot read archive member: {member.name}")
                with source, destination.open("wb") as output:
                    shutil.copyfileobj(source, output)
                destination.chmod(member.mode & 0o777)
    except PublisherError:
        shutil.rmtree(layout.deploy)
        raise
    except (OSError, tarfile.TarError) as error:
        shutil.rmtree(layout.deploy)
        raise PublisherError(f"Cannot extract unsigned app archive: {error}") from error
    app = layout.deployed_app(manifest)
    try:
        validate_bundle(app, manifest)
        validate_unsigned_bundle_inventory(app, manifest)
    except BaseException:
        shutil.rmtree(layout.deploy)
        raise
    print(f"Extracted and validated unsigned app: {app}")
    return app
