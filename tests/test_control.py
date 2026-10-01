#!/usr/bin/env python3
"""Control logic tests: fan hysteresis, alerts and the actuator states they produce."""

from __future__ import annotations

import contextlib
import io
import json
import signal
import subprocess
import sys
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "gitops-agent"))

import watchguard_hardware as hw  # noqa: E402
import watchguard_monitor as monitor  # noqa: E402


CALM = monitor.SensorReading(
    temperature_celsius=25.0,
    humidity_percent=50.0,
    voltage=5.05,
    current_ma=420.0,
    power_mw=2121.0,
    tamper_open=False,
)


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def example_config() -> dict[str, Any]:
    with (ROOT / "config/watchguard.example.json").open(encoding="utf-8") as fh:
        return json.load(fh)


def reading(**changes: Any) -> monitor.SensorReading:
    return replace(CALM, **changes)


def raises(exc_type: type[BaseException], func: Any, *args: Any) -> BaseException:
    try:
        func(*args)
    except exc_type as exc:
        return exc
    raise AssertionError(f"{func} did not raise {exc_type.__name__}")


def test_calm_cabinet_is_green() -> None:
    state = monitor.Controller(example_config()).decide(CALM).actuators
    assert state == monitor.ActuatorState(
        fan_on=False,
        buzzer_on=False,
        led_state="green",
        relay_reset_requested=False,
        relay_on=False,
        relay_phase="idle",
    )


def test_fan_temperature_hysteresis() -> None:
    controller = monitor.Controller(example_config())
    temperatures = (33.9, 34.0, 31.0, 30.1, 30.0, 33.0)
    fan = [controller.decide(reading(temperature_celsius=t)).actuators.fan_on for t in temperatures]
    assert fan == [False, True, True, True, False, False]
    assert controller.decide(reading(temperature_celsius=35.0)).actuators.led_state == "blue"


def test_critical_temperature_alerts_with_fan() -> None:
    state = monitor.Controller(example_config()).decide(reading(temperature_celsius=42.0)).actuators
    assert (state.fan_on, state.buzzer_on, state.led_state) == (True, True, "red")


def test_high_humidity_turns_fan_on_with_hysteresis() -> None:
    controller = monitor.Controller(example_config())
    humid = controller.decide(reading(humidity_percent=75.0)).actuators
    assert (humid.fan_on, humid.buzzer_on, humid.led_state) == (True, True, "red")
    # Below the warning the alert clears, but the fan keeps drying the
    # cabinet until the humidity is down to fan_off_percent (70 %).
    drying = controller.decide(reading(humidity_percent=72.0)).actuators
    assert (drying.fan_on, drying.buzzer_on, drying.led_state) == (True, False, "blue")
    assert controller.decide(reading(humidity_percent=74.9)).actuators.fan_on is True
    dry = controller.decide(reading(humidity_percent=70.0)).actuators
    assert (dry.fan_on, dry.led_state) == (False, "green")


def test_fan_stays_on_while_either_variable_needs_it() -> None:
    controller = monitor.Controller(example_config())
    controller.decide(reading(temperature_celsius=35.0, humidity_percent=80.0))
    assert controller.decide(reading(temperature_celsius=29.0, humidity_percent=72.0)).actuators.fan_on is True
    assert controller.decide(reading(temperature_celsius=32.0, humidity_percent=60.0)).actuators.fan_on is False


def test_humidity_fan_off_defaults_below_warning() -> None:
    config = example_config()
    del config["humidity"]["fan_off_percent"]
    controller = monitor.Controller(config)
    controller.decide(reading(humidity_percent=76.0))
    assert controller.decide(reading(humidity_percent=70.1)).actuators.fan_on is True
    assert controller.decide(reading(humidity_percent=70.0)).actuators.fan_on is False


def test_tamper_sounds_buzzer_and_red_led() -> None:
    decision = monitor.Controller(example_config()).decide(reading(tamper_open=True))
    state = decision.actuators
    assert (state.fan_on, state.buzzer_on, state.led_state) == (False, True, "red")
    assert decision.alerts == ("tamper",)


def test_alerts_name_their_conditions() -> None:
    decision = monitor.Controller(example_config()).decide(
        reading(temperature_celsius=43.0, humidity_percent=80.0, voltage=4.7, current_ma=1000.0)
    )
    assert decision.alerts == ("temperature_critical", "humidity_high", "low_voltage", "high_current")
    assert monitor.Controller(example_config()).decide(CALM).alerts == ()


def test_power_alerts_and_reset_request() -> None:
    controller = monitor.Controller(example_config())
    low = controller.decide(reading(voltage=4.7)).actuators
    assert (low.buzzer_on, low.relay_reset_requested) == (True, False)
    high = controller.decide(reading(current_ma=1000.0)).actuators
    assert (high.buzzer_on, high.relay_reset_requested) == (True, False)
    both = controller.decide(reading(voltage=4.7, current_ma=1000.0)).actuators
    assert (both.buzzer_on, both.relay_reset_requested) == (True, True)


POWER_FAULT = {"voltage": 4.7, "current_ma": 1000.0}


def relay_run(controller: monitor.Controller, clock: FakeClock, steps: list[tuple[float, bool]]) -> list[str]:
    """Feed (time offset, power fault?) readings and collect the relay phases."""
    start = clock.now
    phases = []
    for offset, fault in steps:
        clock.now = start + offset
        state = controller.decide(reading(**POWER_FAULT) if fault else CALM).actuators
        assert state.relay_on == (state.relay_phase == "power_cut")
        phases.append(state.relay_phase)
    return phases


def test_relay_waits_for_the_safety_delay() -> None:
    clock = FakeClock()
    controller = monitor.Controller(example_config(), clock=clock)
    phases = relay_run(
        controller,
        clock,
        [
            (0, True),  # fault seen: armed, relay still released
            (28, True),
            (30, True),  # held for trigger_delay_seconds: power cut
            (32, False),  # router off, current drops: the cut is not cut short
            (39, False),
            (40, False),  # power_off_seconds later: power restored
            (100, True),  # router booting: ignored during the cooldown
            (339, True),
            (340, True),  # cooldown over: armed again, not cut straight away
            (369, True),
            (370, True),
        ],
    )
    assert phases == [
        "armed",
        "armed",
        "power_cut",
        "power_cut",
        "power_cut",
        "cooldown",
        "cooldown",
        "cooldown",
        "armed",
        "armed",
        "power_cut",
    ]


def test_relay_needs_the_fault_on_every_reading() -> None:
    clock = FakeClock()
    controller = monitor.Controller(example_config(), clock=clock)
    phases = relay_run(controller, clock, [(0, True), (20, False), (22, True), (50, True), (52, True)])
    assert phases == ["armed", "idle", "armed", "armed", "power_cut"]


def test_relay_ignores_a_single_power_alert() -> None:
    clock = FakeClock()
    controller = monitor.Controller(example_config(), clock=clock)
    for offset in (0, 60, 120):
        clock.now += offset
        state = controller.decide(reading(voltage=4.7)).actuators
        assert (state.buzzer_on, state.relay_phase) == (True, "idle")


def test_relay_timing_comes_from_config() -> None:
    config = example_config()
    config["relay"] = {"trigger_delay_seconds": 4, "power_off_seconds": 2, "cooldown_seconds": 0}
    clock = FakeClock()
    controller = monitor.Controller(config, clock=clock)
    phases = relay_run(controller, clock, [(0, True), (4, True), (6, True), (8, True), (12, True)])
    assert phases == ["armed", "power_cut", "armed", "armed", "power_cut"]
    for bad in ({"trigger_delay_seconds": 0}, {"power_off_seconds": 0}, {"cooldown_seconds": -1}):
        config["relay"] = bad
        raises(ValueError, monitor.Controller, config)


def test_sensor_fault_releases_relay_and_cools() -> None:
    clock = FakeClock()
    controller = monitor.Controller(example_config(), clock=clock)
    assert relay_run(controller, clock, [(0, True), (30, True)]) == ["armed", "power_cut"]
    clock.now += 2
    decision = controller.fault()
    assert decision.alerts == ("sensor_error",)
    assert decision.actuators == monitor.ActuatorState(
        fan_on=True,
        buzzer_on=False,
        led_state="red",
        relay_reset_requested=False,
        relay_on=False,
        relay_phase="cooldown",
    )
    # The fan stays latched on until a valid reading shows a cool cabinet.
    assert controller.decide(reading(temperature_celsius=32.0)).actuators.fan_on is True
    assert controller.decide(reading(temperature_celsius=30.0)).actuators.fan_on is False


def test_sensor_fault_disarms_relay() -> None:
    clock = FakeClock()
    controller = monitor.Controller(example_config(), clock=clock)
    relay_run(controller, clock, [(0, True)])
    clock.now += 20
    assert controller.fault().actuators.relay_phase == "idle"
    assert relay_run(controller, clock, [(2, True), (30, True), (32, True)]) == ["armed", "armed", "power_cut"]


def test_controller_rejects_inverted_thresholds() -> None:
    config = example_config()
    config["temperature_control"]["fan_off_celsius"] = 36.0
    raises(ValueError, monitor.Controller, config)
    config = example_config()
    config["humidity"]["fan_off_percent"] = 80.0
    raises(ValueError, monitor.Controller, config)


def test_output_levels_light_one_led_colour() -> None:
    state = monitor.ActuatorState(
        fan_on=True,
        buzzer_on=False,
        led_state="blue",
        relay_reset_requested=True,
        relay_on=True,
        relay_phase="power_cut",
    )
    assert monitor.output_levels(state) == {
        "fan": True,
        "buzzer": False,
        "relay": True,
        "led_green": False,
        "led_blue": True,
        "led_red": False,
    }
    assert set(monitor.output_levels(state)) == set(hw.OUTPUT_NAMES)


class RecordingOutputs:
    def __init__(self, fail: bool = False) -> None:
        self.writes: list[dict[str, bool]] = []
        self.fail = fail
        self.closed = False

    def write(self, levels: Mapping[str, bool]) -> None:
        if self.fail:
            raise hw.ActuatorError("GPIO output on /dev/gpiochip0 failed: [Errno 5] Input/output error")
        self.writes.append(dict(levels))

    def close(self) -> None:
        self.closed = True


class ScriptedBackend(monitor.SensorBackend):
    def __init__(self, *results: monitor.SensorReading | Exception) -> None:
        self.results = list(results)

    def read(self) -> monitor.SensorReading:
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def cycle(backend: monitor.SensorBackend, controller: monitor.Controller, outputs: Any) -> dict[str, Any]:
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        monitor.run_cycle(backend, controller, outputs, "test-device")
    return json.loads(out.getvalue())


def test_cycle_drives_outputs_and_reports_them() -> None:
    outputs = RecordingOutputs()
    event = cycle(ScriptedBackend(reading(tamper_open=True)), monitor.Controller(example_config()), outputs)
    assert outputs.writes == [
        {"fan": False, "buzzer": True, "relay": False, "led_green": False, "led_blue": False, "led_red": True}
    ]
    assert event["reading"]["tamper_open"] is True
    assert event["alerts"] == ["tamper"]
    assert event["actuators"]["buzzer_on"] is True


def test_cycle_on_sensor_error_drives_fault_state() -> None:
    outputs = RecordingOutputs()
    backend = ScriptedBackend(hw.SensorError("no device named 'dht11' under /sys/bus/iio/devices"))
    event = cycle(backend, monitor.Controller(example_config()), outputs)
    assert "reading" not in event
    assert "dht11" in event["error"]
    assert event["alerts"] == ["sensor_error"]
    assert outputs.writes[0]["fan"] is True
    assert outputs.writes[0]["relay"] is False
    assert outputs.writes[0]["led_red"] is True


def test_cycle_reports_actuator_errors_and_drops_relay_sequence() -> None:
    clock = FakeClock()
    controller = monitor.Controller(example_config(), clock=clock)
    relay_run(controller, clock, [(0, True), (30, True)])
    event = cycle(ScriptedBackend(reading(**POWER_FAULT)), controller, RecordingOutputs(fail=True))
    assert "Input/output error" in event["actuator_error"]
    assert "actuators" not in event
    clock.now += 4
    assert controller.decide(reading(**POWER_FAULT)).actuators.relay_phase == "cooldown"


def test_monitor_exits_cleanly_on_sigterm() -> None:
    process = subprocess.Popen(
        [
            sys.executable,
            str(ROOT / "gitops-agent/watchguard_monitor.py"),
            "--config",
            str(ROOT / "config/watchguard.example.json"),
        ],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert process.stdout is not None
        json.loads(process.stdout.readline())
        process.send_signal(signal.SIGTERM)
        assert process.wait(timeout=10) == 0
    finally:
        process.kill()
        process.wait()
        if process.stdout is not None:
            process.stdout.close()


def main() -> int:
    tests = [value for name, value in globals().items() if name.startswith("test_") and callable(value)]
    for test in tests:
        test()
        print(f"ok - {test.__name__}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
