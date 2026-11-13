# slcan_can1's own component. The shared components (vtrux/x2,
# vtrux/can_port, vtrux/twai_port) are its dependencies in idf_component.yml,
# which makes them its requirements.
idf_component_register(
    SRCS "app_main.c" "slcan1_proto.c"
    INCLUDE_DIRS "."
    PRIV_REQUIRES esp_driver_usb_serial_jtag freertos)

target_compile_options(${COMPONENT_LIB} PRIVATE -Wall -Wextra)
