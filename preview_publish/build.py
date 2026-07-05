from __future__ import annotations

import os
import platform
import re
from pathlib import Path

from .commands import output, require_tool, run
from .config import Manifest
from .errors import PublisherError
from .layout import Layout


def depends_host(source: Path) -> str:
    depends = source / "bitcoin" / "depends"
    guessed = output([depends / "config.guess"], cwd=depends)
    return output([depends / "config.sub", guessed], cwd=depends)


def configure_command(layout: Layout, manifest: Manifest, host: str) -> list[str]:
    toolchain = layout.source / "bitcoin" / "depends" / host / "toolchain.cmake"
    return [
        require_tool("cmake"),
        "-S",
        str(layout.source),
        "-B",
        str(layout.cmake_build),
        "-G",
        "Ninja",
        f"-DCMAKE_BUILD_TYPE={manifest.build.build_type}",
        f"-DCMAKE_TOOLCHAIN_FILE={toolchain}",
        "-DBUILD_APP_TESTS=OFF",
        "-DBUILD_GUI=ON",
        "-DENABLE_WALLET=ON",
        "-DENABLE_IPC=OFF",
        f"-DGUI_QML_BUILD_VERSION={manifest.build.display_version}",
    ]


def linked_libraries(binary: Path) -> tuple[str, ...]:
    lines = output([require_tool("otool"), "-L", binary]).splitlines()[1:]
    return tuple(line.strip().split(" (", 1)[0] for line in lines if line.strip())


def _contains_any(binary: Path, needles: tuple[bytes, ...]) -> bool:
    overlap = max(map(len, needles)) - 1
    tail = b""
    with binary.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            block = tail + chunk
            if any(needle in block for needle in needles):
                return True
            tail = block[-overlap:]
    return False


def _contains_display_version(binary: Path, version: str) -> bool:
    return _contains_any(binary, (version.encode("ascii"), version.encode("utf-16le")))


def validate_binary(binary: Path, manifest: Manifest) -> None:
    if not binary.is_file():
        raise PublisherError(f"Build did not produce {binary}")
    architectures = output([require_tool("lipo"), "-archs", binary]).split()
    if architectures != [manifest.build.architecture]:
        raise PublisherError(
            f"Expected a thin {manifest.build.architecture} executable, got: "
            f"{' '.join(architectures)}"
        )
    unexpected = [
        library
        for library in linked_libraries(binary)
        if not library.startswith(("/System/Library/", "/usr/lib/"))
    ]
    if unexpected:
        raise PublisherError(
            "Static depends build contains undeployed libraries: " + ", ".join(unexpected)
        )
    load_commands = output([require_tool("otool"), "-l", binary])
    minimum_versions = re.findall(r"^\s+minos\s+([0-9.]+)$", load_commands, re.MULTILINE)
    if minimum_versions != [manifest.build.minimum_macos]:
        raise PublisherError(
            f"Expected macOS {manifest.build.minimum_macos} deployment target, got: "
            f"{', '.join(minimum_versions) or 'none'}"
        )
    if not _contains_display_version(binary, manifest.build.display_version):
        raise PublisherError(
            f"Executable does not contain source version {manifest.build.display_version}"
        )
    if not _contains_any(binary, (b"default: signet",)):
        raise PublisherError("Executable does not contain the signet-default build marker")


def build(layout: Layout, manifest: Manifest, *, jobs: int | None = None) -> Path:
    if platform.system() != "Darwin":
        raise PublisherError("The native preview build must run on macOS")
    for tool in ("bison", "cmake", "gmake", "ninja", "pkgconf", "lipo", "otool"):
        require_tool(tool)
    bison_version = output([require_tool("bison"), "--version"]).splitlines()[0]
    match = re.search(r"\b([0-9]+)\.", bison_version)
    if match is None or int(match.group(1)) < 3:
        raise PublisherError(
            f"Bison 3 or newer is required; found {bison_version}. "
            "Put $(brew --prefix bison)/bin first in PATH."
        )
    parallel = jobs or os.cpu_count() or 1
    host = depends_host(layout.source)
    depends = layout.source / "bitcoin" / "depends"
    run(
        [
            require_tool("gmake"),
            f"-j{parallel}",
            f"HOST={host}",
            "DEBUG=",
            "LOG=1",
        ],
        cwd=depends,
    )
    toolchain = depends / host / "toolchain.cmake"
    if not toolchain.is_file():
        raise PublisherError(f"Depends toolchain was not created: {toolchain}")
    run(configure_command(layout, manifest, host))
    run(
        [
            require_tool("cmake"),
            "--build",
            layout.cmake_build,
            "--target",
            manifest.build.target,
            "--parallel",
            str(parallel),
        ]
    )
    binary = layout.executable(manifest)
    validate_binary(binary, manifest)
    print(f"Built {binary}")
    return binary
