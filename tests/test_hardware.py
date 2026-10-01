#!/usr/bin/env python3
"""Raspberry Pi backend tests against fake sysfs trees, a fake I2C bus and a fake gpiod."""

from __future__ import annotations

import contextlib
import enum
import io
import json
import sys
import tempfile
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

    def get_value(self, offset: int) -> FakeValue:
        return self.value

    def release(self) -> None:
        self.released = True


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


def run_monitor(config: dict[str, Any], tmp: Path) -> list[dict[str, Any]]:
    config_path = tmp / "watchguard.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        assert monitor.main(["--config", str(config_path), "--iterations", "1"]) == 0
    return [json.loads(line) for line in out.getvalue().splitlines()]


def pi_config(sysfs: Path) -> dict[str, Any]:
    with (ROOT / "config/watchguard.example.json").open(encoding="utf-8") as fh:
        config = json.load(fh)
    config["simulation"]["enabled"] = False
    config["hardware"]["sysfs_root"] = str(sysfs)
    config["hardware"]["power"]["driver"] = "hwmon"
    return config


def test_monitor_reads_hardware_end_to_end() -> None:
    gpiod = fake_gpiod(FakeValue.ACTIVE)
    saved = sys.modules.get("gpiod")
    sys.modules["gpiod"] = gpiod  # type: ignore[assignment]
    try:
        with tempfile.TemporaryDirectory() as tmp:
            sysfs = Path(tmp) / "sys"
            make_dht(sysfs, temp="36000", humidity="50000")
            make_ina219_hwmon(sysfs, bus_mv="5050", curr_ma="4100", shunt_uohm="10000")
            [event] = run_monitor(pi_config(sysfs), Path(tmp))
    finally:
        if saved is None:
            sys.modules.pop("gpiod", None)
        else:
            sys.modules["gpiod"] = saved
    assert event["reading"] == {
        "temperature_celsius": 36.0,
        "humidity_percent": 50.0,
        "voltage": 5.05,
        "current_ma": 410.0,
        "power_mw": 2070.5,
        "tamper_open": True,
    }
    assert event["actuators"]["fan_on"] is True
    assert event["actuators"]["buzzer_on"] is True
    assert gpiod.requests[0].released, "monitor should release the GPIO line on exit"


def test_monitor_reports_sensor_errors_without_crashing() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        sysfs = Path(tmp) / "empty-sys"
        sysfs.mkdir()
        [event] = run_monitor(pi_config(sysfs), Path(tmp))
    assert "reading" not in event
    assert "dht11" in event["error"]


def main() -> int:
    tests = [value for name, value in globals().items() if name.startswith("test_") and callable(value)]
    for test in tests:
        test()
        print(f"ok - {test.__name__}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
