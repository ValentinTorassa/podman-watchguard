# Test report

Date: 2026-05-04, updated 2026-10-04 (repository tests re-run, Podman smoke test of 2026-10-01 added).

## Repository tests

Command:

```sh
make test
```

Result (2026-10-04): passed, 51 tests in three suites, all with simulated devices.

- `tests/test_project.py` (6):
  - Required project files exist, including `docs/proyecto.md` and `docs/proyecto.pdf`.
  - JSON configuration parses correctly.
  - Quadlet files parse as systemd-style INI files and support auto-update.
  - The academic Markdown document includes the required assignment sections.
  - The monitor runs in simulation mode and emits valid JSON.
- `tests/test_hardware.py` (24): sensor reads and actuator control against fake sysfs trees, a fake I2C bus and a fake `gpiod` module, including read errors, a missing GPIO chip and the safe state on SIGTERM.
- `tests/test_control.py` (21): fan hysteresis, alerts, the relay safety delay and the safe state on sensor faults or when the monitor stops.

Nothing has run yet on a Raspberry Pi with the circuit wired.

## PDF generation

Command:

```sh
make docs
```

Result: passed.

Output (last regenerated 2026-10-01):

- `docs/proyecto.pdf`
- PDF version 1.4
- 6 pages

## Podman smoke test on Linux (2026-10-01)

Podman was not installed on the available Linux machines, so it ran inside a
container: image `quay.io/podman/stable` (Podman 5.8.7 on Fedora 44), with
`--privileged`, on Docker 29.8.1 rootless, on an x86_64 desktop running Debian 13
(kernel 6.12). Inside the container Podman ran as root, with overlay storage and
cgroups v2. The commands are the `Makefile` targets:

```sh
make podman-build
make podman-smoke
```

Observed result:

- Build: all 7 steps succeeded (`gpiod` 2.5.0, `smbus2` 0.6.1), 133 MB image, exit code 0.
- Smoke: three JSON events, one every 2 s, exit code 0. With the example config's
  simulation, all three show the fan on, blue LED, buzzer and relay off, and no alerts.

Result: passed. This proves the image and the container flow, not the hardware or
the quadlet deployment: it did not run on the Raspberry Pi, on arm64, or with
rootless Podman. Source: `docs/paper-vs-code.md`, section 8.

## Podman install/test/uninstall cycle on macOS (2026-05-04)

### Install

Command:

```sh
./scripts/install-podman-macos.sh
```

Observed result:

- Homebrew installed Podman `5.8.2`.
- `podman --version` was available after installation.
- `podman machine init` started downloading the Podman machine image from `quay.io/podman/machine-os:5.8`.
- The VM image download stalled for several minutes with no additional progress, so the hung initialization process was stopped.

### Smoke test

Command:

```sh
./scripts/podman-smoke-test.sh
```

Observed result:

```text
podman version 5.8.2
Cannot connect to Podman. Please verify your connection to the Linux system using `podman system connection list`, or try `podman machine init` and `podman machine start` to manage a new Linux VM
Error: unable to connect to Podman socket
```

Conclusion: the Podman CLI installed correctly, but the container runtime could not be exercised on this macOS host because the required Linux VM was not created. The repository container build/run path is still documented and scripted; it should work once `podman machine init` and `podman machine start` complete successfully. The macOS cycle has not been repeated; the same build and smoke passed on Linux on 2026-10-01 (section above).

### Uninstall

Command:

```sh
./scripts/uninstall-podman-macos.sh
```

Observed result:

- No `podman-machine-default` VM existed after the interrupted initialization.
- Homebrew removed `/opt/homebrew/Cellar/podman/5.8.2`.
- `command -v podman` returned no path after uninstall.

Result: uninstall completed.
