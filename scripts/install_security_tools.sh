#!/usr/bin/env bash
# Installs the pinned gitleaks + osv-scanner binaries (checksum-verified) into ~/.local/bin.
# Used by CI and by cloud threads (SessionStart hook). Locally: brew install gitleaks osv-scanner.
# semgrep and zizmor need no install: make runs them pinned through uvx.
set -euo pipefail

GITLEAKS_VERSION=8.30.1
OSV_SCANNER_VERSION=2.6.0
bin="${1:-$HOME/.local/bin}"
mkdir -p "$bin"

case "$(uname -s)-$(uname -m)" in
  Linux-x86_64)
    gl=linux_x64 gl_sha=551f6fc83ea457d62a0d98237cbad105af8d557003051f41f3e7ca7b3f2470eb
    osv=linux_amd64 osv_sha=ca69b3d3cd08f889a49dc0a383122f71cc528b83803671df5fd874d97485b108 ;;
  Linux-aarch64)
    gl=linux_arm64 gl_sha=e4a487ee7ccd7d3a7f7ec08657610aa3606637dab924210b3aee62570fb4b080
    osv=linux_arm64 osv_sha=2c71403eb443d05891c4f268c3ad771cf4f16e5443463fd7851ef8f454d3c7e4 ;;
  Darwin-arm64)
    gl=darwin_arm64 gl_sha=b40ab0ae55c505963e365f271a8d3846efbc170aa17f2607f13df610a9aeb6a5
    osv=darwin_arm64 osv_sha=98c460dcd37de25819babd757d04542045b6243113e209edcd4d89fedb0256b4 ;;
  Darwin-x86_64)
    gl=darwin_x64 gl_sha=dfe101a4db2255fc85120ac7f3d25e4342c3c20cf749f2c20a18081af1952709
    osv=darwin_amd64 osv_sha=60c5296637e977b28eeda5c7f13573e447659a632922737f94d11fa7e30ad6ca ;;
  *) echo "unsupported platform $(uname -s)-$(uname -m)" >&2; exit 1 ;;
esac

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

fetch() {
  curl -sSfL --retry 3 -o "$tmp/$2" "$1"
  echo "$3  $tmp/$2" | shasum -a 256 -c - >/dev/null || { echo "checksum mismatch for $2" >&2; exit 1; }
}

if ! "$bin/gitleaks" version 2>/dev/null | grep -q "$GITLEAKS_VERSION"; then
  fetch "https://github.com/gitleaks/gitleaks/releases/download/v$GITLEAKS_VERSION/gitleaks_${GITLEAKS_VERSION}_$gl.tar.gz" gitleaks.tar.gz "$gl_sha"
  tar -xzf "$tmp/gitleaks.tar.gz" -C "$tmp" gitleaks
  install -m 755 "$tmp/gitleaks" "$bin/gitleaks"
fi

if ! "$bin/osv-scanner" --version 2>/dev/null | grep -q "$OSV_SCANNER_VERSION"; then
  fetch "https://github.com/google/osv-scanner/releases/download/v$OSV_SCANNER_VERSION/osv-scanner_$osv" osv-scanner "$osv_sha"
  install -m 755 "$tmp/osv-scanner" "$bin/osv-scanner"
fi

echo "security tools in $bin: gitleaks $GITLEAKS_VERSION, osv-scanner $OSV_SCANNER_VERSION"
