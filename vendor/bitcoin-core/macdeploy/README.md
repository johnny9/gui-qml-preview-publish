# Vendored macdeployqtplus

`macdeployqtplus` and `LICENSE` are copied from
`bitcoin/bitcoin@6574cb40869b96b9ffc79c19dc8f4e467d60f321`, the Bitcoin Core
submodule pinned by gui-qml PR #752.

Local changes are intentionally narrow:

- read `CFBundleExecutable` from `Info.plist` instead of requiring
  `Bitcoin-Qt`;
- preserve the input app's bundle name and accept an explicit output directory;
- remove automatic ad-hoc signing so the publisher can apply a Developer ID
  signature only after deployment mutations are complete; and
- make optional ZIP creation use the actual app name.

The vendored files remain licensed under GPL-3.0-or-later. The preview build is
static, so Qt and QML are already embedded; this tool mainly preserves the
Bitcoin Core deployment path and verifies that no unexpected external
frameworks need copying.
