import hashlib
import os
import tempfile
import unittest
import urllib.parse
from pathlib import Path
from unittest.mock import patch

from preview_publish.config import load_manifest
from preview_publish.errors import PublisherError
from preview_publish.layout import Layout
from preview_publish.package import write_checksums
from preview_publish.release import (
    GitHubReleaseConfig,
    PreviewVersion,
    _publish_versioned_release,
    publish_nightly,
    publish_releases,
)


def write_release_assets(layout: Layout, manifest, dmg_contents: bytes) -> None:
    layout.artifacts.mkdir(parents=True)
    layout.dmg(manifest).write_bytes(dmg_contents)
    layout.linux_binary(manifest).write_bytes(b"linux executable")
    write_checksums(
        layout,
        (layout.dmg(manifest), layout.linux_binary(manifest)),
    )


def release_asset_names(layout: Layout, manifest) -> set[str]:
    return {
        layout.dmg(manifest).name,
        layout.linux_binary(manifest).name,
        "SHA256SUMS",
    }


class FakeGitHubClient:
    def __init__(self, _config):
        self.uploads = []
        self.api_calls = []
        self.assets = {}
        self.next_asset_id = 100
        self.release_exists = False
        self.release_draft = True
        self.release_immutable = False
        self.tag_exists = False

    def _release(self):
        return {
            "id": 42,
            "tag_name": "nightly",
            "draft": self.release_draft,
            "immutable": self.release_immutable,
            "upload_url": "https://uploads.github.test/assets{?name,label}",
            "html_url": "https://github.test/release/nightly",
        }

    def api(self, method, path, *, payload=None, allow_status=()):
        self.api_calls.append((method, path, payload, allow_status))
        if method == "GET" and path.endswith("/releases/tags/nightly"):
            if self.release_exists and not self.release_draft:
                return 200, self._release()
            return 404, {"message": "Not Found"}
        if method == "GET" and path.endswith("/releases?per_page=100"):
            return 200, [self._release()] if self.release_exists else []
        if method == "GET" and "/assets?" in path:
            return 200, list(self.assets.values())
        if method == "GET" and "/git/ref/tags/nightly" in path:
            if self.tag_exists:
                return 200, {"ref": "refs/tags/nightly"}
            return 404, {"message": "Not Found"}
        if method == "POST" and path.endswith("/releases"):
            self.release_exists = True
            self.release_draft = bool(payload["draft"])
            return 201, self._release()
        if method == "PATCH" and "/releases/assets/" in path:
            asset_id = int(path.rsplit("/", 1)[1])
            asset = self.assets[asset_id]
            asset = {**asset, "name": payload["name"]}
            self.assets[asset_id] = asset
            return 200, asset
        if method == "DELETE" and "/releases/assets/" in path:
            self.assets.pop(int(path.rsplit("/", 1)[1]), None)
            return 204, None
        if method == "PATCH" and path.endswith("/releases/42"):
            if "draft" in payload:
                self.release_draft = bool(payload["draft"])
            return 200, self._release()
        if method == "POST" and path.endswith("/git/refs"):
            self.tag_exists = True
            return 201, {"ref": payload["ref"]}
        if method == "PATCH" and "/git/refs/tags/nightly" in path:
            self.tag_exists = True
            return 200, {"ref": "refs/tags/nightly"}
        return 204, None

    def request(self, method, url, **kwargs):
        self.uploads.append((method, url, kwargs))
        name = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)["name"][0]
        data = kwargs["data"]
        asset = {
            "id": self.next_asset_id,
            "name": name,
            "state": "uploaded",
            "size": len(data),
            "digest": f"sha256:{hashlib.sha256(data).hexdigest()}",
        }
        self.assets[self.next_asset_id] = asset
        self.next_asset_id += 1
        return 201, asset


class ExistingReleaseClient(FakeGitHubClient):
    def __init__(self, config):
        super().__init__(config)
        self.release_exists = True
        self.release_draft = False
        self.tag_exists = True
        self.assets = {
            1: {"id": 1, "name": "Bitcoin-QML-signet-arm64.dmg"},
            2: {
                "id": 2,
                "name": "bitcoin-core-app-signet-x86_64-linux-gnu",
            },
            3: {"id": 3, "name": "SHA256SUMS"},
        }


class FailingSecondUploadClient(ExistingReleaseClient):
    def request(self, method, url, **kwargs):
        if len(self.uploads) == 1:
            raise PublisherError("simulated upload failure")
        return super().request(method, url, **kwargs)


class FailingSecondRenameClient(ExistingReleaseClient):
    def __init__(self, config):
        super().__init__(config)
        self.rename_count = 0
        self.fail_rename = True

    def api(self, method, path, *, payload=None, allow_status=()):
        if method == "PATCH" and "/releases/assets/" in path:
            self.rename_count += 1
            if self.fail_rename and self.rename_count == 2:
                self.api_calls.append((method, path, payload, allow_status))
                return 502, {"message": "simulated rename failure"}
        return super().api(method, path, payload=payload, allow_status=allow_status)


class FailingHideClient(ExistingReleaseClient):
    def api(self, method, path, *, payload=None, allow_status=()):
        if (
            method == "PATCH"
            and path.endswith("/releases/42")
            and payload == {"draft": True}
        ):
            self.api_calls.append((method, path, payload, allow_status))
            return 502, {"message": "simulated hide failure"}
        return super().api(method, path, payload=payload, allow_status=allow_status)


class FailingFreshRenameClient(FakeGitHubClient):
    def __init__(self, config):
        super().__init__(config)
        self.rename_count = 0
        self.fail_rename = True

    def api(self, method, path, *, payload=None, allow_status=()):
        if method == "PATCH" and "/releases/assets/" in path:
            self.rename_count += 1
            if self.fail_rename and self.rename_count == 2:
                self.api_calls.append((method, path, payload, allow_status))
                return 502, {"message": "simulated rename failure"}
        return super().api(method, path, payload=payload, allow_status=allow_status)


class VersionReleaseClient:
    def __init__(self, _commit: str):
        self.api_calls = []
        self.uploads = []
        self.assets = {}
        self.next_asset_id = 200
        self.release_exists = False
        self.release_draft = True
        self.tag_commit = None

    def _release(self):
        return {
            "id": 84,
            "tag_name": "v0.0.123",
            "draft": self.release_draft,
            "immutable": not self.release_draft,
            "upload_url": "https://uploads.github.test/version{?name,label}",
            "html_url": "https://github.test/release/v0.0.123",
        }

    def api(self, method, path, *, payload=None, allow_status=()):
        self.api_calls.append((method, path, payload, allow_status))
        if method == "GET" and path.endswith("/releases/tags/v0.0.123"):
            if self.release_exists and not self.release_draft:
                return 200, self._release()
            return 404, {"message": "Not Found"}
        if method == "GET" and path.endswith("/releases?per_page=100"):
            return 200, [self._release()] if self.release_exists else []
        if method == "POST" and path.endswith("/releases"):
            self.release_exists = True
            self.release_draft = bool(payload["draft"])
            return 201, self._release()
        if method == "GET" and path.endswith("/releases/84/assets?per_page=100"):
            return 200, list(self.assets.values())
        if method == "DELETE" and "/releases/assets/" in path:
            self.assets.pop(int(path.rsplit("/", 1)[1]), None)
            return 204, None
        if method == "GET" and path.endswith("/git/ref/tags/v0.0.123"):
            if self.tag_commit is None:
                return 404, {"message": "Not Found"}
            return 200, {"object": {"sha": self.tag_commit}}
        if method == "POST" and path.endswith("/git/refs"):
            self.tag_commit = payload["sha"]
            return 201, {"ref": payload["ref"], "object": {"sha": self.tag_commit}}
        if method == "PATCH" and path.endswith("/releases/84"):
            self.release_draft = bool(payload["draft"])
            return 200, self._release()
        return 204, None

    def request(self, method, url, **kwargs):
        self.uploads.append((method, url, kwargs))
        name = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)["name"][0]
        data = kwargs["data"]
        asset = {
            "id": self.next_asset_id,
            "name": name,
            "state": "uploaded",
            "size": len(data),
            "digest": f"sha256:{hashlib.sha256(data).hexdigest()}",
        }
        self.assets[self.next_asset_id] = asset
        self.next_asset_id += 1
        return 201, asset


class ReleaseTest(unittest.TestCase):
    def test_versioned_release_is_stable_and_idempotent(self) -> None:
        manifest = load_manifest()
        release_config = GitHubReleaseConfig(
            token="token",
            repository="owner/repository",
            commit="a" * 40,
            api_url="https://api.github.test",
        )
        with tempfile.TemporaryDirectory() as directory:
            layout = Layout.create(Path(directory), manifest)
            write_release_assets(layout, manifest, b"dmg")
            assets = (
                layout.dmg(manifest),
                layout.linux_binary(manifest),
                layout.artifacts / "SHA256SUMS",
            )
            client = VersionReleaseClient(release_config.commit)
            with patch.dict(os.environ, {"GITHUB_RUN_NUMBER": "123"}, clear=False):
                preview_version = PreviewVersion.from_environment()
            first_url = _publish_versioned_release(
                client,
                "/repos/owner/repository",
                release_config,
                assets,
                manifest,
                preview_version,
            )
            second_url = _publish_versioned_release(
                client,
                "/repos/owner/repository",
                release_config,
                assets,
                manifest,
                preview_version,
            )

        self.assertEqual(first_url, "https://github.test/release/v0.0.123")
        self.assertEqual(second_url, first_url)
        self.assertEqual(preview_version.value, "0.0.123")
        self.assertEqual(preview_version.tag, "v0.0.123")
        self.assertEqual(len(client.uploads), 3)
        self.assertEqual(client.tag_commit, release_config.commit)
        self.assertFalse(client.release_draft)
        create_release = next(
            call
            for call in client.api_calls
            if call[0] == "POST" and call[1].endswith("/releases")
        )
        self.assertIn("macOS and Linux", create_release[2]["name"])

    def test_versioned_release_refuses_moved_tag(self) -> None:
        manifest = load_manifest()
        release_config = GitHubReleaseConfig(
            token="token",
            repository="owner/repository",
            commit="a" * 40,
            api_url="https://api.github.test",
        )
        with tempfile.TemporaryDirectory() as directory:
            layout = Layout.create(Path(directory), manifest)
            write_release_assets(layout, manifest, b"dmg")
            assets = (
                layout.dmg(manifest),
                layout.linux_binary(manifest),
                layout.artifacts / "SHA256SUMS",
            )
            client = VersionReleaseClient(release_config.commit)
            with patch.dict(os.environ, {"GITHUB_RUN_NUMBER": "123"}, clear=False):
                version = PreviewVersion.from_environment()
            _publish_versioned_release(
                client,
                "/repos/owner/repository",
                release_config,
                assets,
                manifest,
                version,
            )
            client.tag_commit = "b" * 40
            with self.assertRaisesRegex(PublisherError, "different publisher commit"):
                _publish_versioned_release(
                    client,
                    "/repos/owner/repository",
                    release_config,
                    assets,
                    manifest,
                    version,
                )

        self.assertEqual(len(client.uploads), 3)

    def test_release_command_publishes_version_then_latest(self) -> None:
        manifest = load_manifest()
        environment = {
            "GITHUB_TOKEN": "token",
            "GITHUB_REPOSITORY": "owner/repository",
            "GITHUB_SHA": "a" * 40,
            "GITHUB_RUN_NUMBER": "123",
        }
        events = []
        with tempfile.TemporaryDirectory() as directory:
            layout = Layout.create(Path(directory), manifest)
            write_release_assets(layout, manifest, b"dmg")
            with patch.dict(os.environ, environment, clear=False), patch(
                "preview_publish.release.verify_finalized_dmg"
            ), patch("preview_publish.release.GitHubClient"), patch(
                "preview_publish.release._publish_versioned_release",
                side_effect=lambda *_args: events.append("versioned")
                or "https://github.test/release/v0.0.123",
            ) as versioned, patch(
                "preview_publish.release._publish_latest_preview",
                side_effect=lambda *_args: events.append("latest")
                or "https://github.test/release/nightly",
            ) as latest:
                urls = publish_releases(layout, manifest)

        self.assertEqual(
            urls,
            (
                "https://github.test/release/v0.0.123",
                "https://github.test/release/nightly",
            ),
        )
        versioned.assert_called_once()
        latest.assert_called_once()
        self.assertEqual(events, ["versioned", "latest"])

    def test_new_nightly_uploads_both_binaries_and_checksum(self) -> None:
        manifest = load_manifest()
        environment = {
            "GITHUB_TOKEN": "token",
            "GITHUB_REPOSITORY": "owner/repository",
            "GITHUB_SHA": "a" * 40,
            "GITHUB_API_URL": "https://api.github.test",
        }
        with tempfile.TemporaryDirectory() as directory:
            layout = Layout.create(Path(directory), manifest)
            write_release_assets(layout, manifest, b"dmg")
            client = FakeGitHubClient(None)
            with patch.dict(os.environ, environment, clear=False), patch(
                "preview_publish.release.verify_finalized_dmg"
            ), patch("preview_publish.release.GitHubClient", return_value=client):
                url = publish_nightly(layout, manifest)
                self.assertNotIn("GITHUB_TOKEN", os.environ)

        self.assertEqual(url, "https://github.test/release/nightly")
        self.assertEqual(len(client.uploads), 3)
        self.assertTrue(all(call[0] == "POST" for call in client.uploads))
        self.assertEqual(
            set(asset["name"] for asset in client.assets.values()),
            release_asset_names(layout, manifest),
        )
        self.assertTrue(
            any(
                method == "POST" and path.endswith("/git/refs")
                for method, path, _payload, _allowed in client.api_calls
            )
        )
        self.assertFalse(client.release_draft)

    def test_missing_assets_make_no_api_calls(self) -> None:
        manifest = load_manifest()
        environment = {
            "GITHUB_TOKEN": "token",
            "GITHUB_REPOSITORY": "owner/repository",
            "GITHUB_SHA": "a" * 40,
        }
        with tempfile.TemporaryDirectory() as directory:
            layout = Layout.create(Path(directory), manifest)
            with patch.dict(os.environ, environment, clear=False), patch(
                "preview_publish.release.GitHubClient"
            ) as client_class:
                with self.assertRaises(PublisherError):
                    publish_nightly(layout, manifest)

        client_class.assert_not_called()

    def test_existing_assets_survive_until_all_uploads_finish(self) -> None:
        manifest = load_manifest()
        environment = {
            "GITHUB_TOKEN": "token",
            "GITHUB_REPOSITORY": "owner/repository",
            "GITHUB_SHA": "b" * 40,
        }
        with tempfile.TemporaryDirectory() as directory:
            layout = Layout.create(Path(directory), manifest)
            write_release_assets(layout, manifest, b"replacement")
            client = ExistingReleaseClient(None)
            with patch.dict(os.environ, environment, clear=False), patch(
                "preview_publish.release.verify_finalized_dmg"
            ), patch("preview_publish.release.GitHubClient", return_value=client):
                publish_nightly(layout, manifest)

        self.assertNotIn(1, client.assets)
        self.assertNotIn(2, client.assets)
        self.assertNotIn(3, client.assets)
        self.assertEqual(
            {asset["name"] for asset in client.assets.values()},
            release_asset_names(layout, manifest),
        )
        self.assertTrue(
            any(
                method == "PATCH" and path.endswith("/git/refs/tags/nightly")
                for method, path, _payload, _allowed in client.api_calls
            )
        )
        self.assertFalse(client.release_draft)

    def test_upload_failure_keeps_old_assets_and_cleans_staging(self) -> None:
        manifest = load_manifest()
        environment = {
            "GITHUB_TOKEN": "token",
            "GITHUB_REPOSITORY": "owner/repository",
            "GITHUB_SHA": "c" * 40,
        }
        with tempfile.TemporaryDirectory() as directory:
            layout = Layout.create(Path(directory), manifest)
            write_release_assets(layout, manifest, b"replacement")
            client = FailingSecondUploadClient(None)
            with patch.dict(os.environ, environment, clear=False), patch(
                "preview_publish.release.verify_finalized_dmg"
            ), patch("preview_publish.release.GitHubClient", return_value=client):
                with self.assertRaises(PublisherError):
                    publish_nightly(layout, manifest)

        self.assertEqual(
            {asset["name"] for asset in client.assets.values()},
            release_asset_names(layout, manifest),
        )
        self.assertFalse(client.release_draft)

    def test_hide_failure_keeps_old_assets_and_cleans_staging(self) -> None:
        manifest = load_manifest()
        environment = {
            "GITHUB_TOKEN": "token",
            "GITHUB_REPOSITORY": "owner/repository",
            "GITHUB_SHA": "c" * 40,
        }
        with tempfile.TemporaryDirectory() as directory:
            layout = Layout.create(Path(directory), manifest)
            write_release_assets(layout, manifest, b"replacement")
            client = FailingHideClient(None)
            with patch.dict(os.environ, environment, clear=False), patch(
                "preview_publish.release.verify_finalized_dmg"
            ), patch("preview_publish.release.GitHubClient", return_value=client):
                with self.assertRaises(PublisherError):
                    publish_nightly(layout, manifest)

        self.assertEqual(set(client.assets), {1, 2, 3})
        self.assertTrue(
            all(
                not asset["name"].startswith("gui-qml-upload-")
                for asset in client.assets.values()
            )
        )

    def test_mid_swap_failure_hides_release_and_next_run_recovers(self) -> None:
        manifest = load_manifest()
        environment = {
            "GITHUB_TOKEN": "token",
            "GITHUB_REPOSITORY": "owner/repository",
            "GITHUB_SHA": "d" * 40,
        }
        with tempfile.TemporaryDirectory() as directory:
            layout = Layout.create(Path(directory), manifest)
            write_release_assets(layout, manifest, b"replacement")
            client = FailingSecondRenameClient(None)
            with patch("preview_publish.release.verify_finalized_dmg"), patch(
                "preview_publish.release.GitHubClient", return_value=client
            ):
                with patch.dict(os.environ, environment, clear=False):
                    with self.assertRaises(PublisherError):
                        publish_nightly(layout, manifest)

                self.assertTrue(client.release_draft)

                client.fail_rename = False
                with patch.dict(os.environ, environment, clear=False):
                    publish_nightly(layout, manifest)

        self.assertFalse(client.release_draft)
        self.assertEqual(
            {asset["name"] for asset in client.assets.values()},
            release_asset_names(layout, manifest),
        )
        self.assertEqual(
            sum(
                method == "POST" and path.endswith("/releases")
                for method, path, _payload, _allowed in client.api_calls
            ),
            0,
        )

    def test_first_publish_failure_reuses_draft_on_retry(self) -> None:
        manifest = load_manifest()
        environment = {
            "GITHUB_TOKEN": "token",
            "GITHUB_REPOSITORY": "owner/repository",
            "GITHUB_SHA": "e" * 40,
        }
        with tempfile.TemporaryDirectory() as directory:
            layout = Layout.create(Path(directory), manifest)
            write_release_assets(layout, manifest, b"first publication")
            client = FailingFreshRenameClient(None)
            with patch("preview_publish.release.verify_finalized_dmg"), patch(
                "preview_publish.release.GitHubClient", return_value=client
            ):
                with patch.dict(os.environ, environment, clear=False):
                    with self.assertRaises(PublisherError):
                        publish_nightly(layout, manifest)

                self.assertTrue(client.release_draft)

                client.fail_rename = False
                with patch.dict(os.environ, environment, clear=False):
                    publish_nightly(layout, manifest)

        self.assertFalse(client.release_draft)
        self.assertTrue(client.tag_exists)
        self.assertEqual(
            {asset["name"] for asset in client.assets.values()},
            release_asset_names(layout, manifest),
        )
        self.assertEqual(
            sum(
                method == "POST" and path.endswith("/releases")
                for method, path, _payload, _allowed in client.api_calls
            ),
            1,
        )

    def test_immutable_nightly_fails_before_asset_mutation(self) -> None:
        manifest = load_manifest()
        environment = {
            "GITHUB_TOKEN": "token",
            "GITHUB_REPOSITORY": "owner/repository",
            "GITHUB_SHA": "f" * 40,
        }
        with tempfile.TemporaryDirectory() as directory:
            layout = Layout.create(Path(directory), manifest)
            write_release_assets(layout, manifest, b"replacement")
            client = ExistingReleaseClient(None)
            client.release_immutable = True
            with patch.dict(os.environ, environment, clear=False), patch(
                "preview_publish.release.verify_finalized_dmg"
            ), patch("preview_publish.release.GitHubClient", return_value=client):
                with self.assertRaisesRegex(PublisherError, "immutable"):
                    publish_nightly(layout, manifest)

        self.assertEqual(client.uploads, [])
        self.assertEqual(set(client.assets), {1, 2, 3})

    def test_unfinalized_dmg_make_no_api_calls(self) -> None:
        manifest = load_manifest()
        environment = {
            "GITHUB_TOKEN": "token",
            "GITHUB_REPOSITORY": "owner/repository",
            "GITHUB_SHA": "a" * 40,
        }
        with tempfile.TemporaryDirectory() as directory:
            layout = Layout.create(Path(directory), manifest)
            write_release_assets(layout, manifest, b"unsigned")
            with patch.dict(os.environ, environment, clear=False), patch(
                "preview_publish.release.verify_finalized_dmg",
                side_effect=PublisherError("not finalized"),
            ), patch("preview_publish.release.GitHubClient") as client_class:
                with self.assertRaises(PublisherError):
                    publish_nightly(layout, manifest)

        client_class.assert_not_called()


if __name__ == "__main__":
    unittest.main()
