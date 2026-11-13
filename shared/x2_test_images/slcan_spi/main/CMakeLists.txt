# slcan_spi's own component. NOT FOR VEHICLE (x2_test_images SPEC T14).
# No TWAI driver: CAN1's controller is never started (SPEC T13); CAN1 gets
# jam_pulse's pulses only.
idf_component_register(
    SRCS "app_main.c" "slcan_proto.c" "jam_frame.c" "can_side.c" "mcp2515.c"
         "host_usb.c"
    INCLUDE_DIRS "."
    PRIV_REQUIRES jam_pulse esp_driver_spi esp_driver_gpio
                  esp_driver_usb_serial_jtag esp_app_format esp_ringbuf)
