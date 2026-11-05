#!/usr/bin/env bash
# Download the pinned zashboard (second Runtime Dashboard) static release and
# extract it into the local bundle directory, so tests and local runs can serve
# /zui/ without a system install. The app looks for the bundle at
# $CLASHFLOW_ZUI_DIST, then at the default path this script prints.
set -euo pipefail

ZASHBOARD_VERSION="${ZASHBOARD_VERSION:-v3.30.0}"
ZASHBOARD_SHA256="${ZASHBOARD_SHA256:-ec794e7c4daf4c839ecbc0a1292fdaf7bc7a6e3e4a4ce284db2b32379145c278}"
DEST="${DEST:-$(cd "$(dirname "$0")/.." && pwd)/zashboard}"

if [ ! -f "$DEST/index.html" ]; then
  tmp="$(mktemp -d)"
  trap 'rm -rf "$tmp"' EXIT
  url="https://github.com/Zephyruso/zashboard/releases/download/${ZASHBOARD_VERSION}/dist-no-fonts.zip"
  curl -sSfL "$url" -o "$tmp/zui.zip"
  echo "${ZASHBOARD_SHA256}  $tmp/zui.zip" | sha256sum -c - >/dev/null
  unzip -q "$tmp/zui.zip" -d "$tmp"
  mkdir -p "$DEST"
  cp -a "$tmp/dist/." "$DEST/"
fi
echo "$DEST"
