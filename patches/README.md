# Preview source patches

These patches are applied to the pinned `bitcoin-core/gui-qml:qt6` checkout
after the branch's own depends patch. The version patch targets gui-qml itself; the
network-default patch targets its `bitcoin` submodule:

1. `0001-use-gui-qml-commit-for-version.patch` adds a build-time override for the
   user-facing version. The publisher supplies the exact gui-qml hash without
   changing Bitcoin Core's P2P or release-version semantics.
2. `0002-default-to-signet.patch` changes only the no-network-selected fallback to
   signet. Explicit command-line and configuration network selections still win.

The release manifest pins the gui-qml commit, Bitcoin Core submodule commit,
and branch depends-patch digest. Updating `qt6` requires reviewing its new head
and then updating those pins deliberately.
