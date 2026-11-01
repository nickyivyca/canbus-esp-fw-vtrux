/*
 * gi_wican -- gen-inhibit's hooks into the shared stock code (spec 3.3,
 * shared/wican W5 and W8; port step 1a).
 *
 * The stock WiCAN code is the shared component vtrux/wican. It owns nothing of
 * the inhibitor's: everything the inhibitor needs from it goes through the
 * callbacks and the URI hook registered here, before wican_init().
 *
 * BUS-OWNER CALLBACKS (spec 3.3):
 *   - owns_bus() is gen_inhibit_owns_bus(): true in every non-OFF mode;
 *   - ota_begin() is the worker's update request (8.1 rows 4 and 8, 9.3): go
 *     ahead from IDLE, refused while armed, no answer within 1 s;
 *   - ota_failed() returns the worker to IDLE (8.1 row 11);
 *   - command_refused() counts refused_slcan (3.2, section 11);
 *   - the refusal bodies are this project's, byte for byte as they were in
 *     config_server.c before the move.
 *
 * PROJECT URIS (spec 3.3): /gen_inhibit and /gen_inhibit_set at every server
 * start; the abort probe in its instrument build; the restart-test URIs in the
 * restart-test build, at the first start only. The extra handler slots are the
 * restart-test URIs' count in that build, none otherwise: the rest fit in
 * stock's 21, as before the move.
 */
#include <stdbool.h>
#include <stdint.h>
#include <stdlib.h>

#include "esp_err.h"
#include "esp_http_server.h"

#include "wican.h"
#include "gen_inhibit.h"
#include "gi_wican.h"
#if GI_INSTRUMENT_TXABORT
/* Measurement build only, spec 5.2 item 9. The source is not in the component
 * unless the flag is set -- see main/CMakeLists.txt -- so this include and the
 * registration below are the whole of its footprint in shipping code. */
#include "gi_txabort_probe.h"
#endif
#if GI_TEST_RESTART_QUIESCE
/* Restart test build only, spec 12.4 -- see main/CMakeLists.txt. */
#include "gi_restart_test.h"
#endif

/* ---- gen_inhibit control --------------------------------------------------
 * GET  /gen_inhibit                       -> state and statistics as JSON
 * POST /gen_inhibit_set?mode=N&offset=N   -> 0 off, 1 observe, 2 respond,
 *                                            3 inhibit, 4 passive
 *
 * The setter is POST so that a browser prefetch or a stray GET cannot start
 * the device transmitting. `offset` applies to RESPOND only, which emits
 * GEN_INHIBIT_PROBE_ID (0x7F0) and never 0x051. Mode 3 transmits the real
 * command ID -- see gen_inhibit.h.
 */
/*
 * D10: the size comes from gen_inhibit.h, which records why it is not 1400.
 */
static esp_err_t gen_inhibit_get_handler(httpd_req_t *req)
{
    static char out[GI_STATUS_PAGE_CAP];
    int n = gen_inhibit_get_stats_json(out, sizeof(out));
    httpd_resp_set_type(req, "application/json");
    httpd_resp_send(req, out, n);
    return ESP_OK;
}

static esp_err_t gen_inhibit_set_handler(httpd_req_t *req)
{
    char query[128];
    char val[16];
    int mode = -1;
    uint32_t offset_us = 500;

    /*
     * EVERY ERROR EXIT HERE RETURNS ESP_OK after its 400, for the reason
     * flash_write_refused() gives: ESP_FAIL closes the socket with any request
     * body unread, and lwIP's RST can then take the reply with it.
     */
    if (httpd_req_get_url_query_str(req, query, sizeof(query)) != ESP_OK)
    {
        httpd_resp_send_err(req, HTTPD_400_BAD_REQUEST, "expected ?mode=0|1|2|3|4[&offset=us]");
        return ESP_OK;
    }
    if (httpd_query_key_value(query, "mode", val, sizeof(val)) == ESP_OK)
    {
        mode = atoi(val);
    }
    if (httpd_query_key_value(query, "offset", val, sizeof(val)) == ESP_OK)
    {
        offset_us = (uint32_t)atoi(val);
    }
    if (mode < 0 || mode > 4)
    {
        httpd_resp_send_err(req, HTTPD_400_BAD_REQUEST,
                            "mode must be 0..4 (3=INHIBIT, 4=PASSIVE)");
        return ESP_OK;
    }
    /*
     * Name the actual cause. This used to report "bad offset" for every
     * failure, including the one that matters most on the bench -- the bus
     * refusing to come up, which is how an arm attempt fails on an idle
     * device -- so a real arm failure read as a typo in the query string.
     */
    const esp_err_t serr = gen_inhibit_set_mode((gen_inhibit_mode_t)mode,
                                                offset_us);
    /*
     * The worker makes the change (spec 3, 2026-10-16). One it did not take
     * within 1 s was withdrawn and is never applied, so say that rather than
     * "cannot arm": the bus was never tried.
     */
    if (serr == ESP_ERR_TIMEOUT)
    {
        httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR,
                            "mode change not taken by the gen_inhibit worker "
                            "within 1 s; withdrawn, not applied");
        return ESP_OK;
    }
    /* Spec 8.1 row 12: an OTA upload holds the bus. */
    if (serr == ESP_ERR_NOT_ALLOWED)
    {
        httpd_resp_send_err(req, HTTPD_403_FORBIDDEN,
                            "bus is down for a firmware update");
        return ESP_OK;
    }
    /* Another change held the one slot for the whole second. */
    if (serr == ESP_ERR_NOT_FINISHED)
    {
        httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR,
                            "another mode change was still in progress for 1 s; "
                            "this one was not posted, not applied");
        return ESP_OK;
    }
    if (serr != ESP_OK)
    {
        httpd_resp_send_err(req, HTTPD_400_BAD_REQUEST,
                            serr == ESP_ERR_INVALID_ARG
                                ? "bad offset (max 4000 us)"
                                : "cannot arm: bus would not come up, or "
                                  "OBSERVE could not be made listen-only");
        return ESP_OK;
    }

    static char out[GI_STATUS_PAGE_CAP];    /* D10, as above */
    int n = gen_inhibit_get_stats_json(out, sizeof(out));
    httpd_resp_set_type(req, "application/json");
    httpd_resp_send(req, out, n);
    return ESP_OK;
}

static const httpd_uri_t gen_inhibit_uri = {
    .uri = "/gen_inhibit", .method = HTTP_GET,
    .handler = gen_inhibit_get_handler, .user_ctx = NULL
};
static const httpd_uri_t gen_inhibit_set_uri = {
    .uri = "/gen_inhibit_set", .method = HTTP_POST,
    .handler = gen_inhibit_set_handler, .user_ctx = NULL
};

static void gi_register_uris(httpd_handle_t server, bool restart)
{
    httpd_register_uri_handler(server, &gen_inhibit_uri);
    httpd_register_uri_handler(server, &gen_inhibit_set_uri);
#if GI_INSTRUMENT_TXABORT
    gi_txabort_register(server);
#endif
#if GI_TEST_RESTART_QUIESCE
    /* At the first start only. A full table cannot displace an existing
     * handler, and the table has slots for these (gi_wican_register()). */
    if (!restart)
    {
        gi_restart_test_register(server);
    }
#else
    (void)restart;
#endif
}

/* gen_inhibit_ota_begin()'s answer, as the OTA handler's (gen_inhibit.h). */
static wican_ota_answer_t gi_ota_begin(void)
{
    const esp_err_t ob = gen_inhibit_ota_begin();
    if (ob == ESP_OK)
    {
        return WICAN_OTA_GO;
    }
    if (ob == ESP_ERR_INVALID_STATE)
    {
        return WICAN_OTA_REFUSED;       /* armed */
    }
    return WICAN_OTA_NO_ANSWER;         /* not taken within 1 s */
}

static const wican_bus_owner_t s_gi_bus_owner = {
    .owns_bus        = gen_inhibit_owns_bus,
    .ota_begin       = gi_ota_begin,
    .ota_failed      = gen_inhibit_ota_failed,
    .command_refused = gen_inhibit_note_refused_slcan,
    .flash_write_refused_body = "gen_inhibit is armed; a flash write would stall CAN "
                                "receive. POST /gen_inhibit_set?mode=0 first",
    .ota_refused_body         = "gen_inhibit is armed; POST /gen_inhibit_set?mode=0 first",
    .ota_no_answer_body       = "gen_inhibit worker did not answer within 1 s; "
                                "nothing touched",
};

void gi_wican_register(void)
{
    /* Port 0 first: the stock code's CAN calls are can_port's on it (spec 3.3). */
    gen_inhibit_port_register();
    wican_register_bus_owner(&s_gi_bus_owner);
#if GI_TEST_RESTART_QUIESCE
    wican_register_uris(gi_register_uris, GI_RESTART_TEST_URI_COUNT);
#else
    wican_register_uris(gi_register_uris, 0);
#endif
}
