# gui-qml macOS preview publisher

This repository builds an Apple-silicon macOS preview of Bitcoin Core App from
the depends-based gui-qml work in
[bitcoin-core/gui-qml#752](https://github.com/bitcoin-core/gui-qml/pull/752).
The preview is packaged as `Bitcoin-QML.app`, defaults to signet, and reports
the pinned gui-qml commit as its development version.

The source and submodule commits are pinned in `config/release.toml`. Two small
distribution patches are checked in under `patches/`; see `patches/README.md`
for their exact scope.

The build/package commands and Developer ID signing setup are documented below
as the publisher implementation is introduced.

