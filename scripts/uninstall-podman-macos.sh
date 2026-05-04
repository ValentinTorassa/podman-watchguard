#!/usr/bin/env bash
set -euo pipefail

if command -v podman >/dev/null 2>&1; then
  podman machine stop podman-machine-default || true
  podman machine rm -f podman-machine-default || true
fi

if command -v brew >/dev/null 2>&1; then
  brew uninstall podman || true
fi
