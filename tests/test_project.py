#!/usr/bin/env python3
from __future__ import annotations

import configparser
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def assert_exists(path: str) -> None:
    target = ROOT / path
    assert target.exists(), f"missing {path}"


def test_required_files() -> None:
    for path in [
        "README.md",
        "docs/proyecto.md",
        "docs/proyecto.pdf",
        "docs/test-report.md",
        "config/watchguard.example.json",
        "config/wireguard.env.example",
        "containers/monitor/Containerfile",
        "gitops-agent/watchguard_monitor.py",
        "gitops-agent/watchguard_hardware.py",
        "gitops-agent/requirements.txt",
        "quadlets/watchguard-monitor.container",
        "quadlets/watchguard-wireguard.container",
        "quadlets/watchguard-cowrie.container",
        "quadlets/watchguard-step-ca.container",
        "quadlets/watchguard.pod",
        "scripts/install-podman-macos.sh",
        "scripts/podman-smoke-test.sh",
        "scripts/uninstall-podman-macos.sh",
    ]:
        assert_exists(path)


def test_config_is_valid_json() -> None:
    with (ROOT / "config/watchguard.example.json").open(encoding="utf-8") as fh:
        config = json.load(fh)
    assert config["temperature_control"]["fan_on_celsius"] > config["temperature_control"]["fan_off_celsius"]
    assert config["simulation"]["enabled"] is True


def quadlet_files() -> list[Path]:
    return sorted((ROOT / "quadlets").glob("*.container")) + sorted((ROOT / "quadlets").glob("*.pod"))


def read_quadlet(path: Path) -> configparser.ConfigParser:
    parser = configparser.ConfigParser(strict=False, interpolation=None)
    parser.optionxform = str
    read = parser.read(path)
    assert read, f"could not parse {path}"
    return parser


def test_quadlets_parse_as_ini() -> None:
    for path in quadlet_files():
        parser = read_quadlet(path)
        assert "Unit" in parser.sections(), f"{path.name} missing Unit section"


def test_quadlets_support_auto_update() -> None:
    for path in sorted((ROOT / "quadlets").glob("*.container")):
        container = read_quadlet(path)["Container"]
        pod = container.get("Pod")
        if pod:
            assert (ROOT / "quadlets" / pod).exists(), f"{path.name} joins missing {pod}"
        policy = container.get("AutoUpdate")
        assert policy in ("registry", "local"), f"{path.name} has no AutoUpdate policy"
        if policy == "registry":
            image = container["Image"]
            registry, _, rest = image.partition("/")
            assert "." in registry, f"{path.name}: auto-update needs a fully qualified image, got {image}"
            tag = rest.rpartition(":")[2] if ":" in rest else ""
            assert tag and tag != "latest", f"{path.name}: pin a tag instead of {image}"


def test_document_answers_assignment() -> None:
    text = (ROOT / "docs/proyecto.md").read_text(encoding="utf-8")
    required = [
        "Problema que resuelve",
        "Hardware propuesto",
        "Justificacion del hardware",
        "Software tentativo",
        "Circuito y conexionado",
        "Gabinete",
        "Sensores",
        "Actuadores",
    ]
    for heading in required:
        assert heading in text, f"docs/proyecto.md missing {heading}"


def test_monitor_outputs_json() -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "gitops-agent/watchguard_monitor.py"),
            "--config",
            str(ROOT / "config/watchguard.example.json"),
            "--iterations",
            "1",
        ],
        cwd=ROOT,
        check=True,
        text=True,
        capture_output=True,
    )
    event = json.loads(result.stdout)
    assert event["device_id"] == "podman-watchguard-demo"
    assert "reading" in event
    assert "actuators" in event


def main() -> int:
    tests = [
        test_required_files,
        test_config_is_valid_json,
        test_quadlets_parse_as_ini,
        test_quadlets_support_auto_update,
        test_document_answers_assignment,
        test_monitor_outputs_json,
    ]
    for test in tests:
        test()
        print(f"ok - {test.__name__}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
