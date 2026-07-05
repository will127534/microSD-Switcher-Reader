# microSD-Switcher-Reader: A simple USB3.0 uSD card reader with a SD card switch for SBCs


## Introduction
Basically I want some hardware that I can have agents to load images on RPI and test it all from software cmd to have it build/test RPI images for me. There has been some design out there but most of them are USB2.0 which is kinda slow so I wanna build one using USB3 SD reader, and additionally because I'm targeting RPI here, I also whould like to have USB to UART along with a GPIO to power switch on and off the RPI5.

So I have the board with GL3224 (USB3 SD card reader) and MCP2221A (USB2 to GPIO/UART/I2C), and because USB3 connector has both USB3 and USB2 signal, I have it connected to the two ICs so no USB hub needed for the two.

The board has been tested with RPI5 and GL3224 has been tested with 100 Megabytes/sec read/write speed. 

Also thanks to https://github.com/NVNTLabs/switch2-SDEX2M2 from NVNTLabs for the microSD card PCB library. (It is a neet MicroSD Express to M.2 adaptor board for Switch2)

## Notes
* Gerber and CPL and BOM file for JLCPCB is under /Gerber
* [Interactive BOM](https://htmlpreview.github.io/?https://github.com/will127534/microSD-Switcher-Reader/blob/main/bom/ibom.html) [(provided by InteractiveHtmlBom)](https://github.com/openscopeproject/InteractiveHtmlBom)   
* Source Code for MCP2221A under /software


## Support
For questions, issues, or suggestions, please open an issue in the [GitHub repository](https://github.com/will127534/microSD-Switcher-Reader/issues)

## Schematic

## PCB