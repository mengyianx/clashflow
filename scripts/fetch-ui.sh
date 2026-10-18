#!/usr/bin/env bash
# Download the pinned metacubexd (Runtime Dashboard) static release bundle and
# extract it into the local bundle directory, so tests and local runs can serve
# /ui/ without a system install. The app looks for the bundle at
# $CLASHFLOW_UI_DIST, then at the default path this script prints.
set -euo pipefail

METACUBEXD_VERSION="${METACUBEXD_VERSION:-v1.273.1}"
METACUBEXD_SHA256="${METACUBEXD_SHA256:-a178e00b67acabcda2dcef00afa90be6a7bb261e466a67dad58c8478d9553603}"
DEST="${DEST:-$(cd "$(dirname "$0")/.." && pwd)/metacubexd}"

if [ ! -f "$DEST/index.html" ]; then
  tmp="$(mktemp -d)"
  trap 'rm -rf "$tmp"' EXIT
  url="https://github.com/MetaCubeX/metacubexd/releases/download/${METACUBEXD_VERSION}/compressed-dist.tgz"
  curl -sSfL "$url" -o "$tmp/compressed-dist.tgz"
  echo "${METACUBEXD_SHA256}  $tmp/compressed-dist.tgz" | sha256sum -c - >/dev/null
  mkdir -p "$DEST"
  tar -xzf "$tmp/compressed-dist.tgz" -C "$DEST"
fi
echo "$DEST"
