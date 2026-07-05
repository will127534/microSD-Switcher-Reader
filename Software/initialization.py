#!/usr/bin/env python3
"""Program MCP2221 GP flash defaults for this fixture.

The MCP2221A can store GP pin configuration in flash. Those GP settings are copied
into SRAM on power-up/reset, which makes them the power-up defaults. This script
programs the fixture's intended defaults:

- GP0: LED_URX
- GP1: LED_UTX
- GP2: GPIO output high
- GP3: GPIO output low

The B-revision datasheet documents CHIPSETTING0 bits for the UART LED inactive
states, so this script also programs:

- LED_URX inactive low / active high
- LED_UTX inactive low / active high
"""

from __future__ import annotations

import argparse
import sys

from mcp2221_switch import (
    GPIO_DESIGNATION_ALT0,
    GPIO_DESIGNATION_ALT1,
    GPIO_DESIGNATION_GPIO,
    GPIO_DIRECTION_OUTPUT,
    HidApi,
    MCP2221,
    MCP2221Error,
    PIN_RESET_SWITCH,
    PIN_SD_SWITCH,
    PIN_UART_RX_LED,
    PIN_UART_TX_LED,
    SD_ROUTE_READER,
    add_selector_args,
    describe_pin_mode,
    encode_pin_setting,
    print_device_list,
    select_device,
)

CHIPSETTING0_LED_RX_INACTIVE_MASK = 1 << 6
CHIPSETTING0_LED_TX_INACTIVE_MASK = 1 << 5


def desired_gp_settings() -> list[int]:
    return [
        encode_pin_setting(GPIO_DESIGNATION_ALT0, GPIO_DIRECTION_OUTPUT, False),
        encode_pin_setting(GPIO_DESIGNATION_ALT1, GPIO_DIRECTION_OUTPUT, False),
        encode_pin_setting(GPIO_DESIGNATION_GPIO, GPIO_DIRECTION_OUTPUT, SD_ROUTE_READER),
        encode_pin_setting(GPIO_DESIGNATION_GPIO, GPIO_DIRECTION_OUTPUT, False),
    ]


def desired_flash_chip_settings(current_chip_settings: list[int] | bytes) -> list[int]:
    if len(current_chip_settings) != 4:
        raise ValueError("expected four chip settings bytes")
    updated = list(current_chip_settings)
    updated[0] &= ~(CHIPSETTING0_LED_RX_INACTIVE_MASK | CHIPSETTING0_LED_TX_INACTIVE_MASK)
    return updated


def led_polarity_text(chipsetting0: int, mask: int) -> str:
    if chipsetting0 & mask:
        return "inactive-high / active-low"
    return "inactive-low / active-high"


def print_chip_settings(label: str, chip_settings: list[int] | bytes) -> None:
    chipsetting0 = chip_settings[0]
    print(f"{label}:")
    print(f"  CHIPSETTING0: 0x{chipsetting0:02x}")
    print(f"  LED_URX polarity: {led_polarity_text(chipsetting0, CHIPSETTING0_LED_RX_INACTIVE_MASK)}")
    print(f"  LED_UTX polarity: {led_polarity_text(chipsetting0, CHIPSETTING0_LED_TX_INACTIVE_MASK)}")
    print(f"  raw bytes: {[f'0x{value:02x}' for value in chip_settings]}")


def describe_fixture_pin(pin: int, setting: int) -> str:
    if pin == PIN_UART_RX_LED and setting == encode_pin_setting(
        GPIO_DESIGNATION_ALT0, GPIO_DIRECTION_OUTPUT, False
    ):
        return "LED_URX"
    if pin == PIN_UART_TX_LED and setting == encode_pin_setting(
        GPIO_DESIGNATION_ALT1, GPIO_DIRECTION_OUTPUT, False
    ):
        return "LED_UTX"
    return describe_pin_mode(setting)


def print_settings(label: str, gp_settings: list[int] | bytes) -> None:
    print(f"{label}:")
    for pin, setting in enumerate(gp_settings):
        print(f"  GP{pin}: 0x{setting:02x} {describe_fixture_pin(pin, setting)}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Program MCP2221 flash GP defaults so GP2 powers up high, GP3 powers up low, "
            "and GP0/GP1 power up in UART LED modes."
        )
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser("list", help="List visible HID devices.")
    add_selector_args(list_parser)

    status_parser = subparsers.add_parser(
        "status",
        help="Show current flash and SRAM GP settings.",
    )
    add_selector_args(status_parser)

    reset_parser = subparsers.add_parser(
        "reset",
        help="Reset the MCP2221 so flash chip settings are reloaded from flash.",
    )
    add_selector_args(reset_parser)

    apply_parser = subparsers.add_parser(
        "apply",
        help="Write the desired power-up defaults to flash and apply them to SRAM now.",
    )
    apply_parser.add_argument(
        "--reset-chip",
        action="store_true",
        help="Reset the MCP2221 after writing flash so chip settings reload immediately.",
    )
    add_selector_args(apply_parser)
    return parser


def run(args: argparse.Namespace) -> int:
    hidapi = HidApi()
    try:
        if args.command == "list":
            return print_device_list(hidapi, args.path, args.serial, args.vid, args.pid)

        device = select_device(hidapi, args.path, args.serial, args.vid, args.pid)
        with MCP2221(hidapi, device.path) as mcp:
            if args.command == "reset":
                print(f"device: {device.path}")
                mcp.reset_chip()
                print("done: MCP2221 reset requested; it should re-enumerate on USB")
                return 0

            sram_response = mcp.get_sram_settings()
            flash_chip = list(mcp.get_flash_chip_settings())
            flash = list(mcp.get_flash_gpio_settings())
            sram_chip = list(sram_response[4:8])
            sram = [mcp.pin_setting(sram_response, pin) for pin in range(4)]

            if args.command == "status":
                print(f"device: {device.path}")
                print_chip_settings("flash chip settings", flash_chip)
                print_chip_settings("sram chip settings", sram_chip)
                print_settings("flash", flash)
                print_settings("sram", sram)
                print("note: LED polarity comes from flash CHIPSETTING0 and applies after reset/power-up.")
                return 0

            target = desired_gp_settings()
            target_chip = desired_flash_chip_settings(flash_chip)
            print(f"device: {device.path}")
            print_chip_settings("flash chip settings (before)", flash_chip)
            print_chip_settings("sram chip settings (before)", sram_chip)
            print_settings("flash (before)", flash)
            print_settings("sram (before)", sram)
            print_chip_settings("flash chip settings (target)", target_chip)
            print_settings("target", target)

            mcp.set_flash_chip_settings(target_chip)
            mcp.set_flash_gpio_settings(target)
            mcp.set_sram_gpio_settings(target)
            mcp.set_gpio_outputs(
                {
                    PIN_SD_SWITCH: SD_ROUTE_READER,
                    PIN_RESET_SWITCH: False,
                }
            )

            sram_response_after = mcp.get_sram_settings()
            flash_chip_after = list(mcp.get_flash_chip_settings())
            flash_after = list(mcp.get_flash_gpio_settings())
            sram_chip_after = list(sram_response_after[4:8])
            sram_after = [mcp.pin_setting(sram_response_after, pin) for pin in range(4)]
            print_chip_settings("flash chip settings (after)", flash_chip_after)
            print_chip_settings("sram chip settings (after)", sram_chip_after)
            print_settings("flash (after)", flash_after)
            print_settings("sram (after)", sram_after)
            print("done: flash defaults updated")
            if args.reset_chip:
                print("resetting MCP2221 so flash chip settings reload now...")
                mcp.reset_chip()
                print("done: MCP2221 reset requested; rerun status after it re-enumerates")
            else:
                print(
                    "note: run `sudo /home/pi/sd_switcher/initialization.py reset` "
                    "or replug the MCP2221 so the updated LED polarity bits take effect."
                )
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
