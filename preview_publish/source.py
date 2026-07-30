from __future__ import annotations

import hashlib
import shutil
import subprocess
from pathlib import Path

from .commands import output, require_tool, run
from .config import Manifest
from .errors import PublisherError
from .layout import Layout

WORKSPACE_MARKER = "gui-qml-preview-publisher workspace v1\n"


def _git(*args: str | Path, cwd: Path) -> None:
    run([require_tool("git"), *args], cwd=cwd)


def _git_output(*args: str | Path, cwd: Path) -> str:
    return output([require_tool("git"), *args], cwd=cwd)


def _verify_checkout(layout: Layout, manifest: Manifest) -> None:
    head = _git_output("rev-parse", "HEAD", cwd=layout.source)
    if head != manifest.source.commit:
        raise PublisherError(
            f"Source checkout is {head}, expected {manifest.source.commit}. "
            "Run checkout with --clean to recreate the tool-owned source tree."
        )
    bitcoin_head = _git_output("rev-parse", "HEAD", cwd=layout.source / "bitcoin")
    if bitcoin_head != manifest.source.bitcoin_commit:
        raise PublisherError(
            f"Bitcoin Core submodule is {bitcoin_head}, expected "
            f"{manifest.source.bitcoin_commit}"
        )


def _apply_patch(target: Path, patch: Path) -> None:
    git = require_tool("git")
    # Probe without the command helper because a non-zero result is the normal
    # "not applied yet" branch.
    reverse_probe = subprocess.run(
        [git, "apply", "--reverse", "--check", patch],
        cwd=target,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if reverse_probe.returncode == 0:
        print(f"Patch already applied: {patch.name}")
        return
    run([git, "apply", "--check", patch], cwd=target)
    run([git, "apply", "--whitespace=nowarn", patch], cwd=target)


def _verify_depends_patch(path: Path, expected_sha256: str) -> None:
    try:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as error:
        raise PublisherError(f"Cannot read branch depends patch {path}: {error}") from error
    if digest != expected_sha256:
        raise PublisherError(
            f"Branch depends patch digest is {digest}, expected {expected_sha256}"
        )


def _patched_tree_digest(path: Path) -> str:
    git = require_tool("git")
    status = subprocess.run(
        [git, "status", "--porcelain=v1", "--untracked-files=all"],
        cwd=path,
        check=True,
        stdout=subprocess.PIPE,
        text=True,
    ).stdout
    untracked = [line[3:] for line in status.splitlines() if line.startswith("?? ")]
    if untracked:
        raise PublisherError(
            f"Unexpected untracked files in pinned checkout {path}: {', '.join(untracked)}"
        )
    diff = subprocess.run(
        [
            git,
            "-c",
            "core.abbrev=40",
            "diff",
            "--binary",
            "--full-index",
            "--no-ext-diff",
            "--no-renames",
            "--diff-algorithm=myers",
            "--no-indent-heuristic",
            "HEAD",
            "--",
        ],
        cwd=path,
        check=True,
        stdout=subprocess.PIPE,
    ).stdout
    return hashlib.sha256(diff).hexdigest()


def _verify_patched_tree(path: Path, expected_sha256: str) -> None:
    digest = _patched_tree_digest(path)
    if digest != expected_sha256:
        raise PublisherError(
            f"Patched tree digest for {path} is {digest}, expected {expected_sha256}. "
            "Run checkout with --clean to discard unexpected tool-workspace changes."
        )


def _clean_source(layout: Layout) -> None:
    marker = layout.work / ".gui-qml-preview-workspace"
    try:
        marker_contents = marker.read_text(encoding="utf-8")
    except OSError as error:
        raise PublisherError(
            f"Refusing to delete {layout.source}: the publisher workspace marker is missing"
        ) from error
    if marker_contents != WORKSPACE_MARKER:
        raise PublisherError(
            f"Refusing to delete {layout.source}: the publisher workspace marker is invalid"
        )
    shutil.rmtree(layout.source)


def checkout(layout: Layout, manifest: Manifest, *, clean: bool = False) -> None:
    require_tool("git")
    if clean and layout.source.exists():
        _clean_source(layout)
    if layout.source.exists() and not (layout.source / ".git").exists():
        raise PublisherError(f"Refusing to reuse non-Git directory: {layout.source}")

    if not layout.source.exists():
        layout.work.mkdir(parents=True, exist_ok=True)
        (layout.work / ".gui-qml-preview-workspace").write_text(
            WORKSPACE_MARKER, encoding="utf-8"
        )
        _git("init", layout.source, cwd=layout.work)
        _git("remote", "add", "origin", manifest.source.repository, cwd=layout.source)
        _git(
            "fetch",
            "--depth=1",
            "origin",
            manifest.source.fetch_ref,
            cwd=layout.source,
        )
        fetched = _git_output("rev-parse", "FETCH_HEAD", cwd=layout.source)
        if fetched != manifest.source.commit:
            raise PublisherError(
                f"Configured source ref resolved to {fetched}, but the reviewed pin is "
                f"{manifest.source.commit}. Review and update the manifest deliberately."
            )
        _git("checkout", "--detach", manifest.source.commit, cwd=layout.source)
        _git(
            "submodule",
            "update",
            "--init",
            "--recursive",
            "--depth=1",
            cwd=layout.source,
        )

    _verify_checkout(layout, manifest)
    marker = layout.work / ".gui-qml-preview-workspace"
    if not marker.exists():
        marker.write_text(WORKSPACE_MARKER, encoding="utf-8")

    depends_patch = layout.source / manifest.source.depends_patch
    _verify_depends_patch(depends_patch, manifest.source.depends_patch_sha256)
    _apply_patch(layout.source / "bitcoin", depends_patch)

    for patch in manifest.patches:
        if patch.target == "source":
            target = layout.source
        elif patch.target == "bitcoin":
            target = layout.source / "bitcoin"
        else:
            raise PublisherError(f"Unknown patch target: {patch.target}")
        _apply_patch(target, manifest.repository_path(patch.path))

    _verify_patched_tree(
        layout.source / "bitcoin", manifest.source.patched_bitcoin_diff_sha256
    )
    _verify_patched_tree(layout.source, manifest.source.patched_source_diff_sha256)
    _verify_checkout(layout, manifest)
    print(f"Prepared source {manifest.source.commit} in {layout.source}")
