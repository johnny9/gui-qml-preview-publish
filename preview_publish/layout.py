from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .config import Manifest


@dataclass(frozen=True)
class Layout:
    work: Path
    source: Path
    cmake_build: Path
    stage: Path
    deploy: Path
    artifacts: Path

    @classmethod
    def create(cls, work: Path, manifest: Manifest) -> "Layout":
        resolved = work.resolve()
        source = resolved / "source"
        return cls(
            work=resolved,
            source=source,
            cmake_build=source / manifest.build.build_directory,
            stage=resolved / "stage",
            deploy=resolved / "deploy",
            artifacts=resolved / "artifacts",
        )

    def staged_app(self, manifest: Manifest) -> Path:
        return self.stage / f"{manifest.application.bundle_name}.app"

    def deployed_app(self, manifest: Manifest) -> Path:
        return self.deploy / f"{manifest.application.bundle_name}.app"

    def executable(self, manifest: Manifest) -> Path:
        return self.cmake_build / "bin" / manifest.application.executable

    def dmg(self, manifest: Manifest) -> Path:
        return self.artifacts / f"{manifest.application.artifact_basename}.dmg"
