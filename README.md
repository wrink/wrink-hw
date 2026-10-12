# wrink-hw

Hardware for **wrink**, a low-power wireless sensor network with a
purpose-built IPv6 network stack running on FreeRTOS.

The projects here are [KiCad](https://www.kicad.org/) projects, supported by
[kifab](https://github.com/pengi/kifab). kifab vendors the fab's parts
libraries into each project, applies the fab's design rules, runs DRC, and
generates the gerber, BOM and placement files needed to order the boards.

## Projects

### [rpi-nrf52840-hat](rpi-nrf52840-hat/)

A Raspberry Pi HAT with an nRF52840 microcontroller (E73-2G4M08S1C module).
It is intended as a wrink gateway, bridging the Raspberry Pi and the wireless
network, but it can be used for anything that needs an nRF52840 on a
Raspberry Pi.

<p align="center"><a href="rpi-nrf52840-hat/"><img src="rpi-nrf52840-hat/images/pcb-top.png" alt="rpi-nrf52840-hat, top" width="60%"></a></p>
