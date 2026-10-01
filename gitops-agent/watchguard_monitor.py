#!/usr/bin/env python3
"""Podman Watchguard sensor and actuator monitor.

The production target is a Raspberry Pi Zero 2 W with GPIO/I2C sensors, read
by the drivers in watchguard_hardware.py. The default mode is simulation so the
container and tests can run on a laptop.

Actuator states are decided and reported in the JSON telemetry; driving the
fan, LED, buzzer and relay GPIO outputs is not implemented yet.
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

from watchguard_hardware import (
    ClimateSensor,
    PowerSensor,
    SensorError,
    TamperSensor,
    build_climate_sensor,
    build_power_sensor,
    build_tamper_sensor,
)


@dataclass(frozen=True)
class SensorReading:
    temperature_celsius: float
    humidity_percent: float
    voltage: float
    current_ma: float
    power_mw: float
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

    def close(self) -> None:
        """Release hardware handles; nothing to do by default."""


class SimulationBackend(SensorBackend):
    def __init__(self, config: dict[str, Any]) -> None:
        self._base = config.get("simulation", {})

    def read(self) -> SensorReading:
        temp = float(self._base.get("temperature_celsius", 32.0))
        humidity = float(self._base.get("humidity_percent", 55.0))
        voltage = float(self._base.get("voltage", 5.0)) + random.uniform(-0.03, 0.03)
        current = float(self._base.get("current_ma", 350.0)) + random.uniform(-15, 15)
        return SensorReading(
            temperature_celsius=round(temp + random.uniform(-0.4, 0.4), 2),
            humidity_percent=round(humidity + random.uniform(-1.0, 1.0), 2),
            voltage=round(voltage, 3),
            current_ma=round(current, 1),
            power_mw=round(voltage * current, 1),
            tamper_open=bool(self._base.get("tamper_open", False)),
        )


class RaspberryPiBackend(SensorBackend):
    """Reads the deployed sensors; configured by the ``hardware`` config section.

    DHT22 via the kernel IIO driver, INA219 via smbus2 or the hwmon driver and
    the reed switch via libgpiod. See watchguard_hardware.py for the details.
    """

    def __init__(
        self,
        config: dict[str, Any],
        climate: ClimateSensor | None = None,
        power: PowerSensor | None = None,
        tamper: TamperSensor | None = None,
    ) -> None:
        hardware = config.get("hardware", {})
        self._climate = climate if climate is not None else build_climate_sensor(hardware)
        self._power = power if power is not None else build_power_sensor(hardware)
        self._tamper = tamper if tamper is not None else build_tamper_sensor(hardware)

    def read(self) -> SensorReading:
        temperature, humidity = self._climate.read()
        voltage, current_ma = self._power.read()
        return SensorReading(
            temperature_celsius=round(temperature, 2),
            humidity_percent=round(humidity, 2),
            voltage=round(voltage, 3),
            current_ma=round(current_ma, 1),
            # Power delivered to the load: bus voltage (VIN-) times current,
            # the same product the INA219's power register holds. Computed here
            # because the smbus driver leaves the calibration register unset.
            power_mw=round(voltage * current_ma, 1),
            tamper_open=self._tamper.read(),
        )

    def close(self) -> None:
        for sensor in (self._climate, self._power, self._tamper):
            close = getattr(sensor, "close", None)
            if close is not None:
                close()


class Controller:
    """Decides the actuator states from one reading.

    The fan is an on/off loop with hysteresis on two variables: it turns on at
    ``fan_on_celsius`` or at the humidity ``warning_percent`` and only turns
    off once the temperature is back at ``fan_off_celsius`` and the humidity at
    ``fan_off_percent`` (5 points below the warning if not configured), so it
    does not flap around either threshold. The alerts (buzzer, red LED) use
    the plain thresholds.
    """

    def __init__(self, config: dict[str, Any]) -> None:
        control = config["temperature_control"]
        humidity = config["humidity"]
        power = config["power"]
        self._fan_on_celsius = float(control["fan_on_celsius"])
        self._fan_off_celsius = float(control["fan_off_celsius"])
        self._critical_celsius = float(control["critical_celsius"])
        self._humidity_warning = float(humidity["warning_percent"])
        self._humidity_fan_off = float(humidity.get("fan_off_percent", self._humidity_warning - 5))
        self._low_voltage = float(power["low_voltage"])
        self._high_current_ma = float(power["high_current_ma"])
        if not self._fan_off_celsius < self._fan_on_celsius <= self._critical_celsius:
            raise ValueError("need fan_off_celsius < fan_on_celsius <= critical_celsius")
        if not self._humidity_fan_off < self._humidity_warning:
            raise ValueError("need humidity fan_off_percent < warning_percent")
        self._thermal_fan = False
        self._humidity_fan = False

    def decide(self, reading: SensorReading) -> ActuatorState:
        temperature = reading.temperature_celsius
        if temperature >= self._fan_on_celsius:
            self._thermal_fan = True
        elif temperature <= self._fan_off_celsius:
            self._thermal_fan = False

        humidity = reading.humidity_percent
        if humidity >= self._humidity_warning:
            self._humidity_fan = True
        elif humidity <= self._humidity_fan_off:
            self._humidity_fan = False

        fan = self._thermal_fan or self._humidity_fan
        critical = temperature >= self._critical_celsius
        humid = humidity >= self._humidity_warning
        low_voltage = reading.voltage <= self._low_voltage
        high_current = reading.current_ma >= self._high_current_ma
        alert = critical or humid or low_voltage or high_current or reading.tamper_open

        return ActuatorState(
            fan_on=fan,
            buzzer_on=alert,
            led_state="red" if alert else ("blue" if fan else "green"),
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


def emit_error(device_id: str, error: Exception) -> None:
    event = {"device_id": device_id, "timestamp": int(time.time()), "error": str(error)}
    print(json.dumps(event, sort_keys=True), flush=True)


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

    try:
        while True:
            try:
                reading = backend.read()
            except SensorError as exc:
                # A failed read is reported and retried on the next cycle
                # instead of crashing the service.
                emit_error(config["device_id"], exc)
            else:
                actuators = controller.decide(reading)
                emit_event(config["device_id"], reading, actuators)
            iterations += 1
            if args.iterations and iterations >= args.iterations:
                return 0
            time.sleep(interval)
    finally:
        backend.close()


if __name__ == "__main__":
    raise SystemExit(main())
