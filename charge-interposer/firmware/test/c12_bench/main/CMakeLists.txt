# The image's own Target plus the host suite's conformance sequence,
# compiled in unchanged (conformance.cpp includes its headers by relative
# path, so host_bridge is NOT an include directory: that folder also holds
# fake Arduino.h / SPI.h / driver/twai.h, which must never shadow IDF's).
idf_component_register(
    SRCS "c12_bench.cpp"
         "../../host_bridge/conformance.cpp"
         "../../host_bridge/provenance.cpp"
    INCLUDE_DIRS "."
    REQUIRES esp_driver_spi esp_driver_gpio esp_driver_usb_serial_jtag
             esp_timer esp_app_format vfs)
