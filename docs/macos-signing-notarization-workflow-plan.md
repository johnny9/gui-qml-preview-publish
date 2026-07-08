# macOS Bitcoin QML signing and notarization workflow plan

## Status and authority

This is the local planning description and implementation contract for the
signing/notarization workflow PR. The workflow files and publisher now follow
this design; it does not configure credentials or alter a GitHub environment
by itself.

Use `macos-15` for every new GitHub Actions macOS job. GitHub has announced
that macOS 14 images begin deprecation on July 6, 2026 and become unsupported
on November 2, 2026. The first implementation must remain manual-only.

`macos-15` is currently GitHub's arm64 M1 runner label; `macos-15-intel` is
the separate Intel label. The pinned `qt6` source's own depends workflow uses
native `macos-15`, so this publisher intentionally retains its thin `arm64`
artifact rather than introducing an unvalidated x86_64 build.

The implementation uses `.github/workflows/macos-nightly-dmg.yml` for the
manual release workflow and `.github/workflows/test-macos-signing.yml` for its
credential smoke test:

- Notarization must use an App Store Connect API key (`.p8`).
- The six named repository secrets below are the initial source of truth.
- A protected `release-signing` environment is the target for release jobs.
- The secret-free macOS and Linux build jobs and isolated signing job remain a
  useful security boundary. Linux exports only its raw executable; the
  implementation should not introduce a second packaging system.

## Active depends-build source

The signing workflow targets the `qt6` branch of
`git@github.com:johnny9/BitcoinCoreAppDevelopment.git`. The corresponding
GitHub Actions clone URL is HTTPS, as the hosted runner deliberately has no
SSH deploy key for this source repository.

The active ref is pinned in `config/release.toml` at
`e5a893c991a3d7779b4d30c8765b76c623fa0b89`. It retains the verified Bitcoin
submodule commit, depends patch, and post-patch source/tree hashes; applying
the patches to this branch reproduced those pins before this workflow change.

## Goal

Build the Bitcoin QML macOS package, sign it with an Apple Developer ID,
notarize it, staple the ticket, and publish a signed DMG artifact or nightly
release:

```text
checkout repository
-> install build dependencies
-> build bitcoin-core-app
-> package Bitcoin QML.app
-> deploy or validate Qt/QML payload
-> sign the application with Developer ID Application
-> create the manifest-named DMG (currently Bitcoin-QML-signet-arm64.dmg)
-> sign the DMG
-> notarize the DMG
-> staple the DMG
-> upload artifact or update nightly release
```

For this repository, packaging is currently performed by `preview_publish` and
the reviewed arm64 build is statically linked. `macdeployqtplus` is already
used during packaging, so the final implementation must preserve its
inventory validation instead of assuming a generic dynamic-Qt `macdeployqt`
deployment step.

## Credentials and secret handling

The repository currently has these repository-level GitHub Actions secrets:

| Secret | Required value and use |
| --- | --- |
| `APPLE_CERTIFICATE_P12_BASE64` | Base64 of `DeveloperIDApplication.p12`, containing the Developer ID Application certificate and matching private key. Decode to `$RUNNER_TEMP/developer_id_application.p12` before import. |
| `APPLE_CERTIFICATE_PASSWORD` | Password used to export that P12; passed only to `security import`. |
| `APPLE_SIGNING_IDENTITY` | The complete `Developer ID Application: Name (TEAMID)` identity label, not a certificate fingerprint. |
| `APPLE_API_KEY_ID` | App Store Connect API key ID from `AuthKey_<key-id>.p8`. |
| `APPLE_API_ISSUER_ID` | App Store Connect issuer UUID. |
| `APPLE_API_PRIVATE_KEY_BASE64` | Base64 of `AuthKey_<key-id>.p8`; decode to `$RUNNER_TEMP/AuthKey_${APPLE_API_KEY_ID}.p8`. |

Do not add or require an Apple ID password, a 2FA code, the macOS login or
keychain password, raw P12/P8 files, or a preselected CI keychain password.
Generate the last one for each run:

```sh
KEYCHAIN_PASSWORD="$(openssl rand -base64 32)"
```

The first smoke test may read the repository secrets directly and must not set
an `environment:`. For a public repository, create `release-signing`, copy or
move the six secrets into it, and require an approval before a job can import
the P12, write the P8, sign, or call `notarytool`.

## Workflow changes

### 1. Signing-secret smoke test

Implemented in `.github/workflows/test-macos-signing.yml`.

- Trigger: `workflow_dispatch` only.
- Runner: `macos-15`.
- Permissions: `contents: read`.
- Initially no `environment:`; later use `environment: release-signing` when
  the secrets are moved.
- It must decode the P12 and P8, create and destroy a temporary keychain,
  import the identity, confirm the requested Developer ID identity is valid,
  and confirm `xcrun notarytool` is available.
- It must not build, sign a distributable, notarize, staple, or upload an
  artifact.

The success criterion is exactly one valid Developer ID Application identity
from `security find-identity -v -p codesigning`, with no credential contents
in the logs.

The keychain setup should follow this shape:

```sh
CERT_PATH="$RUNNER_TEMP/developer_id_application.p12"
KEYCHAIN_PATH="$RUNNER_TEMP/app-signing.keychain-db"
KEYCHAIN_PASSWORD="$(openssl rand -base64 32)"

printf '%s' "$APPLE_CERTIFICATE_P12_BASE64" | base64 -D > "$CERT_PATH"
security create-keychain -p "$KEYCHAIN_PASSWORD" "$KEYCHAIN_PATH"
security set-keychain-settings -lut 21600 "$KEYCHAIN_PATH"
security unlock-keychain -p "$KEYCHAIN_PASSWORD" "$KEYCHAIN_PATH"
security list-keychains -d user -s "$KEYCHAIN_PATH"
security default-keychain -s "$KEYCHAIN_PATH"
security import "$CERT_PATH" -k "$KEYCHAIN_PATH" \
  -P "$APPLE_CERTIFICATE_PASSWORD" -T /usr/bin/codesign -T /usr/bin/security
security set-key-partition-list -S apple-tool:,apple:,codesign: -s \
  -k "$KEYCHAIN_PASSWORD" "$KEYCHAIN_PATH"
security find-identity -v -p codesigning
```

Decode the API key with mode 600 and export only its path through
`GITHUB_ENV`:

```sh
APPLE_AUTHKEY_PATH="$RUNNER_TEMP/AuthKey_${APPLE_API_KEY_ID}.p8"
printf '%s' "$APPLE_API_PRIVATE_KEY_BASE64" | base64 -D > "$APPLE_AUTHKEY_PATH"
chmod 600 "$APPLE_AUTHKEY_PATH"
echo "APPLE_AUTHKEY_PATH=$APPLE_AUTHKEY_PATH" >> "$GITHUB_ENV"
```

### 2. Nightly preview release workflow

Implemented as `.github/workflows/macos-nightly-dmg.yml`; the old
`nightly-macos.yml` workflow is replaced, so two workflows cannot publish the
same rolling nightly release.

- Start with `workflow_dispatch` only; add the `0 5 * * *` schedule only after
  a manual signed/notarized run succeeds.
- Use `macos-15` for the macOS build and signing jobs and `ubuntu-24.04` for
  the x86-64 Linux depends build.
- Use `environment: release-signing` for the signing/notarization job.
- Keep both unsigned builds and protected signing in separate jobs/runners.
- Use `contents: read` for artifact-only execution and `contents: write` only
  when the workflow updates the nightly release.
- Never run a secrets-bearing job on `pull_request` or
  `pull_request_target`.

The publisher uses the three API-key secrets plus `APPLE_SIGNING_IDENTITY` and
does not retain an Apple ID/app-specific-password fallback. Its credential
model, tests, and README change together.

## Package, sign, notarize, and verify

The workflow should use stable variables for the source build, distribution
directory, application path, DMG path, QML directory, build directory,
release configuration, and deployment target. Confirm the deployment target
against the actual depends build before changing it: the current pinned build
targets macOS 14.0, whereas the proposed `11.0` only applies if all real
dependencies support it.

The final protected stage must:

1. Download the verified unsigned application and validated raw Linux
   executable from the two secret-free jobs.
2. Use the repository's existing packaging/deployment validation for
   `bitcoin-core-app` and the QML payload.
3. Sign the application using the exact `APPLE_SIGNING_IDENTITY`, hardened
   runtime, and timestamp; verify it with `codesign --verify --deep --strict
   --verbose=4` and `spctl -a -vvv -t exec`.
4. Create the manifest-named DMG (currently
   `Bitcoin-QML-signet-arm64.dmg`), sign it with a timestamp, and verify the
   signature.
5. Submit it with the decoded API key, capture the submission ID immediately,
   and then wait separately so the Actions log identifies the Apple submission
   before the potentially long wait:

   ```sh
   SUBMIT_JSON="$(xcrun notarytool submit "$DMG_PATH" \
     --key "$APPLE_AUTHKEY_PATH" \
     --key-id "$APPLE_API_KEY_ID" \
     --issuer "$APPLE_API_ISSUER_ID" \
     --no-progress \
     --output-format json)"

   SUBMISSION_ID="$(python3 -c \
     'import json,sys; print(json.load(sys.stdin)["id"])' <<< "$SUBMIT_JSON")"
   echo "Notarization submission id: $SUBMISSION_ID"

   xcrun notarytool wait "$SUBMISSION_ID" \
     --key "$APPLE_AUTHKEY_PATH" \
     --key-id "$APPLE_API_KEY_ID" \
     --issuer "$APPLE_API_ISSUER_ID" \
     --timeout 2h \
     --output-format json
   ```

   Preserve the submit and wait JSON receipts. If the wait does not return
   `Accepted`, fetch `xcrun notarytool log "$SUBMISSION_ID" ...` before failing.

6. Staple and validate the ticket, then perform the DMG Gatekeeper-style
   check:

   ```sh
   xcrun stapler staple "$DMG_PATH"
   xcrun stapler validate "$DMG_PATH"
   spctl -a -vvv -t open --context context:primary-signature "$DMG_PATH"
   ```

7. Update the nightly release atomically with only the finalized DMG, raw
   unsigned Linux executable, and their combined checksums. Never upload the
   decoded P12, decoded P8, or their base64 values.

## Implementation acceptance criteria

- A manual smoke test proves that all six secrets decode and the requested
  Developer ID identity imports successfully.
- The protected release job has no Apple ID/app-specific-password dependency.
- The released DMG is Developer ID signed, notarization is accepted, and the
  ticket is stapled and validated.
- The released Linux asset is the validated x86-64 depends-built executable,
  with no signing or packaging step.
- The workflow remains fail-closed: missing, malformed, or mismatched
  credentials cannot yield an unsigned/ad-hoc release.
- `actionlint`, the publisher's Python tests, and a workflow review pass.
- No Apple private material appears in logs, artifacts, the repository, or
  test fixtures.

## Operator checklist

Before running the protected workflow, confirm the local export and backup of
`DeveloperIDApplication.p12`, `AuthKey_<key-id>.p8`, and the credential
handoff notes. Confirm the precise signing label with:

```sh
security find-identity -v -p codesigning
```

Confirm the six secrets with `gh secret list`, use `macos-15`, and run the
smoke test before enabling the full nightly workflow. The older Big Sur
MacBook is for credential export and inspection only; build, signing,
notarization, and stapling run on GitHub's macOS runner.

## References

- [GitHub-hosted runners](https://docs.github.com/en/actions/concepts/runners/github-hosted-runners)
- [GitHub runner image releases](https://github.com/actions/runner-images/releases)
- [GitHub environments](https://docs.github.com/en/actions/reference/workflows-and-actions/deployments-and-environments)
- [Apple notarization guidance](https://developer.apple.com/documentation/security/notarizing-macos-software-before-distribution)
