# test/host/

Minimal host stand-ins for the ESP-IDF and FreeRTOS headers the fake's
`driver/twai.h` and the real `twai_port` include (`esp_err.h`,
`freertos/FreeRTOS.h`, `freertos/task.h`, `driver/gpio.h`, `esp_attr.h`,
`esp_log.h`, `board.h`), laid out as ESP-IDF lays them out. They are for the
fake's own build and for a project build with none of its own: a project's
stand-ins come first on its include path. `../host_platform.c` implements
the calls they declare.
