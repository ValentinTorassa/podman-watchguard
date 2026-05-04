# Podman Watchguard

Portable IoT security and infrastructure appliance for a Raspberry Pi Zero 2 W. It combines sensors, actuators and rootless Podman containers to protect and monitor a small home-network cabinet: thermal control, tamper detection, local alerts, VPN/firewall services and optional honeypot/PKI services.

The academic design document is available in:

- [Markdown](docs/proyecto.md)
- [PDF](docs/proyecto.pdf)
- [Test report](docs/test-report.md)

## Overview

Podman Watchguard turns a Raspberry Pi Zero 2 W into a self-managed embedded IoT appliance. A monitor container reads environmental and tamper sensors, drives actuators, and emits JSON telemetry. Supporting services are designed to run as rootless Podman containers, deployed through quadlet units and updated with a GitOps-style workflow.

```
Git Repo (this repo)
    |
    v
+--------------------------------------+
| Raspberry Pi Zero 2 W                |
|  +--------------------------------+  |
|  | GitOps Agent                   |  |
|  | Pulls repo, deploys quadlets   |  |
|  +-------------+------------------+  |
|                |                     |
|  +-------------+------------------+  |
|  |             v                  |  |
|  |  +-----------+ +-----------+   |  |
|  |  | Monitor   | | WireGuard |   |  |
|  |  | Sensors + | | + DNS     |   |  |
|  |  | Actuators | +-----------+   |  |
|  |  +-----------+ +-----------+   |  |
|  |  +-----------+ | step-ca   |   |  |
|  |  | Cowrie    | | PKI       |   |  |
|  |  | Honeypot  | +-----------+   |  |
|  |  +-----------+ Podman Pod      |  |
|  +--------------------------------+  |
+--------------------------------------+
```

## Services

### IoT Monitor

Reads a DHT22 temperature/humidity sensor, an INA219 power sensor and a magnetic reed switch for tamper detection. Drives a 5 V fan, status LED, buzzer and an optional relay for controlled router/modem reset. The included container can run in simulation mode on any development machine.

### GitOps Agent

Lightweight agent concept that polls this Git repository for configuration changes and manages container lifecycle via Podman quadlets and `podman auto-update`.

### SSH Honeypot (Cowrie)

Emulates an SSH server to capture attacker credentials, commands, and behavior. Logs can be forwarded for analysis.

### WireGuard VPN + DNS Filtering

Portable VPN gateway with DNS-level ad/malware blocking. Acts as a travel firewall and can route traffic through the Pi for safer browsing on untrusted networks.

### PKI / Certificate Authority (step-ca)

Lightweight certificate authority for issuing and managing TLS certificates across homelab or dev infrastructure.

## Hardware

| Component | Spec |
|---|---|
| Board | Raspberry Pi Zero 2 W |
| RAM | 512 MB |
| Storage | microSD, 16 GB+ recommended |
| Network | Built-in WiFi plus USB Ethernet adapter recommended |
| Sensors | DHT22, INA219, magnetic reed switch |
| Actuators | 5 V fan, piezo buzzer, LED, optional relay module |

## Requirements

- Raspberry Pi OS Lite Bookworm
- Podman 4.x+
- Git
- Python 3.11+
- WireGuard kernel module for the VPN service

## Project Structure

```text
podman-watchguard/
├── config/              # Example runtime configuration
├── containers/
│   └── monitor/         # IoT monitor container
├── gitops-agent/        # Monitor application code
├── quadlets/            # Podman quadlet unit files
├── scripts/             # Setup, test and utility scripts
├── tests/               # Local repository checks
└── docs/                # Academic report in Markdown and PDF
```

## Local Validation

Run the repository checks:

```sh
make test
```

Generate the PDF from the Markdown report:

```sh
make docs
```

Build and run the monitor container with Podman:

```sh
make podman-build
make podman-smoke
```

On macOS, helper scripts are provided for the requested install/test/uninstall cycle:

```sh
./scripts/install-podman-macos.sh
./scripts/podman-smoke-test.sh
./scripts/uninstall-podman-macos.sh
```

## Runtime Simulation

The monitor can be tested without Raspberry Pi GPIO hardware:

```sh
python3 gitops-agent/watchguard_monitor.py --config config/watchguard.example.json --iterations 3
```

Expected output is newline-delimited JSON with sensor values and actuator decisions.

## Contributing

This project is built partly to explore and contribute improvements upstream to [Podman](https://github.com/containers/podman), particularly around:

- Quadlet support for ARM/embedded devices
- `podman auto-update` reliability on constrained hardware
- Rootless container networking on low-resource systems
- Secrets management for edge deployments

Contributions and ideas are welcome. Open an issue or submit a PR.

## License

GPL-3.0
