# test/host/

Minimal host stand-ins for the ESP-IDF and FreeRTOS headers the fake's
`driver/twai.h` and the real `twai_port` include (`esp_err.h`,
`esp_rom_sys.h`, `esp_timer.h`, `esp_cpu.h`, `freertos/FreeRTOS.h`,
`freertos/task.h`, `driver/gpio.h`, `esp_attr.h`, `esp_log.h`, `board.h`),
laid out as ESP-IDF lays them out. They are for the fake's own build and for
a project build with none of its own: a project's stand-ins come first on
its include path. `../host_platform.c` implements the calls they declare,
except three sets that `../twai_fake.c` implements:
- `esp_rom_delay_us()`, on the fake's clock;
- `esp_timer_get_time()`, weak, on the fake's clock plus the busy waits the
  clock did not see, so a build that defines its own keeps it
  (`../twai_fake.h`, "Busy waits"; 2026-11-27);
- `esp_cpu_stall()`, `esp_cpu_unstall()` and `esp_cpu_get_core_id()`, on its
  model of the chip's cores (`../README.md`, "Two cores").
