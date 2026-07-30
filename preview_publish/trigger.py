from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .commands import output, require_tool
from .config import Manifest, SHA1_RE
from .errors import PublisherError


SOURCE_COMMIT_LINE_RE = re.compile(
    r"^- source commit: `([0-9a-f]{40})`$", re.MULTILINE
)


@dataclass(frozen=True)
class SourceStatus:
    commit: str
    should_publish: bool


@dataclass(frozen=True)
class GitHubReadConfig:
    token: str
    repository: str
    api_url: str

    @classmethod
    def from_environment(cls) -> "GitHubReadConfig":
        token = os.environ.get("GITHUB_TOKEN", "")
        repository = os.environ.get("GITHUB_REPOSITORY", "")
        api_url = os.environ.get("GITHUB_API_URL", "https://api.github.com")
        if not token:
            raise PublisherError("GITHUB_TOKEN is required to inspect Latest Preview")
        if not re.fullmatch(r"[^/]+/[^/]+", repository):
            raise PublisherError("GITHUB_REPOSITORY must have owner/name form")
        if not api_url.startswith("https://"):
            raise PublisherError("GITHUB_API_URL must use HTTPS")
        return cls(token=token, repository=repository, api_url=api_url.rstrip("/"))


def _parse_source_ref(output_text: str, fetch_ref: str) -> str:
    rows = [line.split("\t", 1) for line in output_text.splitlines() if line]
    if len(rows) != 1 or len(rows[0]) != 2 or rows[0][1] != fetch_ref:
        raise PublisherError(
            f"Source ref lookup returned an unexpected result for {fetch_ref}"
        )
    commit = rows[0][0]
    if not SHA1_RE.fullmatch(commit):
        raise PublisherError(f"Source ref {fetch_ref} did not resolve to a full SHA-1")
    return commit


def resolve_source_head(manifest: Manifest) -> str:
    result = output(
        [
            require_tool("git"),
            "ls-remote",
            "--exit-code",
            "--refs",
            manifest.source.repository,
            manifest.source.fetch_ref,
        ]
    )
    return _parse_source_ref(result, manifest.source.fetch_ref)


def _parse_release_body(response: Any) -> str:
    if not isinstance(response, dict) or not isinstance(response.get("body"), str):
        raise PublisherError("Latest Preview returned an unexpected GitHub response")
    return response["body"]


def latest_release_body(config: GitHubReadConfig, tag: str = "latest") -> str | None:
    repository = urllib.parse.quote(config.repository, safe="/")
    release_tag = urllib.parse.quote(tag, safe="")
    request = urllib.request.Request(
        f"{config.api_url}/repos/{repository}/releases/tags/{release_tag}",
        method="GET",
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {config.token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "gui-qml-preview-publish",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            raw_body = response.read()
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return None
        message = error.read().decode("utf-8", errors="replace")
        raise PublisherError(
            f"GitHub API GET failed with HTTP {error.code}: {message}"
        ) from error
    except (urllib.error.URLError, TimeoutError) as error:
        raise PublisherError(f"GitHub API GET request failed: {error}") from error
    try:
        return _parse_release_body(json.loads(raw_body))
    except json.JSONDecodeError as error:
        raise PublisherError("Latest Preview returned invalid JSON") from error


def published_source_commit(release_body: str) -> str:
    matches = SOURCE_COMMIT_LINE_RE.findall(release_body)
    if len(matches) != 1:
        raise PublisherError(
            "Latest Preview must contain exactly one full source commit line"
        )
    return matches[0]


def source_status(
    manifest: Manifest, config: GitHubReadConfig | None = None
) -> SourceStatus:
    commit = resolve_source_head(manifest)
    release_body = latest_release_body(config or GitHubReadConfig.from_environment())
    published = None if release_body is None else published_source_commit(release_body)
    return SourceStatus(commit=commit, should_publish=published != commit)


def write_github_output(path: Path, status: SourceStatus) -> None:
    try:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(f"source_commit={status.commit}\n")
            handle.write(
                f"should_publish={'true' if status.should_publish else 'false'}\n"
            )
    except OSError as error:
        raise PublisherError(
            f"Cannot write GitHub output file {path}: {error}"
        ) from error
