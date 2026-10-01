#!/usr/bin/env python3
"""Podman Watchguard sensor and actuator monitor.

The production target is a Raspberry Pi Zero 2 W with GPIO/I2C sensors and
GPIO actuators, driven by watchguard_hardware.py. The default mode is
simulation so the container and tests can run on a laptop: sensor values come
from the config and the actuator decisions are reported but not driven.

Every cycle reads the sensors, decides the actuator states, drives them and
prints one JSON event. On any exit (end of --iterations, SIGTERM, an
unexpected error) every actuator is de-energized before the GPIO lines are
released.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import random
import signal
import sys
import time
from collections.abc import Callable, Iterator, Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from watchguard_hardware import (
    ActuatorError,
    ClimateSensor,
    Outputs,
    PowerSensor,
    SensorError,
    TamperSensor,
    build_climate_sensor,
    build_gpio_outputs,
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
    relay_on: bool
    relay_phase: str


@dataclass(frozen=True)
class Decision:
    actuators: ActuatorState
    # Names of the conditions that raised the alert (buzzer, red LED).
    alerts: tuple[str, ...]


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


class RelaySequencer:
    """Power-cycles the router through the relay, behind a safety delay.

    The relay cuts the router's power only after the anomalous power condition
    (low voltage and high current at once) has been present on every reading
    for ``trigger_delay_seconds``. It then holds the power off for
    ``power_off_seconds`` whatever the readings say (the current drops as soon
    as the load is off) and, once power is back, ignores the condition for
    ``cooldown_seconds`` so the router can boot before it is judged again. A
    reading without the condition while armed starts the delay over.

    Phases: ``idle`` -> ``armed`` -> ``power_cut`` -> ``cooldown`` -> ``idle``.
    The relay is energized only in ``power_cut``. Times come from a monotonic
    clock, so they are at least the configured values, rounded up to the next
    reading.
    """

    IDLE = "idle"
    ARMED = "armed"
    POWER_CUT = "power_cut"
    COOLDOWN = "cooldown"

    def __init__(
        self,
        trigger_delay_seconds: float = 30.0,
        power_off_seconds: float = 10.0,
        cooldown_seconds: float = 300.0,
    ) -> None:
        if trigger_delay_seconds <= 0:
            raise ValueError("relay trigger_delay_seconds must be positive")
        if power_off_seconds <= 0:
            raise ValueError("relay power_off_seconds must be positive")
        if cooldown_seconds < 0:
            raise ValueError("relay cooldown_seconds cannot be negative")
        self._trigger_delay = trigger_delay_seconds
        self._power_off = power_off_seconds
        self._cooldown = cooldown_seconds
        self.phase = self.IDLE
        self._since = 0.0

    def update(self, reset_requested: bool, now: float) -> str:
        if self.phase == self.POWER_CUT and now - self._since >= self._power_off:
            self._enter(self.COOLDOWN, now)
        if self.phase == self.COOLDOWN and now - self._since >= self._cooldown:
            self._enter(self.IDLE, now)
        if self.phase == self.IDLE:
            if reset_requested:
                self._enter(self.ARMED, now)
        elif self.phase == self.ARMED:
            if not reset_requested:
                self._enter(self.IDLE, now)
            elif now - self._since >= self._trigger_delay:
                self._enter(self.POWER_CUT, now)
        return self.phase

    def abort(self, now: float) -> str:
        """Release the relay at once; a cut in progress still earns its cooldown."""
        if self.phase == self.POWER_CUT:
            self._enter(self.COOLDOWN, now)
        elif self.phase == self.ARMED:
            self._enter(self.IDLE, now)
        return self.phase

    def _enter(self, phase: str, now: float) -> None:
        self.phase = phase
        self._since = now


class Controller:
    """Decides the actuator states from one reading.

    The fan is an on/off loop with hysteresis on two variables: it turns on at
    ``fan_on_celsius`` or at the humidity ``warning_percent`` and only turns
    off once the temperature is back at ``fan_off_celsius`` and the humidity at
    ``fan_off_percent`` (5 points below the warning if not configured), so it
    does not flap around either threshold. The alerts (buzzer, red LED) use
    the plain thresholds. The relay reset goes through RelaySequencer.
    """

    def __init__(self, config: dict[str, Any], clock: Callable[[], float] = time.monotonic) -> None:
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
        relay = config.get("relay", {})
        self._relay = RelaySequencer(
            trigger_delay_seconds=float(relay.get("trigger_delay_seconds", 30.0)),
            power_off_seconds=float(relay.get("power_off_seconds", 10.0)),
            cooldown_seconds=float(relay.get("cooldown_seconds", 300.0)),
        )
        self._clock = clock
        self._thermal_fan = False
        self._humidity_fan = False

    def decide(self, reading: SensorReading) -> Decision:
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
        conditions = {
            "temperature_critical": temperature >= self._critical_celsius,
            "humidity_high": humidity >= self._humidity_warning,
            "low_voltage": reading.voltage <= self._low_voltage,
            "high_current": reading.current_ma >= self._high_current_ma,
            "tamper": reading.tamper_open,
        }
        alerts = tuple(name for name, active in conditions.items() if active)
        reset_requested = conditions["low_voltage"] and conditions["high_current"]
        relay_phase = self._relay.update(reset_requested, self._clock())

        actuators = ActuatorState(
            fan_on=fan,
            buzzer_on=bool(alerts),
            led_state="red" if alerts else ("blue" if fan else "green"),
            relay_reset_requested=reset_requested,
            relay_on=relay_phase == RelaySequencer.POWER_CUT,
            relay_phase=relay_phase,
        )
        return Decision(actuators, alerts)

    def fault(self) -> Decision:
        """Decide for a cycle without a valid reading.

        The relay is released at once: no power cut without a fresh
        measurement. The fan is latched on, as if the cabinet were hot, until a
        valid reading is back at the fan-off thresholds. The LED turns red to
        show the fault. The buzzer stays quiet: it is kept for the conditions
        in the report, and DHT22 read failures are common enough to make it a
        nuisance.
        """
        self._thermal_fan = True
        actuators = ActuatorState(
            fan_on=True,
            buzzer_on=False,
            led_state="red",
            relay_reset_requested=False,
            relay_on=False,
            relay_phase=self.release_relay(),
        )
        return Decision(actuators, ("sensor_error",))

    def release_relay(self) -> str:
        """Abort any relay sequence; used when the outputs could not be driven."""
        return self._relay.abort(self._clock())


class SimulatedOutputs:
    """Simulation mode drives no GPIO; the decisions are only reported."""

    def write(self, levels: Mapping[str, bool]) -> None:
        pass

    def close(self) -> None:
        pass


LED_COLOURS = ("green", "blue", "red")


def output_levels(actuators: ActuatorState) -> dict[str, bool]:
    """Map actuator states onto the outputs named in watchguard_hardware."""
    levels = {"fan": actuators.fan_on, "buzzer": actuators.buzzer_on, "relay": actuators.relay_on}
    for colour in LED_COLOURS:
        levels[f"led_{colour}"] = actuators.led_state == colour
    return levels


def load_config(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def build_backend(config: dict[str, Any]) -> SensorBackend:
    simulation = config.get("simulation", {})
    if simulation.get("enabled", True):
        return SimulationBackend(config)
    return RaspberryPiBackend(config)


def build_outputs(config: dict[str, Any]) -> Outputs:
    simulation = config.get("simulation", {})
    if simulation.get("enabled", True):
        return SimulatedOutputs()
    return build_gpio_outputs(config.get("hardware", {}))


def emit(device_id: str, fields: dict[str, Any]) -> None:
    event = {"device_id": device_id, "timestamp": int(time.time()), **fields}
    print(json.dumps(event, sort_keys=True), flush=True)


def run_cycle(backend: SensorBackend, controller: Controller, outputs: Outputs, device_id: str) -> None:
    """Read, decide, drive the outputs and emit one event."""
    fields: dict[str, Any]
    try:
        reading = backend.read()
    except SensorError as exc:
        # A failed read is reported and retried on the next cycle instead of
        # crashing the service; meanwhile the outputs take the fault state.
        decision = controller.fault()
        fields = {"error": str(exc)}
    else:
        decision = controller.decide(reading)
        fields = {"reading": asdict(reading)}
    fields["alerts"] = list(decision.alerts)
    try:
        outputs.write(output_levels(decision.actuators))
    except ActuatorError as exc:
        # The driver has already tried to de-energize every output and
        # released the lines; drop any relay sequence and retry next cycle.
        controller.release_relay()
        fields["actuator_error"] = str(exc)
    else:
        fields["actuators"] = asdict(decision.actuators)
    emit(device_id, fields)


@contextlib.contextmanager
def exit_on_sigterm() -> Iterator[None]:
    """Turn SIGTERM into SystemExit so that cleanup runs.

    ``podman stop`` and ``systemctl stop`` send SIGTERM. Python's default
    action ends the process without unwinding, and as PID 1 of the container
    the monitor does not even get the default action, so podman would wait
    for its timeout and SIGKILL it with the actuators still driven.
    """

    def stop(signum: int, frame: Any) -> None:
        raise SystemExit(0)

    previous = signal.signal(signal.SIGTERM, stop)
    try:
        yield
    finally:
        if previous is not None:
            signal.signal(signal.SIGTERM, previous)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Podman Watchguard IoT monitor")
    parser.add_argument("--config", default="config/watchguard.example.json")
    parser.add_argument("--iterations", type=int, default=0, help="0 runs forever")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    config = load_config(Path(args.config))
    controller = Controller(config)
    interval = float(config.get("sample_interval_seconds", 2))
    iterations = 0

    with contextlib.ExitStack() as cleanup:
        cleanup.enter_context(exit_on_sigterm())
        backend = build_backend(config)
        cleanup.callback(backend.close)
        outputs = build_outputs(config)
        # Registered last so it runs first: the actuators are de-energized
        # before anything else is released, whatever ends the loop.
        cleanup.callback(outputs.close)
        while True:
            run_cycle(backend, controller, outputs, config["device_id"])
            iterations += 1
            if args.iterations and iterations >= args.iterations:
                return 0
            time.sleep(interval)


if __name__ == "__main__":
    raise SystemExit(main())
