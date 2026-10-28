# wican_v3: MeatPi WiCAN OBD, hardware v3 (shared/boards/SPEC.md section 2).
# board.h's own includes (esp_err.h, hal/gpio_types.h, hal/spi_types.h) are
# public; board.c's GPIO driver, attributes and registers are not.
idf_component_register(SRCS "board.c"
                       INCLUDE_DIRS "include"
                       REQUIRES esp_common hal
                       PRIV_REQUIRES esp_driver_gpio soc)
