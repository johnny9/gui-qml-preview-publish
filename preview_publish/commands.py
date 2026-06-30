from __future__ import annotations

import os
import shlex
import shutil
import subprocess
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path

from .errors import PublisherError


def require_tool(name: str) -> str:
    path = shutil.which(name)
    if path is None:
        raise PublisherError(f"Required tool is not installed: {name}")
    return path


def _display_command(args: Sequence[str], redacted: Iterable[int]) -> str:
    hidden = set(redacted)
    shown = ["***" if index in hidden else value for index, value in enumerate(args)]
    return shlex.join(shown)


def run(
    args: Sequence[str | os.PathLike[str]],
    *,
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
    capture: bool = False,
    redacted: Iterable[int] = (),
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    command = [os.fspath(value) for value in args]
    print(f"+ {_display_command(command, redacted)}", flush=True)
    try:
        return subprocess.run(
            command,
            cwd=cwd,
            env=None if env is None else {**os.environ, **env},
            check=check,
            text=True,
            stdout=subprocess.PIPE if capture else None,
            stderr=subprocess.PIPE if capture else None,
        )
    except FileNotFoundError as error:
        raise PublisherError(f"Command is not installed: {command[0]}") from error
    except subprocess.CalledProcessError as error:
        if capture:
            if error.stdout:
                print(error.stdout, end="")
            if error.stderr:
                print(error.stderr, end="")
        raise PublisherError(
            f"Command failed with exit code {error.returncode}: "
            f"{_display_command(command, redacted)}"
        ) from error


def output(args: Sequence[str | os.PathLike[str]], *, cwd: Path | None = None) -> str:
    return run(args, cwd=cwd, capture=True).stdout.strip()
