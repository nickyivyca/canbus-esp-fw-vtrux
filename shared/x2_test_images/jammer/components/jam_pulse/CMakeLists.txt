# jam_pulse: the jammer's pulse engine. TEST EQUIPMENT, NOT FOR VEHICLE
# (x2_test_images SPEC T8, T13). Transport-free, and no TWAI or SPI driver.
# slcan_spi takes it unchanged through EXTRA_COMPONENT_DIRS (SPEC T13).
idf_component_register(
    SRCS "jam_parse.c" "jam_engine.c"
    INCLUDE_DIRS "include"
    PRIV_INCLUDE_DIRS "."
    REQUIRES esp_driver_gpio
    PRIV_REQUIRES esp_driver_rmt esp_timer)
