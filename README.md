# Podman Watchguard

Portable IoT security and infrastructure appliance for a Raspberry Pi Zero 2 W. It combines sensors, actuators and rootless Podman containers to protect and monitor a small home-network cabinet: thermal control, tamper detection, local alerts, a WireGuard VPN gateway and optional honeypot/PKI services.

The academic design document is available in:

- [Markdown](docs/proyecto.md)
- [PDF](docs/proyecto.pdf)
- [Test report](docs/test-report.md)

## Status

What the repository contains today, and what it does not:

| Piece | State |
|---|---|
| Monitor control logic and JSON telemetry | Works; runs in simulation mode on any machine |
| Monitor sensor reads on the Pi (DHT22, INA219, reed switch) | Implemented on standard Linux interfaces and unit-tested with fakes; **not yet run on real hardware** |
| Monitor driving the fan, LED, buzzer and relay | **Not implemented**: actuator states are decided and reported, no GPIO output is written |
| Quadlets: pod, monitor, WireGuard, Cowrie, step-ca | Written and checked with `quadlet -dryrun` (Podman 5.4.2); **not yet deployed on a Pi** |
| Image updates | `podman auto-update` through the quadlets' `AutoUpdate=` keys |
| GitOps agent that pulls this repo and applies quadlet changes | **Does not exist**; quadlet changes are copied to the Pi by hand |
| DNS filtering for VPN peers | **Not included**; peers get a plain forwarding resolver |

## Overview

The monitor reads environmental and tamper sensors, decides actuator states and emits JSON telemetry. The network services run as rootless Podman containers, described by quadlet units in `quadlets/` and kept up to date by `podman auto-update`.

```
Git repo (this repo) --- quadlets copied by hand ---> ~/.config/containers/systemd/
                                                              |
+-------------------------------------------------------------v--+
| Raspberry Pi Zero 2 W: rootless Podman under systemd --user    |
|                                                                |
|  watchguard-monitor (standalone container, no published ports) |
|    DHT22 via sysfs IIO, INA219 via I2C, reed switch via GPIO   |
|    -> JSON telemetry on stdout (journald)                      |
|                                                                |
|  watchguard pod (one shared network namespace, pasta)          |
|    WireGuard   51820/udp  + CoreDNS resolver for peers         |
|    Cowrie       2222/tcp  SSH honeypot                         |
|    step-ca      9000/tcp  pod and VPN peers only, not on LAN   |
|                                                                |
|  podman-auto-update.timer: pulls newer images for the pinned   |
|  tags and restarts the units that use them                     |
+----------------------------------------------------------------+
```

## Services

### IoT Monitor

`gitops-agent/watchguard_monitor.py` holds the control logic (fan hysteresis, alert conditions) and the simulation backend; `gitops-agent/watchguard_hardware.py` holds the Raspberry Pi drivers. The Pi backend reads each sensor through a standard Linux interface, using the wiring in `docs/proyecto.md`:

| Sensor | Wiring | Interface | Host setup |
|---|---|---|---|
| DHT22 | GPIO4 | Kernel `dht11` IIO driver, which also handles the DHT22: `in_temp_input` and `in_humidityrelative_input` under `/sys/bus/iio/devices/iio:deviceN/` | `dtoverlay=dht11,gpiopin=4` in `/boot/firmware/config.txt` |
| INA219 | I2C1 (GPIO2/3), address 0x40 | `driver: smbus` (default): shunt- and bus-voltage registers read with `smbus2` on `/dev/i2c-1`, nothing written to the chip. `driver: hwmon`: kernel `ina2xx` driver under `/sys/class/hwmon/` | `dtparam=i2c_arm=on`; for `hwmon`, also bind the kernel driver to 0x40 as root |
| Reed switch | GPIO17 to GND | libgpiod v2 (`gpiod` package) on `/dev/gpiochip0`, input with internal pull-up; the line reads high when the enclosure is open | User in the `gpio` group |

The `hardware` section of `config/watchguard.example.json` configures all of this: the IIO device name and DHT22 retry count, the INA219 driver, bus, address and shunt resistance (0.1 ohm on common breakouts; with `hwmon`, the current is rescaled from the driver's `shunt_resistor` to this value), and the GPIO chip, line, bias and open level of the reed switch. Set `"simulation": {"enabled": false}` to use it. `gpiod` and `smbus2` are imported only in hardware mode and are installed in the container image (`gitops-agent/requirements.txt`).

A failed read emits `{"device_id", "timestamp", "error"}` instead of a reading, and the monitor tries again on the next cycle instead of exiting. DHT22 timing errors, which are common, are retried within the cycle first. A limitation: one failing sensor blanks the whole reading for that cycle, including the tamper state.

Actuator decisions (fan, LED colour, buzzer, relay reset) are included in every event, but the monitor does not drive those GPIO outputs yet.

### Updates: what is GitOps today

- The quadlet files in `quadlets/` are the deployment description, versioned in this repository.
- The WireGuard, Cowrie and step-ca containers use `AutoUpdate=registry`. With `systemctl --user enable --now podman-auto-update.timer`, Podman checks the registries daily, pulls a newer image for the same tag and restarts the units that use it. If a restarted unit fails to start, it rolls back to the previous image.
- The tags decide what can move: `cowrie/cowrie:3.0` follows Cowrie 3.0.x releases, `linuxserver/wireguard:1.0.20260223` follows linuxserver.io rebuilds of that WireGuard release, and `smallstep/step-ca:0.30.2` is an exact release. Moving to a new major or minor version is a commit that changes the tag.
- The monitor uses `AutoUpdate=local`: rebuild `localhost/podman-watchguard-monitor:local` on the Pi and `podman auto-update` restarts it.

What does not exist: an agent that polls this repository and applies changed quadlets. The `gitops-agent/` folder contains only the monitor; it keeps that name because the report refers to that path. Until such an agent exists, a quadlet change is deployed by hand: `git pull`, copy the files, `systemctl --user daemon-reload`, restart the affected units.

### WireGuard VPN gateway

`lscr.io/linuxserver/wireguard` in server mode. Its settings (public `SERVERURL`, `PEERS`, subnet) come from `~/.config/podman-watchguard/wireguard.env`, created from `config/wireguard.env.example` and not committed. Server and peer keys are generated on first start inside the `watchguard-wireguard` volume; `LOG_CONFS=false` keeps peer configs, which contain private keys, out of the journal. Show a peer config as a QR code with `podman exec -it watchguard-wireguard /app/show-peer laptop`.

A rootless container cannot load kernel modules, so `wireguard` must be loaded on the host at boot (for example from `/etc/modules-load.d/`), together with any netfilter NAT modules the kernel does not load on its own. The pod enables `net.ipv4.ip_forward` in its own network namespace and names its interface `eth0`, which the image's NAT rule expects.

Peers use a CoreDNS resolver inside the container that forwards to the pod's resolver. There is no ad or malware filtering.

### SSH Honeypot (Cowrie)

`docker.io/cowrie/cowrie:3.0`, listening on 2222, which the pod publishes on the host. To catch real scanners, forward port 22 on the router to port 2222 on the Pi. The pod uses pasta, which keeps the attackers' real source addresses. Cowrie logs to the journal; its JSON log, TTY recordings and captured downloads stay in the `watchguard-cowrie` volume. The downloads are live malware samples.

Every container in a pod shares one network namespace. Code that escapes Cowrie's emulated shell would be in the same namespace as the WireGuard interface, the VPN peers' subnet and step-ca on port 9000. That is acceptable for a lab demo; for a honeypot exposed to the Internet, run Cowrie in a pod of its own.

### PKI / Certificate Authority (step-ca)

`docker.io/smallstep/step-ca:0.30.2`. It is not published on the LAN: it is reachable from inside the pod and from VPN peers at `https://10.13.13.1:9000`. To expose it on the LAN, add `PublishPort=9000:9000/tcp` to `quadlets/watchguard.pod`.

The CA is initialised once, by hand, before the unit is started. This avoids the image's automatic init, which prints the administrator password to the log. The password that decrypts the CA keys lives in a Podman secret, not in the repository or in the CA volume. Podman's default file driver stores it unencrypted, outside the volume:

```sh
openssl rand -base64 32 | tr -d '\n' | podman secret create watchguard-step-ca-password -
podman run --rm --entrypoint step \
  --secret watchguard-step-ca-password,uid=1000,gid=1000,mode=0400 \
  -v watchguard-step-ca:/home/step \
  docker.io/smallstep/step-ca:0.30.2 \
  ca init --name "Watchguard CA" --dns localhost --dns 10.13.13.1 \
    --address :9000 --provisioner admin \
    --password-file /run/secrets/watchguard-step-ca-password
```

Keep `localhost` among the DNS names: the health check connects to `https://localhost:9000`.

## Deploying on the Pi

These steps follow from the quadlets but have not been run end to end on a Pi yet.

1. Install Raspberry Pi OS Lite (64-bit) based on Debian 13 "trixie", which ships Podman 5.4. Bookworm ships Podman 4.3.1, which predates Quadlet (4.4) and `.pod` units (5.0). The Cowrie and WireGuard images and the `gpiod` wheels are only published for arm64, not 32-bit ARM.
2. In `/boot/firmware/config.txt`: `dtparam=i2c_arm=on` and `dtoverlay=dht11,gpiopin=4`. Load `wireguard` and `i2c-dev` at boot.
3. Add the user to the `gpio` and `i2c` groups and run `sudo loginctl enable-linger "$USER"` so user units start at boot.
4. Build the monitor image on the Pi: `make podman-build`.
5. Create `~/.config/podman-watchguard/watchguard.json` from `config/watchguard.example.json` with simulation disabled, and `~/.config/podman-watchguard/wireguard.env` from `config/wireguard.env.example`.
6. Initialise step-ca as shown above.
7. Install and start the units:

   ```sh
   mkdir -p ~/.config/containers/systemd
   cp quadlets/* ~/.config/containers/systemd/
   systemctl --user daemon-reload
   systemctl --user start watchguard-pod.service watchguard-monitor.service
   systemctl --user enable --now podman-auto-update.timer
   ```

The monitor quadlet passes `/dev/gpiochip0` and `/dev/i2c-1` only if they exist when the units are generated (boot or `daemon-reload`), and uses `GroupAdd=keep-groups`, which needs the crun runtime.

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

- Raspberry Pi OS Lite (64-bit), Debian 13 "trixie" base
- Podman 5.0+ for `.pod` quadlets (checked with the Podman 5.4.2 quadlet generator), with crun
- Git
- Python 3.11+ for the tests; the monitor image uses Python 3.12
- WireGuard kernel module on the host

## Project Structure

```text
podman-watchguard/
├── config/              # Example monitor config and WireGuard env file
├── containers/
│   └── monitor/         # IoT monitor container
├── gitops-agent/        # Monitor application code (no GitOps agent yet)
├── quadlets/            # Podman quadlet units: pod, monitor, WireGuard, Cowrie, step-ca
├── scripts/             # Setup, test and utility scripts
├── tests/               # Repository checks and hardware-backend unit tests
└── docs/                # Academic report in Markdown and PDF
```

## Local Validation

Run the repository checks and the hardware-backend tests (fake sysfs trees, a fake I2C bus and a fake gpiod module; no Pi needed):

```sh
make test
```

Check that the quadlets generate valid systemd units (needs Podman 5 installed):

```sh
QUADLET_UNIT_DIRS="$PWD/quadlets" /usr/libexec/podman/quadlet -dryrun -user
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
