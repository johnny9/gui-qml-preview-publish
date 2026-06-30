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

Developer ID signing, notarization, and nightly release setup are documented
in the release workflow section below.
