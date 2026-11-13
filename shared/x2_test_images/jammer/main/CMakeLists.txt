# The jammer's own component. TEST EQUIPMENT, NOT FOR VEHICLE
# (x2_test_images SPEC T8). No TWAI driver: CAN1's controller is never
# started in this image; ../tools/check_image.py checks the ELF for it.
idf_component_register(
    SRCS "jammer_main.c" "mcp2515_reset.c" "console_usj.c"
    INCLUDE_DIRS "."
    PRIV_REQUIRES jam_pulse esp_driver_spi esp_driver_gpio
                  esp_driver_usb_serial_jtag esp_app_format esp_timer)
