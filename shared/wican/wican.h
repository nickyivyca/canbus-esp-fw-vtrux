/*
 * vtrux/wican: the stock WiCAN start-up and its hooks (SPEC.md W4, W5, W8).
 *
 * A project's app_main registers its bus-owner callbacks and its URI hook,
 * then calls wican_init(), then its own start-up, then wican_start(), so the
 * order stays visible in the project's own file (W4). Both registrations are
 * optional: without them the component behaves as upstream WiCAN.
 */
#ifndef WICAN_H
#define WICAN_H

#include <stdbool.h>
#include <stddef.h>
#include "esp_http_server.h"

/* What the bus owner answers when the OTA handler asks for the bus (W5). */
typedef enum
{
    WICAN_OTA_GO,           /* go ahead: the upload runs */
    WICAN_OTA_REFUSED,      /* 403, nothing touched */
    WICAN_OTA_NO_ANSWER,    /* 500, nothing touched */
} wican_ota_answer_t;

/*
 * THE BUS-OWNER CALLBACKS (W5). One set, registered before wican_init(). Each
 * runs on the task that reaches it (the HTTP server, the CAN tasks) and bounds
 * its own waiting; the component adds no timeout. A NULL member is upstream's
 * behaviour for that call: the bus is never owned, an OTA always proceeds,
 * nothing is told.
 *
 * The refusal bodies are the project's, so its replies are its own. A NULL
 * body sends the generic one in wican.c.
 */
typedef struct
{
    /* While true: the HTTP handlers that write flash answer 403 and write
     * nothing, the Bluetooth config writer writes nothing, ELM327's ATSP is
     * refused, and the CAN receive task does not read the bus. */
    bool (*owns_bus)(void);
    /* Called before the OTA handler touches the bus. */
    wican_ota_answer_t (*ota_begin)(void);
    /* Called on every exit of the OTA handler that does not end in the
     * restart into the new image. */
    void (*ota_failed)(void);
    /* Called once for each inbound SLCAN command that WICAN_SLCAN_COMMANDS
     * off refuses (W6). */
    void (*command_refused)(void);

    const char *flash_write_refused_body;   /* the 403 while owns_bus() */
    const char *ota_refused_body;           /* the 403 for WICAN_OTA_REFUSED */
    const char *ota_no_answer_body;         /* the 500 for WICAN_OTA_NO_ANSWER */
} wican_bus_owner_t;

/*
 * THE PROJECT'S URIS (W8). The hook is called where the project's endpoints
 * go, after the OTA endpoint and before the HTTP health report, at the first
 * server start (restart false) and at a restart (restart true). The server's
 * handler table is stock's 21 plus `slots`.
 */
typedef void (*wican_uri_hook_t)(httpd_handle_t server, bool restart);

/* Before wican_init(). The struct is copied. */
void wican_register_bus_owner(const wican_bus_owner_t *owner);
void wican_register_uris(wican_uri_hook_t hook, size_t slots);

/*
 * Stock start-up up to and including the CAN driver's init: device status,
 * OTA health, NVS, netif and the event loop, the LEDs, board_can_init(0), the
 * queues, the device id, the config server, slcan_init, can_init at the
 * configured bitrate, and the CAN health report.
 */
void wican_init(void);

/*
 * The rest, in stock's order: the protocol branches, Wi-Fi, the TCP server,
 * the version and hardware strings, mDNS, the CAN receive and send tasks,
 * sleep mode, the power LED and the log level.
 */
void wican_start(void);

/*
 * For the component's own files: the registered callbacks, or upstream's
 * behaviour where none is registered.
 */
bool wican_owns_bus(void);
wican_ota_answer_t wican_ota_begin(void);
void wican_ota_failed(void);
void wican_command_refused(void);
const char *wican_flash_write_refused_body(void);
const char *wican_ota_refused_body(void);
const char *wican_ota_no_answer_body(void);
void wican_uri_hook_call(httpd_handle_t server, bool restart);
size_t wican_uri_slots(void);

#endif
