/*
 * twai_port_txabort_http -- MEASUREMENT-ONLY (vtrux/twai_port SPEC.md T13).
 *
 * The transmit-abort probe's URI, POST /gi_txabort: the HTTP side of the
 * probe, split from twai_port_txabort_probe.c for T19 (charge-interposer
 * reviewer, 2026-11-27) so that the X2, which has no URI hook, runs the same
 * parse, run and answer from serial commands. The URI, its arguments, its
 * refusals with their status codes and bodies, and its answer are unchanged
 * (T13; test_app/README.md).
 */
#include <stdbool.h>
#include <stddef.h>

#include "esp_err.h"
#include "esp_log.h"
#include "esp_http_server.h"

#include "twai_port_txabort_probe.h"
#include "twai_port_txabort_run.h"

static const char *TAG = "gi_txabort";

/*
 * The project's check of whether it owns the bus, and the body of the
 * refusal while it does (twai_port_txabort_register()). Set once, before the
 * URI is registered.
 */
static bool (*s_owns_bus)(void);
static const char *s_owned_body = TXAB_OWNED_BODY;

/*
 * Fetch one query argument, or say why not.
 *
 * Returns 1 found, 0 absent, -1 present but unusable. The -1 case is the whole
 * point: httpd_query_key_value() returns ESP_ERR_HTTPD_RESULT_TRUNC rather than
 * ESP_OK when the value does not fit, so testing `== ESP_OK` treats an over-long
 * value exactly like an absent one and runs the DEFAULT. That is a guard whose
 * bypass path is indistinguishable from its ordinary path.
 */
static int http_get(void *ctx, const char *key, char *val, size_t vn)
{
    const esp_err_t e = httpd_query_key_value((const char *)ctx, key, val, vn);
    if (e == ESP_OK)              { return 1; }
    if (e == ESP_ERR_NOT_FOUND)   { return 0; }
    return -1;
}

static bool http_emit(void *ctx, const char *s, size_t n)
{
    return httpd_resp_send_chunk((httpd_req_t *)ctx, s, (ssize_t)n) == ESP_OK;
}

/*
 * The query and the refusal message are static, not on the httpd task's
 * 5120-byte stack: they were 512 of the 576 bytes of this handler's frame on
 * the C3, under twai_port_txabort_run() and the answer (gen-inhibit
 * VERIFICATION.md 12.4, "The rerun on the stack fix", 2026-11-27). One
 * request runs at a time: there is one httpd task.
 */
#define TXAB_QUERY_MAX 256
static char s_query[TXAB_QUERY_MAX];
static char s_err[TXAB_ERR_MAX];

static esp_err_t txab_handler(httpd_req_t *req)
{
    char *const query = s_query;

    /*
     * THE QUERY STRING ITSELF FIRST. A query over TXAB_QUERY_MAX returns
     * RESULT_TRUNC, and the old `== ESP_OK` test then skipped ALL parsing and ran
     * the whole measurement on defaults -- the worst version of this failure,
     * because every argument is wrong at once and nothing says so.
     */
    bool have_query = false;
    {
        const esp_err_t qe = httpd_req_get_url_query_str(req, query,
                                                         TXAB_QUERY_MAX);
        if (qe == ESP_OK)
        {
            have_query = true;
        }
        else if (qe != ESP_ERR_NOT_FOUND)
        {
            httpd_resp_send_err(req, HTTPD_400_BAD_REQUEST,
                                "the query string did not fit and would have "
                                "been truncated -- refused, because every "
                                "argument would silently have taken its default");
            ESP_LOGE(TAG, "refused: query string too long (%d)", (int)qe);
            return ESP_FAIL;
        }
    }

    /*
     * The answer's type is set before the first piece; a refusal's
     * httpd_resp_send_err() sets its own.
     */
    httpd_resp_set_type(req, "application/json");

    char *const err = s_err;
    const txab_io_t io = {
        .have_query = have_query,
        .get = http_get,
        .get_ctx = query,
        .emit = http_emit,
        .emit_ctx = req,
        .owns_bus = s_owns_bus,
        .owned_body = s_owned_body,
    };
    const int st = twai_port_txabort_run(&io, err, TXAB_ERR_MAX);
    switch (st)
    {
    case TXAB_OK:
        return httpd_resp_send_chunk(req, NULL, 0);
    case TXAB_BAD:
        httpd_resp_send_err(req, HTTPD_400_BAD_REQUEST, err);
        return ESP_FAIL;
    case TXAB_UNSUPPORTED:
        httpd_resp_send_err(req, HTTPD_501_METHOD_NOT_IMPLEMENTED, err);
        return ESP_FAIL;
    case TXAB_FAILED:
        httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, err);
        return ESP_FAIL;
    default:    /* TXAB_EMIT_FAILED: the answer was cut off */
        return ESP_FAIL;
    }
}

static const httpd_uri_t txab_uri = {
    .uri = "/gi_txabort", .method = HTTP_POST,
    .handler = txab_handler, .user_ctx = NULL
};

esp_err_t twai_port_txabort_register(httpd_handle_t server,
                                     bool (*owns_bus)(void),
                                     const char *owned_body)
{
    s_owns_bus = owns_bus;
    s_owned_body = (owned_body != NULL) ? owned_body : TXAB_OWNED_BODY;
    ESP_LOGW(TAG, "MEASUREMENT BUILD: /gi_txabort is registered. This image "
                  "writes the TWAI abort command register directly and must "
                  "never be flashed to the truck.");
    return httpd_register_uri_handler(server, &txab_uri);
}
