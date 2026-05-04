#!/usr/bin/env python3
"""Podman Watchguard sensor and actuator monitor.

The production target is a Raspberry Pi Zero 2 W with GPIO/I2C sensors.
The default mode is simulation so the container and tests can run on a laptop.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class SensorReading:
    temperature_celsius: float
    humidity_percent: float
    voltage: float
    current_ma: float
    tamper_open: bool


@dataclass(frozen=True)
class ActuatorState:
    fan_on: bool
    buzzer_on: bool
    led_state: str
    relay_reset_requested: bool


class SensorBackend:
    def read(self) -> SensorReading:
        raise NotImplementedError


class SimulationBackend(SensorBackend):
    def __init__(self, config: dict[str, Any]) -> None:
        self._base = config.get("simulation", {})

    def read(self) -> SensorReading:
        temp = float(self._base.get("temperature_celsius", 32.0))
        humidity = float(self._base.get("humidity_percent", 55.0))
        voltage = float(self._base.get("voltage", 5.0))
        current = float(self._base.get("current_ma", 350.0))
        return SensorReading(
            temperature_celsius=round(temp + random.uniform(-0.4, 0.4), 2),
            humidity_percent=round(humidity + random.uniform(-1.0, 1.0), 2),
            voltage=round(voltage + random.uniform(-0.03, 0.03), 3),
            current_ma=round(current + random.uniform(-15, 15), 1),
            tamper_open=bool(self._base.get("tamper_open", False)),
        )


class RaspberryPiBackend(SensorBackend):
    def __init__(self, config: dict[str, Any]) -> None:
        self._config = config

    def read(self) -> SensorReading:
        raise RuntimeError(
            "GPIO/I2C backend is a hardware integration point. "
            "Install adafruit-circuitpython-dht and adafruit-circuitpython-ina219 "
            "on the Raspberry Pi, then implement this backend for the deployed pins."
        )


class Controller:
    def __init__(self, config: dict[str, Any]) -> None:
        self._config = config
        self._fan_latched = False

    def decide(self, reading: SensorReading) -> ActuatorState:
        control = self._config["temperature_control"]
        humidity = self._config["humidity"]
        power = self._config["power"]

        if reading.temperature_celsius >= float(control["fan_on_celsius"]):
            self._fan_latched = True
        elif reading.temperature_celsius <= float(control["fan_off_celsius"]):
            self._fan_latched = False

        critical = reading.temperature_celsius >= float(control["critical_celsius"])
        humid = reading.humidity_percent >= float(humidity["warning_percent"])
        low_voltage = reading.voltage <= float(power["low_voltage"])
        high_current = reading.current_ma >= float(power["high_current_ma"])
        alert = critical or humid or low_voltage or high_current or reading.tamper_open

        return ActuatorState(
            fan_on=self._fan_latched,
            buzzer_on=alert,
            led_state="red" if alert else ("blue" if self._fan_latched else "green"),
            relay_reset_requested=low_voltage and high_current,
        )


def load_config(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def build_backend(config: dict[str, Any]) -> SensorBackend:
    simulation = config.get("simulation", {})
    if simulation.get("enabled", True):
        return SimulationBackend(config)
    return RaspberryPiBackend(config)


def emit_event(device_id: str, reading: SensorReading, actuators: ActuatorState) -> None:
    event = {
        "device_id": device_id,
        "timestamp": int(time.time()),
        "reading": reading.__dict__,
        "actuators": actuators.__dict__,
    }
    print(json.dumps(event, sort_keys=True), flush=True)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Podman Watchguard IoT monitor")
    parser.add_argument("--config", default="config/watchguard.example.json")
    parser.add_argument("--iterations", type=int, default=0, help="0 runs forever")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    config = load_config(Path(args.config))
    backend = build_backend(config)
    controller = Controller(config)
    interval = float(config.get("sample_interval_seconds", 2))
    iterations = 0

    while True:
        reading = backend.read()
        actuators = controller.decide(reading)
        emit_event(config["device_id"], reading, actuators)
        iterations += 1
        if args.iterations and iterations >= args.iterations:
            return 0
        time.sleep(interval)


if __name__ == "__main__":
    raise SystemExit(main())
