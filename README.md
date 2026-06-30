# gui-qml macOS preview publisher

This repository builds an Apple-silicon macOS preview of Bitcoin Core App from
the depends-based gui-qml work in
[bitcoin-core/gui-qml#752](https://github.com/bitcoin-core/gui-qml/pull/752).
The preview is packaged as `Bitcoin-QML.app`, defaults to signet, and reports
the pinned gui-qml commit as its development version.

The source and submodule commits are pinned in `config/release.toml`. Two small
distribution patches are checked in under `patches/`; see `patches/README.md`
for their exact scope.

## Unsigned build

The pinned PR produces a native, thin `arm64` executable with a macOS 14.0
deployment target. Run the build on Apple silicon with Python 3.11 or newer:

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

Outputs are written only under the selected work directory:

- `build/deploy/Bitcoin-QML.app`
- `build/artifacts/Bitcoin-QML-signet-arm64.dmg`
- `build/artifacts/SHA256SUMS`

The checkout stage fetches `refs/pull/752/head` and refuses to proceed unless
it resolves to the reviewed commit in the manifest. It likewise verifies the
Bitcoin Core submodule commit and PR depends-patch SHA-256 before applying any
patches. The complete patched diffs are also pinned, so a reused work directory
with unrelated source changes is rejected.

## macOS deployment

Bitcoin Core's `macdeployqtplus` is vendored under
`vendor/bitcoin-core/macdeploy` with its GPL license and exact provenance. The
local adaptation reads the executable and bundle name from the QML-specific
`Info.plist`, accepts an isolated output directory, and leaves signing to the
publisher.

PR #752 links Qt 6, QML, and the other depends libraries statically. The
publisher therefore validates that the executable has only Apple system
library dependencies and invokes `macdeployqtplus` without dynamic plugins.
The DMG is created separately with `hdiutil` and contains `Bitcoin-QML.app`
plus an Applications-folder link.

## Developer ID and notarization setup

No Apple credentials are checked in, and the initial commits have not been
Developer ID signed or notarized. Before publishing, create a protected GitHub
environment named `macos-release` and add these environment secrets:

| Secret | Value |
| --- | --- |
| `APPLE_CERTIFICATE_P12_BASE64` | Base64 of a password-protected P12 containing the Developer ID Application certificate **and its private key** |
| `APPLE_CERTIFICATE_PASSWORD` | Password used when exporting that P12 |
| `APPLE_ID` | Apple Account email used for notarization |
| `APPLE_APP_SPECIFIC_PASSWORD` | App-specific password created for that Apple Account; never the normal account password |
| `APPLE_TEAM_ID` | Ten-character Apple Developer team ID matching the certificate |

Bootstrap the Apple credentials first:

1. Use the Account Holder Apple Account for an active Apple Developer Program
   membership, and enable two-factor authentication.
2. On the signing Mac, open **Keychain Access → Certificate Assistant → Request
   a Certificate From a Certificate Authority**. Save the CSR to disk; its
   private key stays in that Mac's login keychain.
3. Following [Apple's Developer ID certificate guide](https://developer.apple.com/help/account/certificates/create-developer-id-certificates/),
   create a **Developer ID → Developer ID Application** certificate (not
   Developer ID Installer), upload the CSR, download the `.cer`, and install it
   on the same Mac.
4. In Keychain Access under **My Certificates**, confirm the certificate
   expands to show its private key, then export that identity as a
   password-protected P12.
5. At `account.apple.com`, create an
   [app-specific password](https://support.apple.com/en-us/102654) for
   `notarytool`. Find the ten-character Team ID in the Apple Developer
   membership details.

A downloaded `.cer` alone cannot sign the app because it does not contain the
private key. There is no separate notarization certificate: `notarytool` uses
the Apple ID, app-specific password, and Team ID, while code signing uses the
Developer ID Application identity in the P12.

Encode it without writing the value to the terminal:

```sh
base64 -i DeveloperIDApplication.p12 | tr -d '\n' |
  gh secret set APPLE_CERTIFICATE_P12_BASE64 \
    --repo johnny9/gui-qml-preview-publish \
    --env macos-release
```

Set the other four secrets in GitHub under **Settings → Environments →
macos-release**, or with `gh secret set NAME --env macos-release`. Protecting
the environment with a required reviewer is useful during the first manual
validation because it controls a Developer ID private key. Remove that review
gate before enabling unattended nightly runs, or approve each scheduled run
within the unsigned artifact's seven-day retention window.

The Python preflight can validate a locally exported P12 and the Apple notary
credentials before a full build. Supply the five variables above in the
environment, then run:

```sh
"$PYTHON" -m preview_publish credentials
```

The preflight and finalizer import the P12 into a random temporary keychain,
auto-select exactly one `Developer ID Application` identity matching the team
ID, validate a temporary `notarytool` profile, and delete the keychain. Missing
credentials are fatal; publishing never falls back to an ad-hoc or unsigned
signature.

## Nightly release workflow

`.github/workflows/nightly-macos.yml` keeps untrusted build execution away
from Apple credentials:

1. A secret-free `macos-15` runner builds the pinned source with depends,
   validates the arm64 Mach-O, and packages the unsigned app.
2. A fresh runner attached to the protected `macos-release` environment safely
   extracts and revalidates that app, applies a timestamped Developer ID
   signature with hardened runtime, creates and signs the DMG, waits for an
   `Accepted` notarization result, staples it, and performs Gatekeeper checks.
3. The finalized DMG and post-staple `SHA256SUMS` replace the assets on the
   `nightly` prerelease.

The static Qt 6.8.3 arm64 build contains one Mach-O and does not use hardened
runtime exception entitlements. The finalizer refuses unexpected frameworks,
plugins, helper executables, links, or special files instead of deep-signing
them implicitly.

First, run the workflow manually from the default branch and confirm that the
notary result is accepted and the DMG is stapled. Before that first run, leave
GitHub's **Settings → General → Releases → Enable release immutability** option
off: immutable assets and tags cannot support a rolling `nightly` release. If
an organization enforces [immutable releases](https://docs.github.com/en/code-security/concepts/supply-chain-security/immutable-releases),
change this publisher to use a unique tag for every build instead.

Scheduled runs are disabled until the repository variable
`MACOS_NIGHTLY_ENABLED` is set to `true`:

```sh
gh variable set MACOS_NIGHTLY_ENABLED \
  --repo johnny9/gui-qml-preview-publish \
  --body true
```

The schedule runs at 07:17 UTC. Leave the variable unset or set it to `false`
to stop scheduled publishing without removing credentials or editing the
workflow.
