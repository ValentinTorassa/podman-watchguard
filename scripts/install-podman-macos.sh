#!/usr/bin/env bash
set -euo pipefail

if ! command -v brew >/dev/null 2>&1; then
  echo "Homebrew is required to install Podman on macOS." >&2
  exit 1
fi

if ! command -v podman >/dev/null 2>&1; then
  brew install podman
fi

if ! podman machine list --format '{{.Name}}' | grep -qx 'podman-machine-default'; then
  podman machine init
fi

podman machine start || true
podman --version
