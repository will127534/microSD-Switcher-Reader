# SD Switcher Rig

This workspace contains two related tools for a Raspberry Pi SD-switching fixture.

## Tool Overview

`mcp2221_switch.py`

- direct MCP2221 GPIO control
- `GP2`: SD route
  - high: SD connected to the host SD card reader
  - low: SD connected to the target device
- `GP3`: target power/reset switch
  - high for `0.1 s`: short press to boot
  - high for `5.0 s`: long press to hard power off
- `GP0` and `GP1`: left in their current UART LED alternate modes

`initialization.py`

- initialize the MCP2221 to have the default state switch to SD read

## Quick Start

Show live MCP2221 state:

```bash
sudo /home/pi/sd_switcher/mcp2221_switch.py status
```

Route SD to the target device:

```bash
sudo /home/pi/sd_switcher/mcp2221_switch.py sd device
```

Route SD to the host SD card reader:

```bash
sudo /home/pi/sd_switcher/mcp2221_switch.py sd reader
```

Pulse the reset output high for 100 ms:

```bash
sudo /home/pi/sd_switcher/mcp2221_switch.py reset
```

List visible HID devices:

```bash
sudo /home/pi/sd_switcher/mcp2221_switch.py list
```


## Main Files

- `mcp2221_switch.py`: low-level MCP2221 HID controller
- `initialization.py`: programs persistent MCP2221 flash defaults for GP0-GP3


## Persistent MCP2221 Defaults

Program the MCP2221 flash defaults for this fixture:

```bash
sudo /home/pi/sd_switcher/initialization.py apply
```

This sets:

- `GP2` power-up default to GPIO output high
- `GP3` power-up default to GPIO output low
- `GP0` to `LED_URX`
- `GP1` to `LED_UTX`
- UART RX/TX LED polarity to inactive-low / active-high in flash

Inspect the current flash and SRAM GP settings:

```bash
sudo /home/pi/sd_switcher/initialization.py status
```

Reset the MCP2221 so flash chip settings reload:

```bash
sudo /home/pi/sd_switcher/initialization.py reset
```
