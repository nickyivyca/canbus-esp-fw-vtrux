/* Host stand-in, compile only (test_stack.py): ESP-IDF v5.4.2's
 * esp_http_server.h, the types and calls the probe's URI handler uses. */
#ifndef MOCK_ESP_HTTP_SERVER_H
#define MOCK_ESP_HTTP_SERVER_H
#include <stddef.h>
#include <sys/types.h>
#include "esp_err.h"
typedef void *httpd_handle_t;
typedef struct httpd_req { httpd_handle_t handle; int method; } httpd_req_t;
typedef enum { HTTP_GET = 1, HTTP_POST = 3 } httpd_method_t;
typedef enum {
    HTTPD_400_BAD_REQUEST = 400,
    HTTPD_500_INTERNAL_SERVER_ERROR = 500,
    HTTPD_501_METHOD_NOT_IMPLEMENTED = 501,
} httpd_err_code_t;
typedef struct {
    const char     *uri;
    httpd_method_t  method;
    esp_err_t     (*handler)(httpd_req_t *r);
    void           *user_ctx;
} httpd_uri_t;
esp_err_t httpd_query_key_value(const char *qry, const char *key, char *val,
                                size_t val_size);
esp_err_t httpd_req_get_url_query_str(httpd_req_t *r, char *buf, size_t len);
esp_err_t httpd_resp_send_chunk(httpd_req_t *r, const char *buf, ssize_t len);
esp_err_t httpd_resp_send_err(httpd_req_t *r, httpd_err_code_t error,
                              const char *msg);
esp_err_t httpd_resp_set_type(httpd_req_t *r, const char *type);
esp_err_t httpd_register_uri_handler(httpd_handle_t h, const httpd_uri_t *u);
#endif
