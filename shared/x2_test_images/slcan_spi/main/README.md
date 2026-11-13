# slcan_spi/main/ -- NOT FOR VEHICLE

The image's one own component. The host side and the CAN side know nothing
of each other (x2_test_images SPEC T15); `app_main.c` joins them.

| File | Side | What |
|---|---|---|
| `app_main.c` | both | the joins: identity, banner, the one ring and output task everything for the host goes through, the F flags and counters |
| `can_frame.h` | both | the frame both sides use |
| `slcan_proto.h`, `slcan_proto.c` | host | the Lawicel protocol, with no ESP-IDF in it, so `../test/` builds it on the host |
| `host_usb.h`, `host_usb.c` | host | the USB Serial/JTAG transport |
| `can_side.h`, `can_side.c` | CAN | the MCP2515's task: open, close, the transmit queue, receive, bus state |
| `mcp2515.h`, `mcp2515.c` | CAN | the MCP2515's registers and SPI instructions, written from its datasheet (DS20001801), with the pins from the board layer (`board_spi_can(1, ...)`) |
| `CMakeLists.txt`, `idf_component.yml` | | the component and its dependencies (the board at a shared tag; ESP-IDF 5.4.2) |
