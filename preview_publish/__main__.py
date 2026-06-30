from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .build import build
from .config import load_manifest
from .errors import PublisherError
from .layout import Layout
from .package import create_dmg, package, write_checksums
from .source import checkout


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gui-qml-preview",
        description="Build and package the pinned gui-qml macOS preview.",
    )
    parser.add_argument("--manifest", type=Path, help="release TOML manifest")
    parser.add_argument("--work-dir", type=Path, default=Path("build"))
    subparsers = parser.add_subparsers(dest="command", required=True)

    checkout_parser = subparsers.add_parser("checkout", help="fetch and patch source")
    checkout_parser.add_argument("--clean", action="store_true")

    build_parser = subparsers.add_parser("build", help="build depends and gui-qml")
    build_parser.add_argument("--jobs", type=int)

    subparsers.add_parser("package", help="create and deploy Bitcoin-QML.app")
    subparsers.add_parser("dmg", help="create an unsigned preview DMG")

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
        elif args.command == "build":
            build(layout, manifest, jobs=args.jobs)
        elif args.command == "package":
            package(layout, manifest)
        elif args.command == "dmg":
            create_dmg(layout, manifest)
            write_checksums(layout)
        elif args.command == "all":
            checkout(layout, manifest, clean=args.clean)
            build(layout, manifest, jobs=args.jobs)
            package(layout, manifest)
            create_dmg(layout, manifest)
            write_checksums(layout)
        return 0
    except PublisherError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
