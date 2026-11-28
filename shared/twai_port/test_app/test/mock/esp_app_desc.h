/* Host stand-in, compile only (test_stack.py): the one field the probe
 * reads. */
#ifndef MOCK_ESP_APP_DESC_H
#define MOCK_ESP_APP_DESC_H
typedef struct { char version[32]; } esp_app_desc_t;
const esp_app_desc_t *esp_app_get_description(void);
#endif
