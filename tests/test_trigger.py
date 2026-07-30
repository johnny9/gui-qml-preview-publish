import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from preview_publish.config import load_manifest
from preview_publish.errors import PublisherError
from preview_publish.trigger import (
    GitHubReadConfig,
    SourceStatus,
    _parse_source_ref,
    published_source_commit,
    source_status,
    write_github_output,
)


class SourceTriggerTest(unittest.TestCase):
    def test_parses_exact_remote_branch_result(self) -> None:
        commit = "a" * 40
        self.assertEqual(
            _parse_source_ref(f"{commit}\trefs/heads/qt6\n", "refs/heads/qt6"),
            commit,
        )
        with self.assertRaises(PublisherError):
            _parse_source_ref(
                f"{commit}\trefs/heads/main\n", "refs/heads/qt6"
            )

    def test_release_body_requires_one_exact_source_commit(self) -> None:
        commit = "b" * 40
        body = f"Preview\n\n- source commit: `{commit}`\n"
        self.assertEqual(published_source_commit(body), commit)
        with self.assertRaises(PublisherError):
            published_source_commit("Preview without provenance")
        with self.assertRaises(PublisherError):
            published_source_commit(body + body)

    def test_source_status_only_publishes_a_new_head(self) -> None:
        manifest = load_manifest()
        commit = "c" * 40
        body = f"- source commit: `{commit}`"
        config = GitHubReadConfig(
            token="token",
            repository="owner/repository",
            api_url="https://api.github.test",
        )
        with patch(
            "preview_publish.trigger.resolve_source_head", return_value=commit
        ), patch("preview_publish.trigger.latest_release_body", return_value=body):
            status = source_status(manifest, config)
        self.assertEqual(status, SourceStatus(commit=commit, should_publish=False))

        old_body = f"- source commit: `{'a' * 40}`"
        with patch(
            "preview_publish.trigger.resolve_source_head", return_value=commit
        ), patch(
            "preview_publish.trigger.latest_release_body", return_value=old_body
        ):
            status = source_status(manifest, config)
        self.assertEqual(status, SourceStatus(commit=commit, should_publish=True))

        with patch(
            "preview_publish.trigger.resolve_source_head", return_value=commit
        ), patch("preview_publish.trigger.latest_release_body", return_value=None):
            status = source_status(manifest, config)
        self.assertEqual(status, SourceStatus(commit=commit, should_publish=True))

    def test_writes_only_safe_github_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "github-output"
            write_github_output(
                output, SourceStatus(commit="d" * 40, should_publish=True)
            )
            self.assertEqual(
                output.read_text(encoding="utf-8"),
                f"source_commit={'d' * 40}\nshould_publish=true\n",
            )


if __name__ == "__main__":
    unittest.main()
