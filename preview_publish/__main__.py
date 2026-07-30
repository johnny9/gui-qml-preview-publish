from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .archive import archive_app, extract_app_archive
from .build import build, export_linux_binary
from .config import load_manifest
from .errors import PublisherError
from .layout import Layout
from .package import create_dmg, package, write_checksums
from .refresh import refresh_manifest
from .release import publish_releases
from .signing import check_credentials, cleanup_temporary_keychains, finalize
from .source import checkout
from .trigger import source_status, write_github_output


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gui-qml-preview",
        description="Build the pinned Bitcoin Core App macOS and Linux previews.",
    )
    parser.add_argument("--manifest", type=Path, help="release TOML manifest")
    parser.add_argument("--work-dir", type=Path, default=Path("build"))
    subparsers = parser.add_subparsers(dest="command", required=True)

    checkout_parser = subparsers.add_parser("checkout", help="fetch and patch source")
    checkout_parser.add_argument("--clean", action="store_true")

    status_parser = subparsers.add_parser(
        "source-status", help="compare the source branch with Latest Preview"
    )
    status_parser.add_argument("--github-output", type=Path, required=True)

    refresh_parser = subparsers.add_parser(
        "refresh-manifest", help="create an exact manifest for a detected source head"
    )
    refresh_parser.add_argument("--source-commit", required=True)
    refresh_parser.add_argument("--output", type=Path, required=True)

    build_parser = subparsers.add_parser(
        "build", help="build depends and Bitcoin Core App for the current platform"
    )
    build_parser.add_argument("--jobs", type=int)

    subparsers.add_parser(
        "export-linux", help="validate and export the raw Linux executable"
    )
    subparsers.add_parser("package", help="create and deploy Bitcoin-QML.app")
    subparsers.add_parser("dmg", help="create an unsigned preview DMG")
    subparsers.add_parser(
        "credentials", help="validate Developer ID and Apple notary credentials"
    )
    subparsers.add_parser(
        "finalize", help="sign, notarize, staple, and checksum the preview DMG"
    )
    subparsers.add_parser(
        "release", help="publish versioned and Latest Preview releases"
    )
    subparsers.add_parser("cleanup", help="remove temporary Apple keychains")

    archive_parser = subparsers.add_parser(
        "archive", help="archive the unsigned app for isolated signing"
    )
    archive_parser.add_argument("--output", type=Path, required=True)

    extract_parser = subparsers.add_parser(
        "extract", help="safely extract and validate the unsigned app"
    )
    extract_parser.add_argument("--archive", type=Path, required=True)

    all_parser = subparsers.add_parser("all", help="run the unsigned pipeline")
    all_parser.add_argument("--clean", action="store_true")
    all_parser.add_argument("--jobs", type=int)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        manifest = load_manifest(args.manifest)
        layout = Layout.create(args.work_dir, manifest)
        if args.command == "checkout":
            checkout(layout, manifest, clean=args.clean)
        elif args.command == "source-status":
            status = source_status(manifest)
            write_github_output(args.github_output, status)
            state = "changed" if status.should_publish else "already published"
            print(f"Qt6 source {status.commit}: {state}")
        elif args.command == "refresh-manifest":
            refresh_manifest(layout, manifest, args.source_commit, args.output)
        elif args.command == "build":
            build(layout, manifest, jobs=args.jobs)
        elif args.command == "export-linux":
            export_linux_binary(layout, manifest)
        elif args.command == "package":
            package(layout, manifest)
        elif args.command == "dmg":
            dmg = create_dmg(layout, manifest)
            write_checksums(layout, (dmg,))
        elif args.command == "credentials":
            check_credentials()
        elif args.command == "finalize":
            finalize(layout, manifest)
        elif args.command == "release":
            publish_releases(layout, manifest)
        elif args.command == "cleanup":
            cleanup_temporary_keychains(layout.work)
        elif args.command == "archive":
            archive_app(layout, manifest, args.output)
        elif args.command == "extract":
            extract_app_archive(args.archive, layout, manifest)
        elif args.command == "all":
            checkout(layout, manifest, clean=args.clean)
            build(layout, manifest, jobs=args.jobs)
            package(layout, manifest)
            dmg = create_dmg(layout, manifest)
            write_checksums(layout, (dmg,))
        return 0
    except PublisherError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
