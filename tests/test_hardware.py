#!/usr/bin/env python3
"""Raspberry Pi backend tests against fake sysfs trees, a fake I2C bus and a fake gpiod."""

from __future__ import annotations

import contextlib
import enum
import io
import json
import signal
import sys
import tempfile
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "gitops-agent"))

import watchguard_hardware as hw  # noqa: E402
import watchguard_monitor as monitor  # noqa: E402


def write_attrs(device: Path, **attrs: str) -> Path:
    device.mkdir(parents=True, exist_ok=True)
    for name, value in attrs.items():
        (device / name).write_text(value + "\n", encoding="ascii")
    return device


def make_dht(sysfs: Path, temp: str = "23500", humidity: str = "45200") -> Path:
    devices = sysfs / "bus" / "iio" / "devices"
    write_attrs(devices / "iio:device0", name="mcp3008")
    return write_attrs(
        devices / "iio:device1",
        name="dht11@4",
        in_temp_input=temp,
        in_humidityrelative_input=humidity,
    )


def make_ina219_hwmon(sysfs: Path, bus_mv: str = "5012", curr_ma: str = "4200", shunt_uohm: str = "10000") -> Path:
    hwmon = sysfs / "class" / "hwmon"
    write_attrs(hwmon / "hwmon0", name="cpu_thermal", temp1_input="48000")
    return write_attrs(
        hwmon / "hwmon1",
        name="ina219",
        in1_input=bus_mv,
        curr1_input=curr_ma,
        shunt_resistor=shunt_uohm,
    )


class FakeBus:
    def __init__(self, registers: dict[int, int], fail: bool = False) -> None:
        self.registers = registers
        self.fail = fail
        self.reads: list[tuple[int, int, int]] = []
        self.closed = False

    def read_i2c_block_data(self, i2c_addr: int, register: int, length: int) -> list[int]:
        self.reads.append((i2c_addr, register, length))
        if self.fail:
            raise OSError(121, "Remote I/O error")
        value = self.registers[register]
        return [value >> 8, value & 0xFF]

    def close(self) -> None:
        self.closed = True


class FakeValue(enum.Enum):
    INACTIVE = 0
    ACTIVE = 1


class FakeDirection(enum.Enum):
    INPUT = 2
    OUTPUT = 3


class FakeBias(enum.Enum):
    AS_IS = 1
    DISABLED = 3
    PULL_UP = 4
    PULL_DOWN = 5


class FakeLineSettings:
    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs


class FakeRequest:
    def __init__(self, value: FakeValue) -> None:
        self.value = value
        self.released = False
        self.writes: list[dict[int, FakeValue]] = []
        self.failing_writes = 0

    def get_value(self, offset: int) -> FakeValue:
        return self.value

    def set_values(self, values: dict[int, FakeValue]) -> None:
        assert not self.released, "write on a released request"
        if self.failing_writes:
            self.failing_writes -= 1
            raise OSError(5, "Input/output error")
        self.writes.append(dict(values))

    def release(self) -> None:
        self.released = True

    def levels(self) -> dict[int, FakeValue]:
        """Current value of every written line."""
        current: dict[int, FakeValue] = {}
        for values in self.writes:
            current.update(values)
        return current


def fake_gpiod(value: FakeValue) -> SimpleNamespace:
    module = SimpleNamespace(calls=[], requests=[])

    def request_lines(path: str, config: dict[int, FakeLineSettings], consumer: str | None = None) -> FakeRequest:
        module.calls.append({"path": path, "config": config, "consumer": consumer})
        request = FakeRequest(value)
        module.requests.append(request)
        return request

    module.line = SimpleNamespace(Value=FakeValue, Direction=FakeDirection, Bias=FakeBias)
    module.LineSettings = FakeLineSettings
    module.request_lines = request_lines
    return module


def raises(exc_type: type[BaseException], func: Any, *args: Any) -> BaseException:
    try:
        func(*args)
    except exc_type as exc:
        return exc
    raise AssertionError(f"{func} did not raise {exc_type.__name__}")


def test_iio_reads_dht22_in_si_units() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        make_dht(Path(tmp), temp="-5300", humidity="45200")
        sensor = hw.IIOClimateSensor(Path(tmp), sleep=lambda _: None)
        assert sensor.read() == (-5.3, 45.2)


def test_iio_retries_transient_read_errors() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        device = make_dht(Path(tmp), temp="")
        sleeps: list[float] = []

        def sleep(seconds: float) -> None:
            sleeps.append(seconds)
            write_attrs(device, in_temp_input="21000")

        sensor = hw.IIOClimateSensor(Path(tmp), retry_delay_seconds=2.5, sleep=sleep)
        assert sensor.read() == (21.0, 45.2)
        assert sleeps == [2.5]


def test_iio_gives_up_on_implausible_values() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        make_dht(Path(tmp), humidity="123000")
        sleeps: list[float] = []
        sensor = hw.IIOClimateSensor(Path(tmp), attempts=3, sleep=sleeps.append)
        error = raises(hw.SensorError, sensor.read)
        assert "after 3 attempts" in str(error)
        assert len(sleeps) == 2


def test_iio_reports_missing_overlay() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        error = raises(hw.SensorError, hw.IIOClimateSensor(Path(tmp)).read)
        assert "dht11" in str(error)


def test_hwmon_rescales_current_to_configured_shunt() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        make_ina219_hwmon(Path(tmp), bus_mv="5012", curr_ma="4200", shunt_uohm="10000")
        voltage, current = hw.HwmonPowerSensor(Path(tmp), shunt_ohms=0.1).read()
        assert voltage == 5.012
        assert abs(current - 420.0) < 1e-9
        voltage, current = hw.HwmonPowerSensor(Path(tmp), shunt_ohms=None).read()
        assert current == 4200.0


def test_hwmon_read_errors_become_sensor_errors() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        device = make_ina219_hwmon(Path(tmp))
        (device / "curr1_input").unlink()
        raises(hw.SensorError, hw.HwmonPowerSensor(Path(tmp)).read)


def test_smbus_ina219_converts_registers() -> None:
    # 42 mV across 0.1 ohm = 420 mA; 1253 * 4 mV = 5.012 V with the CNVR bit set.
    bus = FakeBus({0x01: 4200, 0x02: (1253 << 3) | 0x2})
    sensor = hw.SMBusINA219(bus_number=1, address=0x41, shunt_ohms=0.1, open_bus=lambda n: bus)
    voltage, current = sensor.read()
    assert abs(voltage - 5.012) < 1e-9
    assert abs(current - 420.0) < 1e-9
    assert bus.reads == [(0x41, 0x01, 2), (0x41, 0x02, 2)]


def test_smbus_ina219_handles_negative_shunt_voltage() -> None:
    bus = FakeBus({0x01: 0xFF9C, 0x02: 0})
    _, current = hw.SMBusINA219(shunt_ohms=0.1, open_bus=lambda n: bus).read()
    assert abs(current - (-10.0)) < 1e-9


def test_smbus_ina219_reopens_bus_after_error() -> None:
    buses = [FakeBus({}, fail=True), FakeBus({0x01: 0, 0x02: 0})]
    opened: list[int] = []

    def open_bus(number: int) -> FakeBus:
        opened.append(number)
        return buses[len(opened) - 1]

    sensor = hw.SMBusINA219(bus_number=1, open_bus=open_bus)
    raises(hw.SensorError, sensor.read)
    assert buses[0].closed
    assert sensor.read() == (0.0, 0.0)
    assert opened == [1, 1]


def test_gpiod_tamper_switch_requests_pulled_up_input() -> None:
    gpiod = fake_gpiod(FakeValue.ACTIVE)
    switch = hw.GpiodTamperSwitch(chip="/dev/gpiochip0", line=17, gpiod_module=gpiod)
    assert switch.read() is True
    assert switch.read() is True
    assert len(gpiod.calls) == 1, "line request should be kept open between reads"
    call = gpiod.calls[0]
    assert call["path"] == "/dev/gpiochip0"
    assert call["consumer"] == "watchguard-monitor"
    settings = call["config"][17].kwargs
    assert settings == {"direction": FakeDirection.INPUT, "bias": FakeBias.PULL_UP}
    switch.close()
    assert gpiod.requests[0].released


def test_gpiod_tamper_switch_open_level() -> None:
    closed = hw.GpiodTamperSwitch(gpiod_module=fake_gpiod(FakeValue.INACTIVE))
    assert closed.read() is False
    inverted = hw.GpiodTamperSwitch(open_level="low", gpiod_module=fake_gpiod(FakeValue.INACTIVE))
    assert inverted.read() is True


def test_gpiod_errors_become_sensor_errors() -> None:
    gpiod = fake_gpiod(FakeValue.ACTIVE)

    def missing_chip(*args: Any, **kwargs: Any) -> None:
        raise FileNotFoundError(2, "No such file or directory", "/dev/gpiochip9")

    gpiod.request_lines = missing_chip
    raises(hw.SensorError, hw.GpiodTamperSwitch(chip="/dev/gpiochip9", gpiod_module=gpiod).read)


ALL_OUTPUT_LINES = [18, 22, 23, 24, 25, 27]
ALL_INACTIVE = dict.fromkeys(ALL_OUTPUT_LINES, FakeValue.INACTIVE)


def test_gpiod_outputs_request_documented_lines_inactive() -> None:
    gpiod = fake_gpiod(FakeValue.INACTIVE)
    outputs = hw.GpiodOutputs(active_low=["relay"], gpiod_module=gpiod)
    outputs.write({"fan": True, "led_blue": True, "relay": False})
    [call] = gpiod.calls
    assert call["path"] == "/dev/gpiochip0"
    assert call["consumer"] == "watchguard-monitor"
    config = call["config"]
    assert sorted(config) == ALL_OUTPUT_LINES
    assert config[27].kwargs == {
        "direction": FakeDirection.OUTPUT,
        "output_value": FakeValue.INACTIVE,
        "active_low": False,
    }
    assert config[25].kwargs["active_low"] is True
    assert gpiod.requests[0].writes == [{27: FakeValue.ACTIVE, 23: FakeValue.ACTIVE, 25: FakeValue.INACTIVE}]


def test_gpiod_outputs_close_drives_safe_state() -> None:
    gpiod = fake_gpiod(FakeValue.INACTIVE)
    outputs = hw.GpiodOutputs(gpiod_module=gpiod)
    outputs.write({"fan": True, "buzzer": True, "led_red": True, "relay": True})
    outputs.write({"buzzer": False})
    assert len(gpiod.calls) == 1, "lines should stay requested between writes"
    request = gpiod.requests[0]
    assert request.levels()[25] == FakeValue.ACTIVE
    outputs.close()
    assert request.writes[-1] == ALL_INACTIVE
    assert request.released
    outputs.close()


def test_gpiod_outputs_failed_write_goes_safe_and_retries() -> None:
    gpiod = fake_gpiod(FakeValue.INACTIVE)
    outputs = hw.GpiodOutputs(gpiod_module=gpiod)
    outputs.write({"relay": True, "fan": True})
    first = gpiod.requests[0]
    first.failing_writes = 1
    error = raises(hw.ActuatorError, outputs.write, {"relay": True})
    assert "/dev/gpiochip0" in str(error)
    assert first.levels() == ALL_INACTIVE, "safe state should be attempted after a failed write"
    assert first.released
    outputs.write({"fan": True})
    assert len(gpiod.calls) == 2, "the lines should be requested again"


def test_gpiod_outputs_missing_chip_is_an_actuator_error() -> None:
    gpiod = fake_gpiod(FakeValue.INACTIVE)

    def missing_chip(*args: Any, **kwargs: Any) -> None:
        raise FileNotFoundError(2, "No such file or directory", "/dev/gpiochip0")

    gpiod.request_lines = missing_chip
    outputs = hw.GpiodOutputs(gpiod_module=gpiod)
    raises(hw.ActuatorError, outputs.write, {"fan": True})
    outputs.close()


def test_gpiod_outputs_reject_bad_config() -> None:
    raises(ValueError, hw.GpiodOutputs, "/dev/gpiochip0", {"fan": 27, "siren": 5})
    raises(ValueError, hw.GpiodOutputs, "/dev/gpiochip0", {"fan": 27, "relay": 27})
    raises(ValueError, hw.GpiodOutputs, "/dev/gpiochip0", {"fan": 27}, ["relay"])
    outputs = hw.GpiodOutputs(gpiod_module=fake_gpiod(FakeValue.INACTIVE))
    raises(ValueError, outputs.write, {"fan": True, "siren": True})


def test_gpio_outputs_builder_overrides_and_skips_lines() -> None:
    hardware = {"actuators": {"lines": {"fan": 26, "relay": None}, "active_low": ["led_red"]}}
    outputs = hw.build_gpio_outputs(hardware)
    assert isinstance(outputs, hw.GpiodOutputs)
    gpiod = fake_gpiod(FakeValue.INACTIVE)
    outputs._gpiod = gpiod
    outputs.write({"fan": True, "relay": True})
    config = gpiod.calls[0]["config"]
    assert sorted(config) == [18, 22, 23, 24, 26]
    assert config[24].kwargs["active_low"] is True
    assert gpiod.requests[0].writes == [{26: FakeValue.ACTIVE}]


def test_builders_follow_config() -> None:
    hardware = {"power": {"driver": "smbus", "i2c_bus": 3, "i2c_address": "0x45", "shunt_ohms": 0.05}}
    sensor = hw.build_power_sensor(hardware)
    assert isinstance(sensor, hw.SMBusINA219)
    assert (sensor._bus_number, sensor._address, sensor._shunt_ohms) == (3, 0x45, 0.05)
    assert isinstance(hw.build_power_sensor({"power": {"driver": "hwmon"}}), hw.HwmonPowerSensor)
    raises(ValueError, hw.build_power_sensor, {"power": {"driver": "spi"}})
    raises(ValueError, hw.build_climate_sensor, {"climate": {"driver": "gpio-bitbang"}})
    raises(ValueError, hw.build_tamper_sensor, {"tamper": {"open_level": "sideways"}})


def test_raspberry_pi_backend_composes_reading() -> None:
    climate = SimpleNamespace(read=lambda: (23.456, 45.678))
    power = SimpleNamespace(read=lambda: (5.01234, 420.04))
    tamper = SimpleNamespace(read=lambda: True)
    backend = monitor.RaspberryPiBackend({}, climate=climate, power=power, tamper=tamper)
    assert backend.read() == monitor.SensorReading(
        temperature_celsius=23.46,
        humidity_percent=45.68,
        voltage=5.012,
        current_ma=420.0,
        power_mw=2105.4,
        tamper_open=True,
    )


def run_monitor(config: dict[str, Any], tmp: Path, iterations: int = 1) -> list[dict[str, Any]]:
    config_path = tmp / "watchguard.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        assert monitor.main(["--config", str(config_path), "--iterations", str(iterations)]) == 0
    return [json.loads(line) for line in out.getvalue().splitlines()]


def pi_config(sysfs: Path) -> dict[str, Any]:
    with (ROOT / "config/watchguard.example.json").open(encoding="utf-8") as fh:
        config = json.load(fh)
    config["simulation"]["enabled"] = False
    config["hardware"]["sysfs_root"] = str(sysfs)
    config["hardware"]["power"]["driver"] = "hwmon"
    return config


@contextlib.contextmanager
def installed_gpiod(module: SimpleNamespace) -> Iterator[SimpleNamespace]:
    saved = sys.modules.get("gpiod")
    sys.modules["gpiod"] = module  # type: ignore[assignment]
    try:
        yield module
    finally:
        if saved is None:
            sys.modules.pop("gpiod", None)
        else:
            sys.modules["gpiod"] = saved


def requests_by_line(gpiod: SimpleNamespace) -> dict[int, FakeRequest]:
    return {line: request for call, request in zip(gpiod.calls, gpiod.requests) for line in call["config"]}


def test_monitor_reads_hardware_end_to_end() -> None:
    with installed_gpiod(fake_gpiod(FakeValue.ACTIVE)) as gpiod, tempfile.TemporaryDirectory() as tmp:
        sysfs = Path(tmp) / "sys"
        make_dht(sysfs, temp="36000", humidity="50000")
        make_ina219_hwmon(sysfs, bus_mv="5050", curr_ma="4100", shunt_uohm="10000")
        [event] = run_monitor(pi_config(sysfs), Path(tmp))
    assert event["reading"] == {
        "temperature_celsius": 36.0,
        "humidity_percent": 50.0,
        "voltage": 5.05,
        "current_ma": 410.0,
        "power_mw": 2070.5,
        "tamper_open": True,
    }
    assert event["alerts"] == ["tamper"]
    assert event["actuators"]["fan_on"] is True
    assert event["actuators"]["buzzer_on"] is True
    lines = requests_by_line(gpiod)
    assert lines[17].released, "monitor should release the reed switch line on exit"
    outputs = lines[27]
    # Fan (27), buzzer (18) and red LED (24) on; green (22), blue (23) and relay (25) off.
    assert outputs.writes[0] == {
        27: FakeValue.ACTIVE,
        18: FakeValue.ACTIVE,
        25: FakeValue.INACTIVE,
        22: FakeValue.INACTIVE,
        23: FakeValue.INACTIVE,
        24: FakeValue.ACTIVE,
    }
    assert outputs.writes[-1] == ALL_INACTIVE, "monitor should de-energize the actuators on exit"
    assert outputs.released


def test_monitor_reports_sensor_errors_without_crashing() -> None:
    with installed_gpiod(fake_gpiod(FakeValue.INACTIVE)) as gpiod, tempfile.TemporaryDirectory() as tmp:
        sysfs = Path(tmp) / "empty-sys"
        sysfs.mkdir()
        [event] = run_monitor(pi_config(sysfs), Path(tmp))
    assert "reading" not in event
    assert "dht11" in event["error"]
    assert event["actuators"]["fan_on"] is True
    assert event["actuators"]["relay_on"] is False
    outputs = requests_by_line(gpiod)[27]
    assert outputs.writes[0][27] == FakeValue.ACTIVE
    assert outputs.writes[-1] == ALL_INACTIVE


def test_monitor_reports_missing_gpio_chip_and_keeps_running() -> None:
    gpiod = fake_gpiod(FakeValue.INACTIVE)

    def missing_chip(*args: Any, **kwargs: Any) -> None:
        raise FileNotFoundError(2, "No such file or directory", "/dev/gpiochip0")

    gpiod.request_lines = missing_chip
    with installed_gpiod(gpiod), tempfile.TemporaryDirectory() as tmp:
        sysfs = Path(tmp) / "empty-sys"
        sysfs.mkdir()
        config = pi_config(sysfs)
        config["sample_interval_seconds"] = 0
        events = run_monitor(config, Path(tmp), iterations=2)
    assert len(events) == 2
    assert all("/dev/gpiochip0" in event["actuator_error"] for event in events)
    assert all("actuators" not in event for event in events)


def test_monitor_goes_safe_on_sigterm() -> None:
    def sigterm_instead_of_sleeping(seconds: float) -> None:
        assert signal.getsignal(signal.SIGTERM) is not signal.SIG_DFL, "SIGTERM would kill the test run"
        signal.raise_signal(signal.SIGTERM)

    handler_before = signal.getsignal(signal.SIGTERM)
    with installed_gpiod(fake_gpiod(FakeValue.ACTIVE)) as gpiod, tempfile.TemporaryDirectory() as tmp:
        sysfs = Path(tmp) / "sys"
        make_dht(sysfs, temp="43000", humidity="80000")
        make_ina219_hwmon(sysfs, bus_mv="4600", curr_ma="10000", shunt_uohm="10000")
        config_path = Path(tmp) / "watchguard.json"
        config_path.write_text(json.dumps(pi_config(sysfs)), encoding="utf-8")
        saved_sleep = monitor.time.sleep
        monitor.time.sleep = sigterm_instead_of_sleeping  # type: ignore[assignment]
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                error = raises(SystemExit, monitor.main, ["--config", str(config_path)])
        finally:
            monitor.time.sleep = saved_sleep  # type: ignore[assignment]
    assert isinstance(error, SystemExit) and error.code == 0
    assert signal.getsignal(signal.SIGTERM) is handler_before, "the SIGTERM handler should be restored"
    lines = requests_by_line(gpiod)
    outputs = lines[27]
    assert outputs.writes[0][27] == FakeValue.ACTIVE
    assert outputs.writes[-1] == ALL_INACTIVE
    assert outputs.released and lines[17].released


def main() -> int:
    tests = [value for name, value in globals().items() if name.startswith("test_") and callable(value)]
    for test in tests:
        test()
        print(f"ok - {test.__name__}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
