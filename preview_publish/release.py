from __future__ import annotations

import json
import mimetypes
import os
import re
import secrets
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import Manifest
from .errors import PublisherError
from .layout import Layout
from .package import _sha256_file
from .signing import verify_finalized_dmg


@dataclass(frozen=True)
class GitHubReleaseConfig:
    token: str = field(repr=False)
    repository: str
    commit: str
    api_url: str

    @classmethod
    def from_environment(cls) -> "GitHubReleaseConfig":
        token = os.environ.pop("GITHUB_TOKEN", "")
        repository = os.environ.get("GITHUB_REPOSITORY", "")
        commit = os.environ.get("GITHUB_SHA", "")
        api_url = os.environ.get("GITHUB_API_URL", "https://api.github.com")
        if not token:
            raise PublisherError("GITHUB_TOKEN is required to publish the nightly release")
        if not re.fullmatch(r"[^/]+/[^/]+", repository):
            raise PublisherError("GITHUB_REPOSITORY must have owner/name form")
        if not re.fullmatch(r"[0-9a-f]{40}", commit):
            raise PublisherError("GITHUB_SHA must be a full lowercase Git SHA-1")
        return cls(token, repository, commit, api_url.rstrip("/"))


class GitHubClient:
    def __init__(self, config: GitHubReleaseConfig):
        self.config = config

    def request(
        self,
        method: str,
        url: str,
        *,
        payload: dict[str, Any] | None = None,
        data: bytes | None = None,
        content_type: str = "application/json",
        allow_status: tuple[int, ...] = (),
    ) -> tuple[int, Any]:
        body = data
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=body,
            method=method,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {self.config.token}",
                "Content-Type": content_type,
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "gui-qml-preview-publish",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                response_data = response.read()
                status = response.status
        except urllib.error.HTTPError as error:
            response_data = error.read()
            status = error.code
            if status not in allow_status:
                message = response_data.decode("utf-8", errors="replace")
                raise PublisherError(
                    f"GitHub API {method} failed with HTTP {status}: {message}"
                ) from error
        except (urllib.error.URLError, TimeoutError) as error:
            raise PublisherError(f"GitHub API {method} request failed: {error}") from error
        if not response_data:
            return status, None
        try:
            return status, json.loads(response_data)
        except json.JSONDecodeError:
            return status, response_data.decode("utf-8", errors="replace")

    def api(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, Any] | None = None,
        allow_status: tuple[int, ...] = (),
    ) -> tuple[int, Any]:
        return self.request(
            method,
            f"{self.config.api_url}{path}",
            payload=payload,
            allow_status=allow_status,
        )


def _release_body(manifest: Manifest) -> str:
    generated = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    return "\n".join(
        [
            "Automated macOS and Linux previews.",
            "",
            f"- source ref: {manifest.source.reference}",
            f"- source commit: `{manifest.source.commit}`",
            f"- Bitcoin Core commit: `{manifest.source.bitcoin_commit}`",
            "- default network: signet",
            f"- macOS architecture: {manifest.build.architecture}",
            f"- minimum macOS: {manifest.build.minimum_macos}",
            f"- Linux architecture: {manifest.linux.architecture}",
            "- Linux format: raw unsigned depends-built executable",
            f"- generated: {generated}",
            "",
            "This is experimental preview software. Do not use it with mainnet funds.",
        ]
    )


def _upload_asset(
    client: GitHubClient, upload_url: str, asset: Path, upload_name: str
) -> dict[str, Any]:
    base_url = upload_url.split("{", 1)[0]
    url = f"{base_url}?{urllib.parse.urlencode({'name': upload_name})}"
    content_type = mimetypes.guess_type(asset.name)[0] or "application/octet-stream"
    status, response = client.request(
        "POST",
        url,
        data=asset.read_bytes(),
        content_type=content_type,
    )
    if status != 201 or not isinstance(response, dict):
        raise PublisherError(f"GitHub did not accept staged release asset {asset.name}")
    return _verified_asset(response, asset, upload_name)


def _verified_asset(
    response: dict[str, Any], asset: Path, expected_name: str
) -> dict[str, Any]:
    expected_digest = f"sha256:{_sha256_file(asset)}"
    expected_size = asset.stat().st_size
    if (
        response.get("name") != expected_name
        or response.get("state") != "uploaded"
        or response.get("size") != expected_size
        or response.get("digest") != expected_digest
        or not isinstance(response.get("id"), int)
    ):
        raise PublisherError(
            f"GitHub staged asset verification failed for {asset.name}: {response}"
        )
    return response


def _release_response(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise PublisherError("GitHub release API returned an unexpected response")
    return value


def _assert_mutable_nightly(release: dict[str, Any]) -> None:
    if release.get("immutable") is True:
        raise PublisherError(
            "The nightly release is immutable and cannot be updated. Rolling nightlies "
            "require GitHub release immutability to be disabled before first publication; "
            "after an immutable nightly exists, disable the setting and choose a new tag."
        )


def _release_assets(layout: Layout, manifest: Manifest) -> tuple[Path, Path, Path]:
    dmg = layout.dmg(manifest)
    linux_binary = layout.linux_binary(manifest)
    checksums = layout.artifacts / "SHA256SUMS"
    for asset in (dmg, linux_binary, checksums):
        if not asset.is_file():
            raise PublisherError(f"Release asset is missing: {asset}")
    expected = "".join(
        f"{_sha256_file(asset)}  {asset.name}\n"
        for asset in (dmg, linux_binary)
    )
    try:
        actual = checksums.read_text(encoding="utf-8")
    except OSError as error:
        raise PublisherError(f"Cannot read release checksums: {error}") from error
    if actual != expected:
        raise PublisherError("SHA256SUMS does not exactly match the release artifacts")
    return dmg, linux_binary, checksums


def _list_assets(
    client: GitHubClient, repository_path: str, release_id: int
) -> list[dict[str, Any]]:
    status, response = client.api(
        "GET",
        f"{repository_path}/releases/{release_id}/assets?per_page=100",
    )
    if status != 200 or not isinstance(response, list):
        raise PublisherError("GitHub returned an invalid release asset list")
    return [asset for asset in response if isinstance(asset, dict)]


def _delete_asset(client: GitHubClient, repository_path: str, asset: dict[str, Any]) -> None:
    asset_id = asset.get("id")
    if not isinstance(asset_id, int):
        raise PublisherError("GitHub release asset did not contain a numeric id")
    status, _ = client.api("DELETE", f"{repository_path}/releases/assets/{asset_id}")
    if status != 204:
        raise PublisherError(f"GitHub did not delete release asset {asset_id}")


def _cleanup_staged_assets(
    client: GitHubClient, repository_path: str, release_id: int
) -> None:
    for asset in _list_assets(client, repository_path, release_id):
        name = asset.get("name")
        if isinstance(name, str) and name.startswith("gui-qml-upload-"):
            _delete_asset(client, repository_path, asset)


def _upsert_tag(
    client: GitHubClient,
    repository_path: str,
    tag: str,
    commit: str,
) -> None:
    status, _ = client.api(
        "GET",
        f"{repository_path}/git/ref/tags/{tag}",
        allow_status=(404,),
    )
    if status == 404:
        create_status, _ = client.api(
            "POST",
            f"{repository_path}/git/refs",
            payload={"ref": f"refs/tags/{tag}", "sha": commit},
        )
        if create_status != 201:
            raise PublisherError("GitHub did not create the nightly tag")
    else:
        update_status, _ = client.api(
            "PATCH",
            f"{repository_path}/git/refs/tags/{tag}",
            payload={"sha": commit, "force": True},
        )
        if update_status != 200:
            raise PublisherError("GitHub did not update the nightly tag")


def _find_or_create_release(
    client: GitHubClient,
    repository_path: str,
    tag: str,
    release_payload: dict[str, Any],
) -> tuple[dict[str, Any], bool]:
    status, release = client.api(
        "GET",
        f"{repository_path}/releases/tags/{tag}",
        allow_status=(404,),
    )
    if status == 200:
        return _release_response(release), True

    list_status, releases = client.api(
        "GET", f"{repository_path}/releases?per_page=100"
    )
    if list_status != 200 or not isinstance(releases, list):
        raise PublisherError("GitHub returned an invalid release list")
    matching = [
        item
        for item in releases
        if isinstance(item, dict) and item.get("tag_name") == tag
    ]
    if len(matching) > 1:
        raise PublisherError("Multiple nightly releases exist; manual cleanup is required")
    if matching:
        return matching[0], not bool(matching[0].get("draft"))

    draft_payload = {**release_payload, "draft": True}
    create_status, release = client.api(
        "POST", f"{repository_path}/releases", payload=draft_payload
    )
    if create_status != 201:
        raise PublisherError("GitHub did not create the draft nightly release")
    return _release_response(release), False


def publish_nightly(layout: Layout, manifest: Manifest) -> str:
    release_config = GitHubReleaseConfig.from_environment()
    assets = _release_assets(layout, manifest)
    verify_finalized_dmg(assets[0])
    client = GitHubClient(release_config)
    repository_path = f"/repos/{release_config.repository}"
    tag = "nightly"
    release_payload: dict[str, Any] = {
        "tag_name": tag,
        "target_commitish": release_config.commit,
        "name": "gui-qml nightly previews",
        "body": _release_body(manifest),
        "draft": False,
        "prerelease": True,
        "make_latest": "false",
    }
    release, was_published = _find_or_create_release(
        client, repository_path, tag, release_payload
    )
    _assert_mutable_nightly(release)
    release_id = release.get("id")
    if not isinstance(release_id, int):
        raise PublisherError("GitHub release response did not contain a numeric id")
    upload_url = release.get("upload_url")
    if not isinstance(upload_url, str) or not upload_url:
        raise PublisherError("GitHub release response did not contain upload_url")

    # Keep the currently published assets intact until all replacements have
    # uploaded and their GitHub-computed digests match the local files.
    _cleanup_staged_assets(client, repository_path, release_id)
    current_assets = _list_assets(client, repository_path, release_id)
    upload_token = secrets.token_hex(8)
    staged: list[tuple[Path, dict[str, Any]]] = []
    try:
        for asset in assets:
            upload_name = f"gui-qml-upload-{upload_token}-{asset.name}"
            staged.append(
                (asset, _upload_asset(client, upload_url, asset, upload_name))
            )
    except BaseException:
        _cleanup_staged_assets(client, repository_path, release_id)
        raise

    if was_published:
        try:
            hide_status, hidden_release = client.api(
                "PATCH",
                f"{repository_path}/releases/{release_id}",
                payload={"draft": True},
            )
            if hide_status != 200 or not isinstance(hidden_release, dict):
                raise PublisherError(
                    "GitHub did not hide the nightly release for asset swap"
                )
        except BaseException:
            _cleanup_staged_assets(client, repository_path, release_id)
            raise

    for asset, uploaded in staged:
        for existing in current_assets:
            if existing.get("name") == asset.name:
                _delete_asset(client, repository_path, existing)
        uploaded_id = uploaded["id"]
        rename_status, renamed = client.api(
            "PATCH",
            f"{repository_path}/releases/assets/{uploaded_id}",
            payload={"name": asset.name},
        )
        if rename_status != 200 or not isinstance(renamed, dict):
            raise PublisherError(f"Could not finalize GitHub release asset {asset.name}")
        _verified_asset(renamed, asset, asset.name)

    _upsert_tag(client, repository_path, tag, release_config.commit)
    update_status, release = client.api(
        "PATCH",
        f"{repository_path}/releases/{release_id}",
        payload=release_payload,
    )
    if update_status != 200:
        raise PublisherError("GitHub did not finalize the nightly release")
    release = _release_response(release)
    if release.get("immutable") is True:
        raise PublisherError(
            "GitHub published this nightly as immutable. The assets are live, but future "
            "updates cannot reuse the nightly tag; disable release immutability and choose "
            "a new rolling tag before the next run."
        )

    html_url = release.get("html_url", "")
    if not isinstance(html_url, str):
        html_url = ""
    print(f"Published nightly release: {html_url}")
    return html_url
