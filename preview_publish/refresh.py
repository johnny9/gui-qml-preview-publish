from __future__ import annotations

import hashlib
import re
from pathlib import Path

from .config import Manifest, SHA1_RE, load_manifest
from .errors import PublisherError
from .layout import Layout
from .source import (
    WORKSPACE_MARKER,
    _apply_patch,
    _clean_source,
    _git,
    _git_output,
    _patched_tree_digest,
    checkout,
)


_SECTION_RE = re.compile(r"^\[([A-Za-z0-9_-]+)\]\s*$")
_KEY_RE = re.compile(r'^(\s*)([A-Za-z0-9_-]+)\s*=\s*".*"\s*$')


def _render_manifest(template: str, replacements: dict[tuple[str, str], str]) -> str:
    section = ""
    replaced: set[tuple[str, str]] = set()
    result: list[str] = []
    for original in template.splitlines(keepends=True):
        line = original.rstrip("\r\n")
        newline = original[len(line) :]
        section_match = _SECTION_RE.fullmatch(line)
        if section_match is not None:
            section = section_match.group(1)
            result.append(original)
            continue
        key_match = _KEY_RE.fullmatch(line)
        key = None if key_match is None else (section, key_match.group(2))
        if key not in replacements:
            result.append(original)
            continue
        if key in replaced:
            raise PublisherError(
                f"Manifest template contains duplicate [{section}] {key[1]}"
            )
        replacement = f'{key_match.group(1)}{key[1]} = "{replacements[key]}"{newline}'
        result.append(replacement)
        replaced.add(key)
    missing = replacements.keys() - replaced
    if missing:
        names = ", ".join(f"[{section}] {key}" for section, key in sorted(missing))
        raise PublisherError(
            f"Manifest template is missing replacement fields: {names}"
        )
    return "".join(result)


def _write_refreshed_manifest(
    template: Manifest,
    output_path: Path,
    *,
    source_commit: str,
    bitcoin_commit: str,
    depends_patch_sha256: str,
    source_diff_sha256: str,
    bitcoin_diff_sha256: str,
) -> Manifest:
    if output_path.resolve() == template.path:
        raise PublisherError("Refusing to overwrite the checked-in release manifest")
    try:
        contents = template.path.read_text(encoding="utf-8")
    except OSError as error:
        raise PublisherError(
            f"Cannot read manifest template {template.path}: {error}"
        ) from error
    rendered = _render_manifest(
        contents,
        {
            ("source", "commit"): source_commit,
            ("source", "bitcoin_commit"): bitcoin_commit,
            ("source", "depends_patch_sha256"): depends_patch_sha256,
            ("source", "patched_source_diff_sha256"): source_diff_sha256,
            ("source", "patched_bitcoin_diff_sha256"): bitcoin_diff_sha256,
            ("build", "display_version"): source_commit[:12],
        },
    )
    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(rendered, encoding="utf-8")
    except OSError as error:
        raise PublisherError(
            f"Cannot write refreshed manifest {output_path}: {error}"
        ) from error
    return load_manifest(output_path)


def refresh_manifest(
    layout: Layout,
    template: Manifest,
    source_commit: str,
    output_path: Path,
) -> Manifest:
    if not SHA1_RE.fullmatch(source_commit):
        raise PublisherError(
            "The refreshed source commit must be a full lowercase SHA-1"
        )
    if layout.source.exists():
        _clean_source(layout)
    layout.work.mkdir(parents=True, exist_ok=True)
    (layout.work / ".gui-qml-preview-workspace").write_text(
        WORKSPACE_MARKER, encoding="utf-8"
    )

    _git("init", layout.source, cwd=layout.work)
    _git("remote", "add", "origin", template.source.repository, cwd=layout.source)
    _git(
        "fetch",
        "--depth=1",
        "origin",
        template.source.fetch_ref,
        cwd=layout.source,
    )
    fetched = _git_output("rev-parse", "FETCH_HEAD", cwd=layout.source)
    if fetched != source_commit:
        raise PublisherError(
            f"Source ref moved from detected commit {source_commit} to {fetched}; "
            "the next scheduled run will retry the new head"
        )
    _git("checkout", "--detach", source_commit, cwd=layout.source)
    _git(
        "submodule",
        "update",
        "--init",
        "--recursive",
        "--depth=1",
        cwd=layout.source,
    )

    bitcoin = layout.source / "bitcoin"
    bitcoin_commit = _git_output("rev-parse", "HEAD", cwd=bitcoin)
    depends_patch = layout.source / template.source.depends_patch
    try:
        depends_patch_sha256 = hashlib.sha256(depends_patch.read_bytes()).hexdigest()
    except OSError as error:
        raise PublisherError(
            f"Cannot read branch depends patch {depends_patch}: {error}"
        ) from error
    _apply_patch(bitcoin, depends_patch)

    for patch in template.patches:
        if patch.target == "source":
            target = layout.source
        elif patch.target == "bitcoin":
            target = bitcoin
        else:
            raise PublisherError(f"Unknown patch target: {patch.target}")
        _apply_patch(target, template.repository_path(patch.path))

    bitcoin_diff_sha256 = _patched_tree_digest(bitcoin)
    source_diff_sha256 = _patched_tree_digest(layout.source)
    refreshed = _write_refreshed_manifest(
        template,
        output_path.resolve(),
        source_commit=source_commit,
        bitcoin_commit=bitcoin_commit,
        depends_patch_sha256=depends_patch_sha256,
        source_diff_sha256=source_diff_sha256,
        bitcoin_diff_sha256=bitcoin_diff_sha256,
    )
    checkout(layout, refreshed)
    print(f"Wrote exact source manifest to {refreshed.path}")
    return refreshed
