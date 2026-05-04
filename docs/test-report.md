# Test report

Date: 2026-05-04

## Repository tests

Command:

```sh
make test
```

Result: passed.

Checks covered:

- Required project files exist, including `docs/proyecto.md` and `docs/proyecto.pdf`.
- JSON configuration parses correctly.
- Quadlet files parse as systemd-style INI files.
- The academic Markdown document includes the required assignment sections.
- The monitor runs in simulation mode and emits valid JSON.

## PDF generation

Command:

```sh
make docs
```

Result: passed.

Output:

- `docs/proyecto.pdf`
- PDF version 1.4
- 5 pages

## Podman install/test/uninstall cycle on macOS

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

Conclusion: the Podman CLI installed correctly, but the container runtime could not be exercised on this macOS host because the required Linux VM was not created. The repository container build/run path is still documented and scripted; it should work once `podman machine init` and `podman machine start` complete successfully.

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
