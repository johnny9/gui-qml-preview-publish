from __future__ import annotations

import os
import platform
import re
import shutil
import stat
from pathlib import Path

from .commands import output, require_tool, run
from .config import Manifest
from .errors import PublisherError
from .layout import Layout

STATIC_QML_PLUGIN_CLASSES = (
    "QtQuick2Plugin",
    "QtQuickControls2Plugin",
    "QtQuickLayoutsPlugin",
    "QmlSettingsPlugin",
)

# Bitcoin Core's Linux depends recipes deliberately build fontconfig and
# freetype shared; everything else here is part of the standard C/C++ runtime.
LINUX_ALLOWED_DYNAMIC_LIBRARIES = frozenset(
    {
        "libc.so.6",
        "libdl.so.2",
        "libfontconfig.so.1",
        "libfreetype.so.6",
        "libgcc_s.so.1",
        "libm.so.6",
        "libpthread.so.0",
        "librt.so.1",
        "libstdc++.so.6",
        "libutil.so.1",
        "ld-linux-x86-64.so.2",
    }
)


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


def _missing_static_qml_plugins(symbols: str) -> tuple[str, ...]:
    return tuple(
        plugin
        for plugin in STATIC_QML_PLUGIN_CLASSES
        if f"qt_plugin_instance_{plugin}" not in symbols
    )


def _validate_embedded_preview(
    binary: Path, manifest: Manifest, platform_plugin: str
) -> None:
    symbols = output([require_tool("nm"), binary])
    missing_plugins = _missing_static_qml_plugins(symbols)
    if f"qt_plugin_instance_{platform_plugin}" not in symbols:
        missing_plugins = (*missing_plugins, platform_plugin)
    if missing_plugins:
        raise PublisherError(
            "Static depends build is missing required plugins: "
            + ", ".join(missing_plugins)
        )
    if not _contains_display_version(binary, manifest.build.display_version):
        raise PublisherError(
            f"Executable does not contain source version {manifest.build.display_version}"
        )
    if not _contains_any(binary, (b"default: signet",)):
        raise PublisherError("Executable does not contain the signet-default build marker")


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
    _validate_embedded_preview(binary, manifest, "QCocoaIntegrationPlugin")


def _linux_needed_libraries(dynamic_section: str) -> tuple[str, ...]:
    return tuple(
        re.findall(
            r"\(NEEDED\).*?Shared library: \[([^]]+)]",
            dynamic_section,
        )
    )


def validate_linux_binary(binary: Path, manifest: Manifest) -> None:
    if not binary.is_file():
        raise PublisherError(f"Build did not produce {binary}")
    header = output([require_tool("readelf"), "--file-header", binary])
    expected_header_fields = {
        "Class": "ELF64",
        "Machine": "Advanced Micro Devices X86-64",
    }
    for field, expected in expected_header_fields.items():
        match = re.search(rf"^\s*{re.escape(field)}:\s*(.+)$", header, re.MULTILINE)
        actual = match.group(1).strip() if match else "missing"
        if actual != expected:
            raise PublisherError(
                f"Expected Linux {field.lower()} {expected}, got: {actual}"
            )
    dynamic_section = output([require_tool("readelf"), "--dynamic", binary])
    unexpected = tuple(
        library
        for library in _linux_needed_libraries(dynamic_section)
        if library not in LINUX_ALLOWED_DYNAMIC_LIBRARIES
    )
    if unexpected:
        raise PublisherError(
            "Linux depends build has unexpected dynamic libraries: "
            + ", ".join(unexpected)
        )
    _validate_embedded_preview(binary, manifest, "QXcbIntegrationPlugin")


def export_linux_binary(layout: Layout, manifest: Manifest) -> Path:
    if platform.system() != "Linux":
        raise PublisherError("The Linux executable must be exported on Linux")
    binary = layout.executable(manifest)
    validate_linux_binary(binary, manifest)
    destination = layout.linux_binary(manifest)
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(binary, destination)
    destination.chmod(
        destination.stat().st_mode
        | stat.S_IXUSR
        | stat.S_IXGRP
        | stat.S_IXOTH
    )
    print(f"Exported Linux executable {destination}")
    return destination


def build(layout: Layout, manifest: Manifest, *, jobs: int | None = None) -> Path:
    system = platform.system()
    if system not in {"Darwin", "Linux"}:
        raise PublisherError("The native preview build must run on macOS or Linux")
    common_tools = (
        "bison",
        "cmake",
        "ninja",
        "pkgconf",
        "nm",
    )
    platform_tools = (
        ("gmake", "lipo", "otool")
        if system == "Darwin"
        else ("make", "readelf")
    )
    for tool in (*common_tools, *platform_tools):
        require_tool(tool)
    bison_version = output([require_tool("bison"), "--version"]).splitlines()[0]
    match = re.search(r"\b([0-9]+)\.", bison_version)
    if match is None or int(match.group(1)) < 3:
        hint = (
            "Put $(brew --prefix bison)/bin first in PATH."
            if system == "Darwin"
            else "Install Bison 3 or newer before building."
        )
        raise PublisherError(
            f"Bison 3 or newer is required; found {bison_version}. {hint}"
        )
    parallel = jobs or os.cpu_count() or 1
    host = depends_host(layout.source) if system == "Darwin" else manifest.linux.host
    depends = layout.source / "bitcoin" / "depends"
    run(
        [
            require_tool("gmake" if system == "Darwin" else "make"),
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
    if system == "Darwin":
        validate_binary(binary, manifest)
    else:
        validate_linux_binary(binary, manifest)
    print(f"Built {binary}")
    return binary
