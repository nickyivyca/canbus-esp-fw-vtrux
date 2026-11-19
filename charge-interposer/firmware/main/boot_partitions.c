// The partition table is read in ESP-IDF's core startup stage, on core 0
// after ESP-IDF's flash setup and while the second core is still held
// (spec 9, "Settings the X2 board leaves to this project", core dumps;
// user, 2026-11-18). Built into every variant: each has core dumps to flash.
//
// WHY. Before the scheduler starts, ESP-IDF v5.4.2 turns the cache off on
// both cores whenever core 0 reads or maps flash, on the assumption that
// core 1 waits in IRAM (components/spi_flash/cache_utils.c). In the
// secondary stage that is false: core 1 runs do_system_init_fn
// (components/esp_system/startup.c), which is in flash, while core 0 runs
// its own secondary-stage functions. The core dump setup, init_coredump
// (SECONDARY, core 0, priority 130), looks up the coredump partition with
// esp_partition_find_first, and the first lookup maps the table from flash
// (esp_partition/partition.c, load_partitions). Reading the table here
// first means that lookup finds the table already in RAM and reads no
// flash. With CONFIG_ESP_COREDUMP_CHECK_BOOT and
// CONFIG_ESP_COREDUMP_FLASH_NO_OVERWRITE not set (sdkconfig), it is the
// core dump setup's only flash access.
//
// WHEN. CORE stage, core 0 only. Priority 150 comes after init_flash (130),
// which sets esp_flash_default_chip that each loaded partition records, and
// after init_efuse (140), since the load reads the flash-encryption eFuse.
// Core 1 is held in IRAM (call_start_cpu1 waits for s_resume_cores) until
// do_secondary_init releases it, so turning the cache off here is safe, as
// it is for init_flash's own flash reads.
//
// FAILURE. If the table cannot be read, or has no coredump partition, the
// board still starts: init_coredump then reads the table itself, as it did
// before this file. The bench and bring-up variants print one line; the
// truck variant prints nothing (spec 8.3), and ESP-IDF's own early log is
// compiled out at log level 0 in every variant.

#include "esp_err.h"
#include "esp_partition.h"
#include "esp_private/startup_internal.h"
#include "esp_rom_sys.h"

// Linker hook: main/CMakeLists.txt passes -u intp_boot_partitions_include
// so the linker keeps this object, which nothing else references.
void intp_boot_partitions_include(void)
{
}

ESP_SYSTEM_INIT_FN(intp_read_partition_table, CORE, BIT(0), 150)
{
    const esp_partition_t *part = esp_partition_find_first(
        ESP_PARTITION_TYPE_DATA, ESP_PARTITION_SUBTYPE_DATA_COREDUMP, NULL);
    if (part == NULL) {
#ifndef INTP_NO_SERIAL
        esp_rom_printf("intp: core stage: partition table not read or no "
                       "coredump partition; the core dump setup reads it\n");
#endif
    }
    return ESP_OK;
}
