/*
 * Mock esp_attr.h. Placement attributes mean nothing on the host: gen_inhibit.c
 * puts the scheduler's abort in IRAM because the restart step calls it (spec 3),
 * and the shim compiles that file as shipped.
 */
#ifndef MOCK_ESP_ATTR_H
#define MOCK_ESP_ATTR_H
#define IRAM_ATTR
#endif
