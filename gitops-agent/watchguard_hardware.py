"""Raspberry Pi sensor drivers for Podman Watchguard.

Every sensor is read through a standard Linux interface, following the wiring
in docs/proyecto.md:

- DHT22 (GPIO4): kernel ``dht11`` IIO driver, which also handles the DHT22.
  Enabled with ``dtoverlay=dht11,gpiopin=4`` and read from
  ``/sys/bus/iio/devices/iio:deviceN/in_{temp,humidityrelative}_input``.
- INA219 (I2C bus 1, 0x40): either the kernel ``ina2xx`` hwmon driver under
  ``/sys/class/hwmon`` or raw register reads over ``/dev/i2c-1`` with smbus2.
- Reed switch (GPIO17 to GND, internal pull-up): GPIO character device
  through libgpiod v2 (the ``gpiod`` Python bindings).

smbus2 and gpiod are imported lazily, so simulation mode and the unit tests
only need the standard library. Each driver takes its I/O dependency as a
constructor argument, which is how the tests substitute fakes.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, ClassVar, Protocol


class SensorError(RuntimeError):
    """A hardware sensor could not be read or returned an implausible value."""


class ClimateSensor(Protocol):
    def read(self) -> tuple[float, float]:
        """Return (temperature in degrees Celsius, relative humidity in %)."""


class PowerSensor(Protocol):
    def read(self) -> tuple[float, float]:
        """Return (bus voltage in V, current in mA)."""


class TamperSensor(Protocol):
    def read(self) -> bool:
        """Return True when the enclosure is open."""


def _read_int(path: Path) -> int:
    return int(path.read_text(encoding="ascii").strip())


def find_sysfs_device(base: Path, pattern: str, name: str) -> Path:
    """Return the first device under ``base`` whose ``name`` attribute matches.

    Device-tree instantiated IIO devices may report ``dht11@4`` rather than
    ``dht11``, so a ``name@unit-address`` suffix also matches.
    """
    for candidate in sorted(base.glob(pattern)):
        try:
            value = (candidate / "name").read_text(encoding="ascii").strip()
        except OSError:
            continue
        if value == name or value.startswith(name + "@"):
            return candidate
    raise SensorError(f"no device named {name!r} under {base}")


class IIOClimateSensor:
    """DHT22 read through the kernel dht11 IIO driver.

    The driver reports milli-degrees Celsius and milli-percent relative
    humidity. Single-wire timing errors surface as EIO/ETIMEDOUT on read, which
    is common on this sensor, so failed reads are retried. The DHT22 needs
    about two seconds between conversions, hence the default retry delay.
    """

    TEMPERATURE_RANGE = (-40.0, 80.0)
    HUMIDITY_RANGE = (0.0, 100.0)

    def __init__(
        self,
        sysfs_root: Path,
        device_name: str = "dht11",
        attempts: int = 3,
        retry_delay_seconds: float = 2.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if attempts < 1:
            raise ValueError("attempts must be at least 1")
        self._devices = sysfs_root / "bus" / "iio" / "devices"
        self._device_name = device_name
        self._attempts = attempts
        self._retry_delay = retry_delay_seconds
        self._sleep = sleep

    def read(self) -> tuple[float, float]:
        device = find_sysfs_device(self._devices, "iio:device*", self._device_name)
        last_error: Exception | None = None
        for attempt in range(1, self._attempts + 1):
            try:
                temperature = _read_int(device / "in_temp_input") / 1000
                humidity = _read_int(device / "in_humidityrelative_input") / 1000
                self._check_range(temperature, humidity)
                return temperature, humidity
            except (OSError, ValueError, SensorError) as exc:
                last_error = exc
                if attempt < self._attempts:
                    self._sleep(self._retry_delay)
        raise SensorError(
            f"DHT22 read via {device} failed after {self._attempts} attempts: {last_error}"
        )

    def _check_range(self, temperature: float, humidity: float) -> None:
        low, high = self.TEMPERATURE_RANGE
        if not low <= temperature <= high:
            raise SensorError(f"implausible DHT22 temperature {temperature} C")
        low, high = self.HUMIDITY_RANGE
        if not low <= humidity <= high:
            raise SensorError(f"implausible DHT22 humidity {humidity} %")


class HwmonPowerSensor:
    """INA219 read through the kernel ina2xx hwmon driver.

    ``in1_input`` is the bus voltage in mV and ``curr1_input`` the current in
    mA, computed by the driver from its ``shunt_resistor`` attribute (micro-ohm,
    10000 by default). Breakout boards usually carry a 0.1 ohm shunt, so when
    ``shunt_ohms`` is given the current is rescaled from the driver's shunt
    value to the configured one instead of trusting the driver default.
    """

    def __init__(self, sysfs_root: Path, device_name: str = "ina219", shunt_ohms: float | None = None) -> None:
        if shunt_ohms is not None and shunt_ohms <= 0:
            raise ValueError("shunt_ohms must be positive")
        self._hwmon = sysfs_root / "class" / "hwmon"
        self._device_name = device_name
        self._shunt_ohms = shunt_ohms

    def read(self) -> tuple[float, float]:
        device = find_sysfs_device(self._hwmon, "hwmon*", self._device_name)
        try:
            voltage = _read_int(device / "in1_input") / 1000
            current_ma = float(_read_int(device / "curr1_input"))
            if self._shunt_ohms is not None:
                driver_shunt_uohm = _read_int(device / "shunt_resistor")
                current_ma *= driver_shunt_uohm / (self._shunt_ohms * 1_000_000)
        except (OSError, ValueError) as exc:
            raise SensorError(f"INA219 read via {device} failed: {exc}") from exc
        return voltage, current_ma


class I2CBus(Protocol):
    def read_i2c_block_data(self, i2c_addr: int, register: int, length: int) -> list[int]: ...

    def close(self) -> None: ...


def _open_smbus(bus_number: int) -> I2CBus:
    from smbus2 import SMBus  # optional dependency, only needed on the Pi

    return SMBus(bus_number)


class SMBusINA219:
    """INA219 read directly over I2C with smbus2, without a kernel driver.

    Only the shunt-voltage (0x01) and bus-voltage (0x02) registers are read;
    nothing is written to the chip, so it keeps its power-on configuration
    (32 V range, +/-320 mV shunt range, continuous conversion). Current is
    derived from the shunt voltage and the configured shunt resistance, which
    avoids depending on the calibration register.

    Do not combine with the hwmon driver: if ina2xx is bound to the address,
    the bus access fails with EBUSY.
    """

    REG_SHUNT_VOLTAGE = 0x01
    REG_BUS_VOLTAGE = 0x02
    SHUNT_LSB_VOLTS = 10e-6
    BUS_LSB_VOLTS = 4e-3

    def __init__(
        self,
        bus_number: int = 1,
        address: int = 0x40,
        shunt_ohms: float = 0.1,
        open_bus: Callable[[int], I2CBus] = _open_smbus,
    ) -> None:
        if shunt_ohms <= 0:
            raise ValueError("shunt_ohms must be positive")
        self._bus_number = bus_number
        self._address = address
        self._shunt_ohms = shunt_ohms
        self._open_bus = open_bus
        self._bus: I2CBus | None = None

    def read(self) -> tuple[float, float]:
        try:
            if self._bus is None:
                self._bus = self._open_bus(self._bus_number)
            shunt_raw = self._read_register(self.REG_SHUNT_VOLTAGE)
            bus_raw = self._read_register(self.REG_BUS_VOLTAGE)
        except OSError as exc:
            self.close()
            raise SensorError(
                f"INA219 read on i2c-{self._bus_number} at {self._address:#04x} failed: {exc}"
            ) from exc
        if shunt_raw & 0x8000:
            shunt_raw -= 0x10000
        current_ma = shunt_raw * self.SHUNT_LSB_VOLTS / self._shunt_ohms * 1000
        voltage = (bus_raw >> 3) * self.BUS_LSB_VOLTS
        return voltage, current_ma

    def close(self) -> None:
        if self._bus is not None:
            bus, self._bus = self._bus, None
            bus.close()

    def _read_register(self, register: int) -> int:
        assert self._bus is not None
        msb, lsb = self._bus.read_i2c_block_data(self._address, register, 2)
        return (msb << 8) | lsb


class GpiodTamperSwitch:
    """Reed switch read through the GPIO character device with libgpiod v2.

    The documented wiring closes the switch to GND with the magnet in place and
    uses the SoC's internal pull-up, so the line reads high when the enclosure
    is open. The line request is kept open between reads and released by
    ``close()``.
    """

    BIASES: ClassVar[dict[str, str]] = {
        "pull-up": "PULL_UP",
        "pull-down": "PULL_DOWN",
        "disabled": "DISABLED",
        "as-is": "AS_IS",
    }

    def __init__(
        self,
        chip: str = "/dev/gpiochip0",
        line: int = 17,
        bias: str = "pull-up",
        open_level: str = "high",
        gpiod_module: Any = None,
    ) -> None:
        if bias not in self.BIASES:
            raise ValueError(f"bias must be one of {sorted(self.BIASES)}")
        if open_level not in ("high", "low"):
            raise ValueError("open_level must be 'high' or 'low'")
        self._chip = chip
        self._line = line
        self._bias = bias
        self._open_when_high = open_level == "high"
        self._gpiod = gpiod_module
        self._request: Any = None

    def read(self) -> bool:
        try:
            if self._request is None:
                self._request = self._request_line()
            value = self._request.get_value(self._line)
        except OSError as exc:
            self.close()
            raise SensorError(f"reed switch read on {self._chip} line {self._line} failed: {exc}") from exc
        high = value == self._gpiod.line.Value.ACTIVE
        return high if self._open_when_high else not high

    def close(self) -> None:
        if self._request is not None:
            request, self._request = self._request, None
            request.release()

    def _request_line(self) -> Any:
        if self._gpiod is None:
            import gpiod  # optional dependency (libgpiod v2 bindings), only needed on the Pi

            self._gpiod = gpiod
        line = self._gpiod.line
        settings = self._gpiod.LineSettings(
            direction=line.Direction.INPUT,
            bias=getattr(line.Bias, self.BIASES[self._bias]),
        )
        return self._gpiod.request_lines(
            self._chip,
            consumer="watchguard-monitor",
            config={self._line: settings},
        )


def _parse_address(value: Any) -> int:
    return int(value, 0) if isinstance(value, str) else int(value)


def build_climate_sensor(hardware: dict[str, Any]) -> ClimateSensor:
    options = hardware.get("climate", {})
    driver = options.get("driver", "iio")
    if driver != "iio":
        raise ValueError(f"unsupported climate driver {driver!r} (supported: iio)")
    return IIOClimateSensor(
        Path(hardware.get("sysfs_root", "/sys")),
        device_name=options.get("iio_name", "dht11"),
        attempts=int(options.get("attempts", 3)),
        retry_delay_seconds=float(options.get("retry_delay_seconds", 2.0)),
    )


def build_power_sensor(hardware: dict[str, Any]) -> PowerSensor:
    options = hardware.get("power", {})
    driver = options.get("driver", "smbus")
    shunt_ohms = options.get("shunt_ohms", 0.1)
    if driver == "smbus":
        return SMBusINA219(
            bus_number=int(options.get("i2c_bus", 1)),
            address=_parse_address(options.get("i2c_address", 0x40)),
            shunt_ohms=float(shunt_ohms),
        )
    if driver == "hwmon":
        return HwmonPowerSensor(
            Path(hardware.get("sysfs_root", "/sys")),
            device_name=options.get("hwmon_name", "ina219"),
            shunt_ohms=None if shunt_ohms is None else float(shunt_ohms),
        )
    raise ValueError(f"unsupported power driver {driver!r} (supported: smbus, hwmon)")


def build_tamper_sensor(hardware: dict[str, Any]) -> TamperSensor:
    options = hardware.get("tamper", {})
    return GpiodTamperSwitch(
        chip=options.get("gpio_chip", "/dev/gpiochip0"),
        line=int(options.get("gpio_line", 17)),
        bias=options.get("bias", "pull-up"),
        open_level=options.get("open_level", "high"),
    )
