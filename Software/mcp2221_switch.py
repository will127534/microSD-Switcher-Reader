#!/usr/bin/env python3
"""Control GP2/GP3 switches on a Microchip MCP2221/MCP2221A."""

from __future__ import annotations

import argparse
import ctypes
import ctypes.util
import sys
import time
from dataclasses import dataclass


REPORT_SIZE = 64
WRITE_REPORT_SIZE = REPORT_SIZE + 1

CMD_SET_GPIO_OUTPUT_VALUES = 0x50
CMD_GET_GPIO_VALUES = 0x51
CMD_SET_SRAM_SETTINGS = 0x60
CMD_GET_SRAM_SETTINGS = 0x61
CMD_RESET_CHIP = 0x70
CMD_READ_FLASH_DATA = 0xB0
CMD_WRITE_FLASH_DATA = 0xB1

GPIO_DIRECTION_OUTPUT = 0
GPIO_DIRECTION_INPUT = 1
GPIO_DESIGNATION_GPIO = 0
GPIO_DESIGNATION_DEDICATED = 1
GPIO_DESIGNATION_ALT0 = 2
GPIO_DESIGNATION_ALT1 = 3
GPIO_DESIGNATION_ALT2 = 4

PIN_UART_RX_LED = 0
PIN_UART_TX_LED = 1
PIN_SD_SWITCH = 2
PIN_RESET_SWITCH = 3

SD_ROUTE_DEVICE = False
SD_ROUTE_READER = True


class MCP2221Error(RuntimeError):
    """Raised when MCP2221 communication or configuration fails."""


@dataclass(frozen=True)
class DeviceInfo:
    path: str
    vendor_id: int
    product_id: int
    serial_number: str
    manufacturer_string: str
    product_string: str
    interface_number: int


def state_text(value: bool) -> str:
    return "on" if value else "off"


def level_text(value: bool) -> str:
    return "high" if value else "low"


def sd_route_text(value: bool) -> str:
    return "reader (high)" if value else "device (low)"


def parse_hex_int(value: str) -> int:
    return int(value, 0)


class HidDeviceInfo(ctypes.Structure):
    pass


HidDeviceInfoPtr = ctypes.POINTER(HidDeviceInfo)
HidDeviceInfo._fields_ = [
    ("path", ctypes.c_char_p),
    ("vendor_id", ctypes.c_ushort),
    ("product_id", ctypes.c_ushort),
    ("serial_number", ctypes.c_wchar_p),
    ("release_number", ctypes.c_ushort),
    ("manufacturer_string", ctypes.c_wchar_p),
    ("product_string", ctypes.c_wchar_p),
    ("usage_page", ctypes.c_ushort),
    ("usage", ctypes.c_ushort),
    ("interface_number", ctypes.c_int),
    ("next", HidDeviceInfoPtr),
    ("bus_type", ctypes.c_int),
]


class HidApi:
    def __init__(self) -> None:
        library_name = (
            ctypes.util.find_library("hidapi-hidraw")
            or ctypes.util.find_library("hidapi-libusb")
            or "libhidapi-hidraw.so.0"
        )
        self.lib = ctypes.CDLL(library_name)

        self.lib.hid_init.argtypes = []
        self.lib.hid_init.restype = ctypes.c_int

        self.lib.hid_exit.argtypes = []
        self.lib.hid_exit.restype = ctypes.c_int

        self.lib.hid_error.argtypes = [ctypes.c_void_p]
        self.lib.hid_error.restype = ctypes.c_wchar_p

        self.lib.hid_enumerate.argtypes = [ctypes.c_ushort, ctypes.c_ushort]
        self.lib.hid_enumerate.restype = HidDeviceInfoPtr

        self.lib.hid_free_enumeration.argtypes = [HidDeviceInfoPtr]
        self.lib.hid_free_enumeration.restype = None

        self.lib.hid_open_path.argtypes = [ctypes.c_char_p]
        self.lib.hid_open_path.restype = ctypes.c_void_p

        self.lib.hid_close.argtypes = [ctypes.c_void_p]
        self.lib.hid_close.restype = None

        self.lib.hid_write.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_ubyte),
            ctypes.c_size_t,
        ]
        self.lib.hid_write.restype = ctypes.c_int

        self.lib.hid_read_timeout.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_ubyte),
            ctypes.c_size_t,
            ctypes.c_int,
        ]
        self.lib.hid_read_timeout.restype = ctypes.c_int

        self.lib.hid_set_nonblocking.argtypes = [ctypes.c_void_p, ctypes.c_int]
        self.lib.hid_set_nonblocking.restype = ctypes.c_int

        if self.lib.hid_init() != 0:
            raise MCP2221Error("hidapi initialization failed")

    def close(self) -> None:
        self.lib.hid_exit()

    def error_text(self, handle: ctypes.c_void_p | None = None) -> str:
        text = self.lib.hid_error(handle)
        return text or "unknown hidapi error"

    def enumerate(self) -> list[DeviceInfo]:
        devices: list[DeviceInfo] = []
        head = self.lib.hid_enumerate(0, 0)
        current = head
        try:
            while current:
                entry = current.contents
                devices.append(
                    DeviceInfo(
                        path=(entry.path or b"").decode("utf-8", errors="replace"),
                        vendor_id=entry.vendor_id,
                        product_id=entry.product_id,
                        serial_number=entry.serial_number or "",
                        manufacturer_string=entry.manufacturer_string or "",
                        product_string=entry.product_string or "",
                        interface_number=entry.interface_number,
                    )
                )
                current = entry.next
        finally:
            if head:
                self.lib.hid_free_enumeration(head)
        return devices

    def open_path(self, path: str) -> ctypes.c_void_p:
        handle = self.lib.hid_open_path(path.encode("utf-8"))
        if not handle:
            raise MCP2221Error(f"failed to open {path}: {self.error_text(None)}")
        if self.lib.hid_set_nonblocking(handle, 0) != 0:
            error = self.error_text(handle)
            self.lib.hid_close(handle)
            raise MCP2221Error(f"failed to configure blocking I/O: {error}")
        return handle


def describe_pin_mode(setting: int) -> str:
    designation = setting & 0x07
    direction = (setting >> 3) & 0x01
    output_value = (setting >> 4) & 0x01

    if designation == GPIO_DESIGNATION_GPIO:
        if direction == GPIO_DIRECTION_OUTPUT:
            return f"gpio-output ({state_text(bool(output_value))})"
        return "gpio-input"

    if designation == GPIO_DESIGNATION_DEDICATED:
        return "dedicated"

    if designation == GPIO_DESIGNATION_ALT0:
        return "alternate-0"

    if designation == GPIO_DESIGNATION_ALT1:
        return "alternate-1"

    if designation == GPIO_DESIGNATION_ALT2:
        return "alternate-2"

    return f"reserved ({designation})"


def encode_pin_setting(designation: int, direction: int, output_value: bool) -> int:
    return (designation & 0x07) | ((direction & 0x01) << 3) | (int(output_value) << 4)


class MCP2221:
    def __init__(self, hidapi: HidApi, path: str, timeout_ms: int = 1000) -> None:
        self.hidapi = hidapi
        self.handle = hidapi.open_path(path)
        self.timeout_ms = timeout_ms

    def close(self) -> None:
        if self.handle:
            self.hidapi.lib.hid_close(self.handle)
            self.handle = None

    def __enter__(self) -> "MCP2221":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def transact(
        self,
        payload: bytes,
        expect_response: bool = True,
        expected_command: int | None = None,
    ) -> bytes:
        if len(payload) != REPORT_SIZE:
            raise ValueError("MCP2221 reports must be exactly 64 bytes")

        outgoing = (ctypes.c_ubyte * WRITE_REPORT_SIZE)()
        outgoing[0] = 0
        for index, value in enumerate(payload, start=1):
            outgoing[index] = value

        written = self.hidapi.lib.hid_write(self.handle, outgoing, WRITE_REPORT_SIZE)
        if written < 0:
            raise MCP2221Error(f"hid_write failed: {self.hidapi.error_text(self.handle)}")

        if not expect_response:
            return b""

        # The MCP2221 can leave a previous command response queued briefly across
        # rapid reconnects/open-close cycles. Read until the expected response
        # arrives so follow-up sessions do not fail on a stale packet.
        for _ in range(4):
            incoming = (ctypes.c_ubyte * REPORT_SIZE)()
            received = self.hidapi.lib.hid_read_timeout(
                self.handle, incoming, REPORT_SIZE, self.timeout_ms
            )
            if received < 0:
                raise MCP2221Error(
                    f"hid_read_timeout failed: {self.hidapi.error_text(self.handle)}"
                )
            if received == 0:
                raise MCP2221Error("timed out waiting for MCP2221 response")

            reply = bytes(incoming[:received])
            if len(reply) < REPORT_SIZE:
                reply += bytes(REPORT_SIZE - len(reply))

            if expected_command is None or reply[0] == expected_command:
                return reply

        raise MCP2221Error(
            f"timed out waiting for command 0x{expected_command:02x} response"
        )

    def get_sram_settings(self) -> bytes:
        request = bytearray(REPORT_SIZE)
        request[0] = CMD_GET_SRAM_SETTINGS
        response = self.transact(bytes(request), expected_command=CMD_GET_SRAM_SETTINGS)
        if response[0] != CMD_GET_SRAM_SETTINGS or response[1] != 0x00:
            raise MCP2221Error(f"unexpected Get SRAM Settings response: {response[:8].hex()}")
        return response

    def reset_chip(self) -> None:
        request = bytearray(REPORT_SIZE)
        request[0] = CMD_RESET_CHIP
        request[1] = 0xAB
        request[2] = 0xCD
        request[3] = 0xEF
        self.transact(bytes(request), expect_response=False)

    def get_flash_chip_settings(self) -> bytes:
        request = bytearray(REPORT_SIZE)
        request[0] = CMD_READ_FLASH_DATA
        request[1] = 0x00
        response = self.transact(bytes(request), expected_command=CMD_READ_FLASH_DATA)
        if response[0] != CMD_READ_FLASH_DATA or response[1] != 0x00:
            raise MCP2221Error(f"unexpected Read Flash Data response: {response[:8].hex()}")
        if response[2] < 4:
            raise MCP2221Error(f"invalid chip settings length in flash response: {response[2]}")
        return response[4:8]

    def get_flash_gpio_settings(self) -> bytes:
        request = bytearray(REPORT_SIZE)
        request[0] = CMD_READ_FLASH_DATA
        request[1] = 0x01
        response = self.transact(bytes(request), expected_command=CMD_READ_FLASH_DATA)
        if response[0] != CMD_READ_FLASH_DATA or response[1] != 0x00:
            raise MCP2221Error(f"unexpected Read Flash Data response: {response[:8].hex()}")
        if response[2] < 4:
            raise MCP2221Error(f"invalid GP settings length in flash response: {response[2]}")
        return response[4:8]

    def get_gpio_values(self) -> bytes:
        request = bytearray(REPORT_SIZE)
        request[0] = CMD_GET_GPIO_VALUES
        response = self.transact(bytes(request), expected_command=CMD_GET_GPIO_VALUES)
        if response[0] != CMD_GET_GPIO_VALUES or response[1] != 0x00:
            raise MCP2221Error(f"unexpected Get GPIO Values response: {response[:8].hex()}")
        return response

    def set_sram_gpio_settings(self, gp_settings: list[int]) -> None:
        if len(gp_settings) != 4:
            raise ValueError("expected four GP settings bytes")

        request = bytearray(REPORT_SIZE)
        request[0] = CMD_SET_SRAM_SETTINGS
        request[7] = 0x80
        request[8:12] = bytes(gp_settings)

        response = self.transact(bytes(request), expected_command=CMD_SET_SRAM_SETTINGS)
        if response[0] != CMD_SET_SRAM_SETTINGS or response[1] != 0x00:
            raise MCP2221Error(f"failed to update SRAM GPIO settings: {response[:8].hex()}")

    def set_flash_gpio_settings(self, gp_settings: list[int]) -> None:
        if len(gp_settings) != 4:
            raise ValueError("expected four GP settings bytes")

        request = bytearray(REPORT_SIZE)
        request[0] = CMD_WRITE_FLASH_DATA
        request[1] = 0x01
        request[2:6] = bytes(gp_settings)

        response = self.transact(bytes(request), expected_command=CMD_WRITE_FLASH_DATA)
        if response[0] != CMD_WRITE_FLASH_DATA:
            raise MCP2221Error(f"unexpected Write Flash Data response: {response[:8].hex()}")
        if response[1] == 0x00:
            return
        if response[1] == 0x02:
            raise MCP2221Error("flash write command is not supported by this device")
        if response[1] == 0x03:
            raise MCP2221Error("flash write command was rejected by the device")
        raise MCP2221Error(f"failed to update flash GPIO settings: {response[:8].hex()}")

    def set_flash_chip_settings(self, chip_settings: list[int]) -> None:
        if len(chip_settings) != 4:
            raise ValueError("expected four chip settings bytes")

        request = bytearray(REPORT_SIZE)
        request[0] = CMD_WRITE_FLASH_DATA
        request[1] = 0x00
        request[2:6] = bytes(chip_settings)

        response = self.transact(bytes(request), expected_command=CMD_WRITE_FLASH_DATA)
        if response[0] != CMD_WRITE_FLASH_DATA:
            raise MCP2221Error(f"unexpected Write Flash Data response: {response[:8].hex()}")
        if response[1] == 0x00:
            return
        if response[1] == 0x02:
            raise MCP2221Error("flash write command is not supported by this device")
        if response[1] == 0x03:
            raise MCP2221Error("flash write command was rejected by the device")
        raise MCP2221Error(f"failed to update flash chip settings: {response[:8].hex()}")

    def set_gpio_outputs(self, states: dict[int, bool]) -> None:
        request = bytearray(REPORT_SIZE)
        request[0] = CMD_SET_GPIO_OUTPUT_VALUES

        for pin, state in states.items():
            base = 2 + (pin * 4)
            request[base] = 0x01
            request[base + 1] = 0x01 if state else 0x00
            request[base + 2] = 0x01
            request[base + 3] = 0x00

        response = self.transact(bytes(request), expected_command=CMD_SET_GPIO_OUTPUT_VALUES)
        if response[0] != CMD_SET_GPIO_OUTPUT_VALUES or response[1] != 0x00:
            raise MCP2221Error(f"failed to set GPIO outputs: {response[:8].hex()}")

        for pin in states:
            base = 2 + (pin * 4)
            if 0xEE in response[base : base + 4]:
                raise MCP2221Error(
                    f"GP{pin} is not configured for GPIO operation; runtime configuration failed"
                )

    def pin_setting(self, sram: bytes, pin: int) -> int:
        return sram[22 + pin]

    def ensure_pin_outputs(self, requested_states: dict[int, bool]) -> dict[int, bool]:
        sram = self.get_sram_settings()

        gp_settings = [
            self.pin_setting(sram, 0),
            self.pin_setting(sram, 1),
            self.pin_setting(sram, 2),
            self.pin_setting(sram, 3),
        ]

        if not requested_states:
            raise MCP2221Error("no GPIO state requested")

        target_states: dict[int, bool] = {}
        for pin, requested_state in requested_states.items():
            current_setting = gp_settings[pin]
            is_gpio_output = (current_setting & 0x0F) == 0x00

            target_states[pin] = requested_state

            if not is_gpio_output:
                gp_settings[pin] = (
                    GPIO_DESIGNATION_GPIO
                    | (GPIO_DIRECTION_OUTPUT << 3)
                    | (int(target_states[pin]) << 4)
                )

        if gp_settings[2] != self.pin_setting(sram, 2) or gp_settings[3] != self.pin_setting(sram, 3):
            self.set_sram_gpio_settings(gp_settings)

        self.set_gpio_outputs(target_states)
        return target_states

    def pulse_pin(
        self,
        pin: int,
        duration_seconds: float,
        active_state: bool = True,
    ) -> None:
        if duration_seconds <= 0:
            raise MCP2221Error("pulse duration must be greater than zero")

        inactive_state = not active_state
        self.ensure_pin_outputs({pin: inactive_state})
        self.set_gpio_outputs({pin: active_state})
        try:
            time.sleep(duration_seconds)
        finally:
            self.set_gpio_outputs({pin: inactive_state})

    def switch_status(self) -> dict[str, str]:
        sram = self.get_sram_settings()
        gpio = self.get_gpio_values()

        status: dict[str, str] = {}
        for name, pin in (("sd", PIN_SD_SWITCH), ("reset", PIN_RESET_SWITCH)):
            setting = self.pin_setting(sram, pin)
            designation = setting & 0x07
            direction = (setting >> 3) & 0x01

            if designation == GPIO_DESIGNATION_GPIO and direction == GPIO_DIRECTION_OUTPUT:
                value = gpio[2 + (pin * 2)]
                status[name] = (
                    sd_route_text(bool(value))
                    if pin == PIN_SD_SWITCH
                    else level_text(bool(value))
                )
            else:
                status[name] = f"not-gpio-output ({describe_pin_mode(setting)})"

        status["gp0"] = describe_pin_mode(self.pin_setting(sram, 0))
        status["gp1"] = describe_pin_mode(self.pin_setting(sram, 1))
        return status


def match_devices(
    devices: list[DeviceInfo],
    path: str | None,
    serial: str | None,
    vid: int | None,
    pid: int | None,
) -> list[DeviceInfo]:
    if path:
        return [device for device in devices if device.path == path]

    matches = devices
    if serial:
        matches = [device for device in matches if device.serial_number == serial]
    if vid is not None:
        matches = [device for device in matches if device.vendor_id == vid]
    if pid is not None:
        matches = [device for device in matches if device.product_id == pid]

    if serial or vid is not None or pid is not None:
        return matches

    preferred: list[DeviceInfo] = []
    for device in matches:
        manufacturer = device.manufacturer_string.lower()
        product = device.product_string.lower()
        if "mcp2221" in product or "microchip" in manufacturer:
            preferred.append(device)
    return preferred


def select_device(
    hidapi: HidApi,
    path: str | None,
    serial: str | None,
    vid: int | None,
    pid: int | None,
) -> DeviceInfo:
    matches = match_devices(hidapi.enumerate(), path, serial, vid, pid)
    if not matches:
        raise MCP2221Error("no matching MCP2221 HID device found; run `list` to inspect paths")
    if len(matches) > 1:
        details = "\n".join(
            f"  {device.path} serial={device.serial_number or '-'} "
            f"vid=0x{device.vendor_id:04x} pid=0x{device.product_id:04x}"
            for device in matches
        )
        raise MCP2221Error(
            "multiple matching HID devices found; rerun with --path or --serial:\n" + details
        )
    return matches[0]


def print_device_list(
    hidapi: HidApi,
    path: str | None,
    serial: str | None,
    vid: int | None,
    pid: int | None,
) -> int:
    devices = hidapi.enumerate()
    if path or serial or vid is not None or pid is not None:
        devices = match_devices(devices, path, serial, vid, pid)

    if not devices:
        print("No HID devices found.")
        return 1

    for device in devices:
        manufacturer = device.manufacturer_string or "-"
        product = device.product_string or "-"
        serial = device.serial_number or "-"
        print(
            f"path={device.path} vid=0x{device.vendor_id:04x} pid=0x{device.product_id:04x} "
            f"serial={serial} manufacturer={manufacturer!r} product={product!r}"
        )
    return 0


def add_selector_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--path", help="Open a specific HID device path.")
    parser.add_argument("--serial", help="Match a specific USB serial number.")
    parser.add_argument("--vid", type=parse_hex_int, help="Match a USB VID, for example 0x04d8.")
    parser.add_argument("--pid", type=parse_hex_int, help="Match a USB PID, for example 0x00dd.")


def command_state_to_bool(value: str) -> bool:
    if value == "on":
        return True
    if value == "off":
        return False
    raise ValueError(f"unsupported state {value}")


def sd_state_to_bool(value: str) -> bool:
    if value in {"reader", "on", "high"}:
        return SD_ROUTE_READER
    if value in {"device", "off", "low"}:
        return SD_ROUTE_DEVICE
    raise ValueError(f"unsupported SD route {value}")


def parse_pulse_seconds(value: str) -> float:
    seconds = float(value)
    if seconds <= 0:
        raise argparse.ArgumentTypeError("pulse duration must be greater than zero")
    return seconds


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Control GP2 (SD switch) and GP3 (reset pulse) on a MCP2221/MCP2221A."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser("list", help="List HID devices visible through hidapi.")
    add_selector_args(list_parser)

    status_parser = subparsers.add_parser("status", help="Show current GP2/GP3 output state.")
    add_selector_args(status_parser)

    sd_parser = subparsers.add_parser(
        "sd",
        help="Route SD to the device or the SD card reader using GP2.",
    )
    sd_parser.add_argument(
        "state",
        choices=("device", "reader", "on", "off", "high", "low"),
        help="`reader`/`on`/`high` drives GP2 high. `device`/`off`/`low` drives GP2 low.",
    )
    add_selector_args(sd_parser)

    reset_parser = subparsers.add_parser(
        "reset",
        help="Pulse GP3 high, then return it low.",
    )
    reset_parser.add_argument(
        "--seconds",
        type=parse_pulse_seconds,
        default=0.1,
        help="Reset pulse width in seconds. Default: 0.1",
    )
    add_selector_args(reset_parser)

    return parser


def run(args: argparse.Namespace) -> int:
    hidapi = HidApi()
    try:
        if args.command == "list":
            return print_device_list(hidapi, args.path, args.serial, args.vid, args.pid)

        device = select_device(hidapi, args.path, args.serial, args.vid, args.pid)

        with MCP2221(hidapi, device.path) as mcp:
            if args.command == "status":
                status = mcp.switch_status()
                print(f"device: {device.path}")
                print(f"sd: {status['sd']}")
                print(f"reset: {status['reset']}")
                print(f"gp0: {status['gp0']}")
                print(f"gp1: {status['gp1']}")
                return 0

            if args.command == "sd":
                final_states = mcp.ensure_pin_outputs(
                    {PIN_SD_SWITCH: sd_state_to_bool(args.state)}
                )
                print(f"device: {device.path}")
                print(f"sd: {sd_route_text(final_states[PIN_SD_SWITCH])}")
                return 0

            if args.command == "reset":
                mcp.pulse_pin(PIN_RESET_SWITCH, args.seconds, active_state=True)
                print(f"device: {device.path}")
                print(f"reset: pulsed high for {args.seconds:.3f} s")
                return 0
    finally:
        hidapi.close()


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        return run(args)
    except MCP2221Error as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
