# Podman Watchguard

Portable security & infrastructure appliance on Raspberry Pi Zero — GitOps-managed Podman containers running a SSH honeypot, WireGuard VPN/firewall, and a lightweight PKI (step-ca). Built to explore embedded container orchestration and contribute upstream to Podman.

## Overview

Podman Watchguard turns a Raspberry Pi Zero into a self-managed security appliance. All services run as rootless Podman containers, deployed and updated via a GitOps agent that pulls configuration from this repository.

```
Git Repo (this repo)
    │
    ▼
┌──────────────────────────────────────┐
│  Raspberry Pi Zero                   │
│  ┌────────────────────────────────┐  │
│  │  GitOps Agent                  │  │
│  │  Polls repo, deploys containers│  │
│  └────────────┬───────────────────┘  │
│               │                      │
│  ┌────────────┼───────────────────┐  │
│  │            ▼                   │  │
│  │  ┌───────────┐ ┌───────────┐  │  │
│  │  │ WireGuard │ │  Cowrie   │  │  │
│  │  │ + DNS     │ │ Honeypot  │  │  │
│  │  └───────────┘ └───────────┘  │  │
│  │  ┌───────────┐                │  │
│  │  │  step-ca  │  Podman Pod    │  │
│  │  │  (PKI)    │                │  │
│  │  └───────────┘                │  │
│  └────────────────────────────────┘  │
└──────────────────────────────────────┘
```

## Services

### GitOps Agent
Lightweight agent that polls this Git repository for configuration changes and manages the lifecycle of all other containers via Podman quadlets and `podman auto-update`.

### SSH Honeypot (Cowrie)
Emulates an SSH server to capture attacker credentials, commands, and behavior. Logs are forwarded for analysis.

### WireGuard VPN + DNS Filtering
Portable VPN gateway with DNS-level ad/malware blocking. Acts as a travel firewall — route traffic through the Pi for secure browsing on untrusted networks.

### PKI / Certificate Authority (step-ca)
Lightweight certificate authority for issuing and managing TLS certificates across homelab or dev infrastructure.

## Hardware

| Component | Spec |
|---|---|
| Board | Raspberry Pi Zero W / Zero 2 W |
| RAM | 512 MB |
| Storage | microSD (16GB+ recommended) |
| Network | Built-in WiFi + USB Ethernet adapter (recommended) |

## Requirements

- Raspberry Pi OS Lite (Bookworm)
- Podman 4.x+
- Git
- WireGuard kernel module

## Project Structure

```
podman-watchguard/
├── gitops-agent/        # GitOps polling agent
├── containers/
│   ├── honeypot/        # Cowrie honeypot container config
│   ├── vpn/             # WireGuard + DNS filtering config
│   └── pki/             # step-ca configuration
├── quadlets/            # Podman quadlet unit files
├── scripts/             # Setup and utility scripts
└── docs/                # Documentation
```

## Getting Started

> Work in progress — setup instructions coming soon.

## Contributing

This project is built partly to explore and contribute improvements upstream to [Podman](https://github.com/containers/podman), particularly around:

- Quadlet support for ARM/embedded devices
- `podman auto-update` reliability on constrained hardware
- Rootless container networking on low-resource systems
- Secrets management for edge deployments

Contributions and ideas are welcome — open an issue or submit a PR.

## License

MIT
