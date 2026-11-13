# slcan_can1/main/

The image's own component.

| File | What |
|---|---|
| `app_main.c` | the image: the USB Serial/JTAG link, CAN1's port (`vtrux/twai_port` behind `vtrux/can_port`, set up and registered at boot, never brought up until an `O` or `L`), the protocol's operations, and the loop task; split into `slcan1_app_boot()` and `slcan1_app_step()` so `../test/test_app.c` runs it on fakes |
| `slcan1_app.h` | those two calls |
| `slcan1_proto.h`, `slcan1_proto.c` | the protocol, moved from `slcan_main.cpp`, with no ESP-IDF in it, so `../test/` builds it on the host |
| `CMakeLists.txt`, `idf_component.yml` | the component and its dependencies (the board, `can_port` and `twai_port` at one shared tag; ESP-IDF 5.4.2) |
