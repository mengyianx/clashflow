#!/usr/bin/env bash
# Download the pinned mihomo (Kernel) release for the host platform into the
# local cache directory, so tests and CI can locate it without a system install.
# Tests look for the binary at $MIHOMO_BIN, then on PATH, then at the default
# path this script prints.
set -euo pipefail

MIHOMO_VERSION="${MIHOMO_VERSION:-v1.19.32}"
CACHE_DIR="${CACHE_DIR:-$HOME/.cache/clashflow/kernel-${MIHOMO_VERSION}}"

case "$(uname -m)" in
  x86_64) arch=amd64 ;;
  aarch64|arm64) arch=arm64 ;;
  armv7l) arch=armv7 ;;
  *) echo "fetch-kernel: unsupported machine $(uname -m)" >&2; exit 1 ;;
esac

mkdir -p "$CACHE_DIR"
url="https://github.com/MetaCubeX/mihomo/releases/download/${MIHOMO_VERSION}/mihomo-linux-${arch}-${MIHOMO_VERSION}.gz"
if [ ! -x "$CACHE_DIR/mihomo" ]; then
  curl -sSfL "$url" -o "$CACHE_DIR/mihomo.gz"
  gunzip -f "$CACHE_DIR/mihomo.gz"
  chmod +x "$CACHE_DIR/mihomo"
fi
echo "$CACHE_DIR/mihomo"
