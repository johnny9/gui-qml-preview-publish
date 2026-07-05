# Bitcoin Core App macOS preview publisher

This repository builds an Apple-silicon macOS preview of Bitcoin Core App from
the `qt6` branch of
[johnny9/BitcoinCoreAppDevelopment](https://github.com/johnny9/BitcoinCoreAppDevelopment/tree/qt6).
The preview is packaged as `Bitcoin-QML.app`, defaults to signet, and reports
the pinned source commit as its development version.

`config/release.toml` pins the source commit, Bitcoin Core submodule commit,
depends patch digest, and patched-tree digests. The GitHub Actions clone URL is
HTTPS even though the source is also available as
`git@github.com:johnny9/BitcoinCoreAppDevelopment.git`: hosted runners do not
receive an SSH deploy key for this source repository. The small distribution
patches are checked in under `patches/`; see `patches/README.md` for scope.

The signing/notarization design and its operating constraints are in
[`docs/macos-signing-notarization-workflow-plan.md`](docs/macos-signing-notarization-workflow-plan.md).

## Unsigned build

The pinned Qt 6 source produces a native, thin `arm64` executable with a
macOS 14.0 deployment target. Run the build on Apple silicon with Python 3.11
or newer:

```sh
brew install bison cmake make ninja pkgconf python
export PATH="$(brew --prefix bison)/bin:$PATH"
PYTHON="$(brew --prefix)/bin/python3"
"$PYTHON" -m preview_publish --work-dir build all --clean
```

The `all` command performs four independently runnable stages:

```sh
"$PYTHON" -m preview_publish --work-dir build checkout --clean
"$PYTHON" -m preview_publish --work-dir build build
"$PYTHON" -m preview_publish --work-dir build package
"$PYTHON" -m preview_publish --work-dir build dmg
```

Outputs stay under the chosen work directory:

- `build/deploy/Bitcoin-QML.app`
- `build/artifacts/Bitcoin-QML-signet-arm64.dmg`
- `build/artifacts/SHA256SUMS`

The checkout stage fetches `refs/heads/qt6` and refuses to continue unless it
resolves to the pinned commit. It verifies the Bitcoin Core submodule and
depends patch before applying the local distribution patches. A reused work
directory with unreviewed source changes is rejected.

## macOS packaging

Bitcoin Core's `macdeployqtplus` is vendored under
`vendor/bitcoin-core/macdeploy` with its GPL license and provenance. The Qt 6
depends build is static, so the publisher validates that the executable has
only Apple system-library dependencies, invokes `macdeployqtplus` without
dynamic plugins, and rejects unexpected frameworks, helpers, links, or other
bundle payloads. The DMG contains `Bitcoin-QML.app` and an Applications link.

## Developer ID and notarization setup

No Apple credential material is checked in. The workflows use this six-secret
model:

| Secret | Value |
| --- | --- |
| `APPLE_CERTIFICATE_P12_BASE64` | Base64 of a password-protected Developer ID Application P12 that includes its private key |
| `APPLE_CERTIFICATE_PASSWORD` | Password used to export the P12 |
| `APPLE_SIGNING_IDENTITY` | Exact `Developer ID Application: Name (TEAMID)` identity label |
| `APPLE_API_KEY_ID` | App Store Connect API key ID |
| `APPLE_API_ISSUER_ID` | App Store Connect issuer UUID |
| `APPLE_API_PRIVATE_KEY_BASE64` | Base64 of `AuthKey_<key-id>.p8` |

Export the Developer ID Application identity from Keychain Access only after
confirming the certificate expands to show its matching private key. A
downloaded `.cer` alone cannot sign. Create the App Store Connect API key and
retain its `.p8` privately; it authorizes `notarytool` without an Apple ID or
app-specific password in CI.

For the initial smoke test, the six secrets may remain repository secrets.
Create a protected GitHub environment named `release-signing`, copy or move
the same six secrets into it, and require approval before enabling the release
workflow. Only the release job references that environment.

Encode credential files without printing their contents:

```sh
base64 -i DeveloperIDApplication.p12 | tr -d '\n' |
  gh secret set APPLE_CERTIFICATE_P12_BASE64 \
    --repo johnny9/gui-qml-preview-publish

base64 -i AuthKey_ABCDEFGHIJ.p8 | tr -d '\n' |
  gh secret set APPLE_API_PRIVATE_KEY_BASE64 \
    --repo johnny9/gui-qml-preview-publish
```

Set the remaining repository secrets with `gh secret set NAME`, or set all
six in `release-signing` with `gh secret set NAME --env release-signing`.
Never upload a decoded P12/P8, print their base64 values, or add them to this
repository.

The local preflight imports the P12 into a random temporary keychain, checks
that it contains the exact requested identity, decodes the P8, and confirms
that `notarytool` is present:

```sh
"$PYTHON" -m preview_publish credentials
```

Missing, malformed, or mismatched credentials are fatal; publishing never
falls back to an ad-hoc or unsigned signature.

## Workflows

`test-macos-signing.yml` is a manual `macos-15` smoke test. It reads the six
repository secrets, decodes the P12 and P8 into `$RUNNER_TEMP`, imports the
Developer ID identity into a temporary keychain, checks the exact identity,
and confirms `notarytool` exists. It does not build, sign a distributable,
notarize, staple, or upload an artifact.

`macos-nightly-dmg.yml` is also manual-only until a signed run succeeds:

1. A secret-free `macos-15` job builds the pinned source with depends and
   uploads the validated unsigned app.
2. A fresh `macos-15` job in `release-signing` downloads that app, applies a
   timestamped Developer ID signature with hardened runtime, creates and
   signs the DMG, submits it to `notarytool` with the API key, staples it, and
   performs Gatekeeper-style validation.
3. The signed DMG and post-staple `SHA256SUMS` replace the assets on the
   `nightly` prerelease.

The protected job has `contents: write`; the build job has only
`contents: read`. Neither workflow is triggered by `pull_request` or
`pull_request_target`. Once a manual run has passed, add the scheduled trigger
described in the workflow plan. Keep GitHub release immutability off for a
rolling `nightly` release, or change this publisher to create a unique release
tag per build.
