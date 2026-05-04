#!/usr/bin/env bash
set -euo pipefail

podman --version
podman info >/dev/null
podman build -t podman-watchguard-monitor:local -f containers/monitor/Containerfile .
podman run --rm \
  -v ./config:/config:ro \
  podman-watchguard-monitor:local \
  --config /config/watchguard.example.json \
  --iterations 3
