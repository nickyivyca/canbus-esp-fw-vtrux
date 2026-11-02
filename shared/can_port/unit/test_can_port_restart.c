/*
 * can_port's unit tests for C7, the restart stop: can_port_restart.c against
 * a scripted board (the port count and each port's kind, B5) and a scripted
 * twai_port restart stop (T12). Built twice (Makefile): with
 * CAN_PORT_HAVE_TWAI_PORT=1, as a build that has vtrux/twai_port, and with
 * =0, as one without it. Self-checks for the implementor, not verification.
 *
 * No backend is ever registered here: C7 works by the board's port kind,
 * whether or not a backend is registered in this boot.
 */
#include "../can_port_restart.c"

#include <stdio.h>
#include <string.h>

static int s_fail, s_checks;
static const char *s_case;

#define CHECK(cond) do { s_checks++; if (!(cond)) { s_fail++; \
    printf("FAIL %s:%d [%s] %s\n", __FILE__, __LINE__, s_case, #cond); } } while (0)

/* ------------------------------------------------- the scripted board -- */

#define MAX_PORTS 4
static int s_ports;
static board_can_kind_t s_kind[MAX_PORTS];
static int s_kind_calls;

int board_can_ports(void)
{
    return s_ports;
}

board_can_kind_t board_can_kind(int port)
{
    s_kind_calls++;
    return (port >= 0 && port < MAX_PORTS) ? s_kind[port] : BOARD_CAN_NONE;
}

/* ------------------------------------- the scripted TWAI restart stop -- */

static int s_twai_calls;
static esp_err_t s_twai_ret;

#if CAN_PORT_HAVE_TWAI_PORT
esp_err_t twai_port_restart_stop(can_port_restart_report_t *out)
{
    s_twai_calls++;
    /* What a stop that ran reports: every field set, so a lost one shows. */
    out->clock_on = true;
    out->held_in_reset = false;
    out->was_in_reset = true;
    out->status_at_entry = 0x24;
    out->tec = 7;
    out->rec = 5;
    out->status_at_reset = 0x04;
    out->wait_us = 18;
    out->timed_out = false;
    out->aborts = 2;
    out->entered_reset = true;
    return s_twai_ret;
}
#endif

static void reset(void)
{
    s_ports = 2;
    s_kind[0] = BOARD_CAN_TWAI;
    s_kind[1] = BOARD_CAN_SPI;
    s_kind[2] = s_kind[3] = BOARD_CAN_NONE;
    s_kind_calls = 0;
    s_twai_calls = 0;
    s_twai_ret = ESP_OK;
}

static can_port_restart_report_t garbage(void)
{
    can_port_restart_report_t r;
    memset(&r, 0xA5, sizeof(r));
    r.clock_on = r.held_in_reset = r.was_in_reset = true;
    r.timed_out = r.entered_reset = true;
    return r;
}

static bool cleared(const can_port_restart_report_t *r)
{
    return !r->clock_on && !r->held_in_reset && !r->was_in_reset
        && r->status_at_entry == 0 && r->tec == 0 && r->rec == 0
        && r->status_at_reset == 0 && r->wait_us == 0 && !r->timed_out
        && r->aborts == 0 && !r->entered_reset;
}

/* ------------------------------------------------------------- cases -- */

/* A TWAI port goes to twai_port's stop, with its report and its answer. */
static void case_twai_port(void)
{
    can_port_restart_report_t r = garbage();
    const esp_err_t e = can_port_restart_stop(0, &r);
#if CAN_PORT_HAVE_TWAI_PORT
    CHECK(e == ESP_OK);
    CHECK(s_twai_calls == 1);
    CHECK(r.clock_on && r.was_in_reset && r.entered_reset && r.aborts == 2);
    CHECK(r.status_at_entry == 0x24 && r.wait_us == 18 && r.tec == 7);
    s_twai_ret = ESP_ERR_NOT_SUPPORTED;     /* a chip with no measured file */
    CHECK(can_port_restart_stop(0, &r) == ESP_ERR_NOT_SUPPORTED);
    CHECK(s_twai_calls == 2);
#else
    /* No TWAI driver in the build: not supported, nothing touched. */
    CHECK(e == ESP_ERR_NOT_SUPPORTED);
    CHECK(s_twai_calls == 0);
    CHECK(cleared(&r));
#endif
}

/* An SPI port has no restart stop until mcp2515_port: not supported, the
 * TWAI driver not called, the report cleared. */
static void case_spi_port(void)
{
    can_port_restart_report_t r = garbage();
    CHECK(can_port_restart_stop(1, &r) == ESP_ERR_NOT_SUPPORTED);
    CHECK(s_twai_calls == 0);
    CHECK(cleared(&r));
    CHECK(s_kind_calls == 1);
}

/* A port the board does not have, or a NULL report: refused, nothing
 * called. The board's own count bounds it, not CAN_PORT_MAX. */
static void case_bad_args(void)
{
    can_port_restart_report_t r = garbage();
    CHECK(can_port_restart_stop(-1, &r) == ESP_ERR_INVALID_ARG);
    CHECK(cleared(&r));
    r = garbage();
    CHECK(can_port_restart_stop(2, &r) == ESP_ERR_INVALID_ARG);
    CHECK(cleared(&r));
    s_ports = 1;
    CHECK(can_port_restart_stop(1, &r) == ESP_ERR_INVALID_ARG);
    CHECK(can_port_restart_stop(0, NULL) == ESP_ERR_INVALID_ARG);
    CHECK(s_twai_calls == 0 && s_kind_calls == 0);
}

/* A TWAI kind at another index still goes to the TWAI driver: the kind
 * decides, not the index. */
static void case_kind_decides(void)
{
    s_kind[0] = BOARD_CAN_SPI;
    s_kind[1] = BOARD_CAN_TWAI;
    can_port_restart_report_t r = garbage();
    CHECK(can_port_restart_stop(0, &r) == ESP_ERR_NOT_SUPPORTED);
    CHECK(s_twai_calls == 0);
    const esp_err_t e = can_port_restart_stop(1, &r);
#if CAN_PORT_HAVE_TWAI_PORT
    CHECK(e == ESP_OK && s_twai_calls == 1);
#else
    CHECK(e == ESP_ERR_NOT_SUPPORTED && s_twai_calls == 0);
#endif
}

int main(void)
{
    static const struct { const char *name; void (*fn)(void); } cases[] = {
        { "twai_port", case_twai_port },
        { "spi_port", case_spi_port },
        { "bad_args", case_bad_args },
        { "kind_decides", case_kind_decides },
    };
    for (unsigned i = 0; i < sizeof(cases) / sizeof(cases[0]); i++)
    {
        s_case = cases[i].name;
        reset();
        cases[i].fn();
    }
    printf("%s: %d checks, %d failed, %u cases (CAN_PORT_HAVE_TWAI_PORT=%d)\n",
           s_fail ? "FAIL" : "PASS", s_checks, s_fail,
           (unsigned)(sizeof(cases) / sizeof(cases[0])), CAN_PORT_HAVE_TWAI_PORT);
    return s_fail ? 1 : 0;
}
