# The image is this one file: the TWAI driver and a console. No product
# code and nothing from host_bridge.
idf_component_register(
    SRCS "vport_bench.cpp"
    INCLUDE_DIRS "."
    REQUIRES driver esp_driver_gpio esp_driver_usb_serial_jtag
             esp_timer esp_app_format vfs)
