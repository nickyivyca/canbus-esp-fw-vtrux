/*
 * can_port's unit tests (SPEC.md C6): can_port.c and the stock calls of
 * can_stock.c against a scripted backend, an ops table whose every operation
 * returns what the case set and records that it was called. Self-checks for
 * the implementor, not verification.
 *
 * The component's files are included, not linked, so each case can put their
 * statics back to their start-up values (reset()).
 */
#include "../can_port.c"
#include "../can_stock.c"

#include <stdio.h>
#include <string.h>

static int s_fail, s_checks;
static const char *s_case;

#define CHECK(cond) do { s_checks++; if (!(cond)) { s_fail++; \
    printf("FAIL %s:%d [%s] %s\n", __FILE__, __LINE__, s_case, #cond); } } while (0)
#define CHECK_CALLS(want) do { s_checks++; if (strcmp(calls(), (want)) != 0) { s_fail++; \
    printf("FAIL %s:%d [%s] calls\n  got:  %s\n  want: %s\n", __FILE__, __LINE__, \
           s_case, calls(), (want)); } } while (0)

/* --------------------------------------------- the platform stand-ins -- */

int stub_log_e, stub_log_w;
const char *esp_err_to_name(esp_err_t e) { (void)e; return "err"; }
void stub_error_check_failed(esp_err_t e) { (void)e; }

static int64_t s_now_us;
int64_t esp_timer_get_time(void) { return s_now_us; }

static EventBits_t s_bits;
static int s_groups;
EventGroupHandle_t xEventGroupCreate(void) { s_groups++; return &s_bits; }
EventBits_t xEventGroupSetBits(EventGroupHandle_t g, EventBits_t b) { return *g |= b; }
EventBits_t xEventGroupClearBits(EventGroupHandle_t g, EventBits_t b)
{
    const EventBits_t was = *g;
    *g &= ~b;
    return was;
}
EventBits_t xEventGroupGetBits(EventGroupHandle_t g) { return *g; }
EventBits_t xEventGroupWaitBits(EventGroupHandle_t g, EventBits_t b, BaseType_t clear,
                                BaseType_t all, TickType_t wait)
{
    (void)b; (void)clear; (void)all; (void)wait;
    return *g;
}

struct mock_timer { TimerCallbackFunction_t cb; bool active; };
static struct mock_timer s_timer;
TimerHandle_t xTimerCreate(const char *name, TickType_t period, BaseType_t reload,
                           void *id, TimerCallbackFunction_t cb)
{
    (void)name; (void)period; (void)reload; (void)id;
    s_timer.cb = cb;
    return &s_timer;
}
BaseType_t xTimerIsTimerActive(TimerHandle_t t) { return t->active; }
BaseType_t xTimerStart(TimerHandle_t t, TickType_t w) { (void)w; t->active = true; return pdTRUE; }
BaseType_t xTimerStop(TimerHandle_t t, TickType_t w) { (void)w; t->active = false; return pdTRUE; }
BaseType_t xTimerReset(TimerHandle_t t, TickType_t w) { (void)w; t->active = true; return pdTRUE; }
static void fire_timer(void)
{
    if (s_timer.active)
    {
        s_timer.active = false;
        s_timer.cb(&s_timer);
    }
}
void vTaskDelay(TickType_t t) { (void)t; }

/* ------------------------------------------------- the scripted backend -- */

static struct {
    const char *calls[64];
    int n;
    esp_err_t up_ret, down_ret, mode_ret, configure_ret, receive_ret, send_ret, status_ret;
    bool is_up;
    can_port_settings_t settings;
    can_port_mode_t mode;
    twai_message_t sent;
    TickType_t wait;
    can_port_status_t status;
    void *ctx;
} b;

static void call(const char *name) { if (b.n < 64) b.calls[b.n++] = name; }

static const char *calls(void)
{
    static char buf[1024];
    size_t n = 0;
    buf[0] = '\0';
    for (int i = 0; i < b.n && n < sizeof(buf); i++)
    {
        n += (size_t)snprintf(buf + n, sizeof(buf) - n, "%s%s", i ? " " : "", b.calls[i]);
    }
    return buf;
}

static esp_err_t be_up(void *ctx) { b.ctx = ctx; call("up"); if (b.up_ret == ESP_OK) b.is_up = true; return b.up_ret; }
static esp_err_t be_down(void *ctx) { b.ctx = ctx; call("down"); if (b.down_ret == ESP_OK) b.is_up = false; return b.down_ret; }
static bool be_is_up(void *ctx) { (void)ctx; return b.is_up; }
static esp_err_t be_set_mode(void *ctx, can_port_mode_t m) { (void)ctx; call("set_mode"); b.mode = m; return b.mode_ret; }
static esp_err_t be_configure(void *ctx, const can_port_settings_t *s) { (void)ctx; call("configure"); b.settings = *s; return b.configure_ret; }
static esp_err_t be_receive(void *ctx, twai_message_t *out, TickType_t w)
{
    (void)ctx; call("receive"); b.wait = w;
    memset(out, 0, sizeof(*out)); out->identifier = 0x7E8;
    return b.receive_ret;
}
static esp_err_t be_send(void *ctx, const twai_message_t *m, TickType_t w) { (void)ctx; call("send"); b.sent = *m; b.wait = w; return b.send_ret; }
static void be_service(void *ctx) { (void)ctx; call("service"); }
static esp_err_t be_status(void *ctx, can_port_status_t *out) { (void)ctx; call("status"); *out = b.status; return b.status_ret; }
static void be_flush(void *ctx) { (void)ctx; call("flush_rx"); }

static const can_port_ops_t BE = {
    .name = "scripted", .up = be_up, .down = be_down, .is_up = be_is_up,
    .set_mode = be_set_mode, .configure = be_configure, .receive = be_receive,
    .send = be_send, .service = be_service, .status = be_status, .flush_rx = be_flush,
};

static int s_ctx_token;
static bool s_owned;
static int s_refusals;
static bool owns(void) { return s_owned; }
static void refused(void) { s_refusals++; }

static void reset(void)
{
    memset(s_ports, 0, sizeof(s_ports));
    s_owned = false;
    s_refusals = 0;
    can_port_register_owner(NULL, NULL);
    s_can_event_group = NULL;
    xCAN_EN_Timer = NULL;
    datarate = CAN_500K;
    memset(&can_cfg, 0, sizeof(can_cfg));
    can_cfg.bus_state = END_BUS;
    can_cfg.mask = 0xFFFFFFFF;
    s_bits = 0;
    s_groups = 0;
    memset(&s_timer, 0, sizeof(s_timer));
    memset(&b, 0, sizeof(b));
    stub_log_e = stub_log_w = 0;
}

/* Port 0 registered, can_init() done, the call log cleared. */
static void ready(void)
{
    CHECK(can_port_register(0, &BE, &s_ctx_token) == ESP_OK);
    can_init(CAN_500K);
    b.n = 0;
}

/* Enabled, the enable timer fired, the call log cleared. */
static void enabled(void)
{
    ready();
    can_enable();
    fire_timer();
    b.n = 0;
}

/* ---------------------------------------------------------------- cases -- */

static void case_register(void)
{
    CHECK(can_port_register(-1, &BE, NULL) == ESP_ERR_INVALID_ARG);
    CHECK(can_port_register(CAN_PORT_MAX, &BE, NULL) == ESP_ERR_INVALID_ARG);
    CHECK(can_port_register(0, NULL, NULL) == ESP_ERR_INVALID_ARG);
    can_port_ops_t partial = BE;
    partial.flush_rx = NULL;
    CHECK(can_port_register(0, &partial, NULL) == ESP_ERR_INVALID_ARG);
    CHECK(!can_port_registered(0));
    CHECK(can_port_register(0, &BE, &s_ctx_token) == ESP_OK);
    CHECK(can_port_registered(0) && !can_port_registered(1));
    CHECK(can_port_register(0, &BE, NULL) == ESP_ERR_INVALID_STATE);
    CHECK(strcmp(can_port_name(0), "scripted") == 0);
    CHECK(strcmp(can_port_name(1), "none") == 0);
    /* The context it was registered with reaches the backend. */
    CHECK(can_port_up(0) == ESP_OK && b.ctx == &s_ctx_token);
}

static void case_no_backend(void)
{
    twai_message_t m;
    can_port_status_t s;
    memset(&m, 0, sizeof(m));
    CHECK(can_port_up(1) == ESP_ERR_INVALID_STATE);
    CHECK(can_port_down(1) == ESP_ERR_INVALID_STATE);
    CHECK(!can_port_is_up(1));
    CHECK(can_port_set_mode(1, CAN_PORT_MODE_NORMAL) == ESP_ERR_INVALID_STATE);
    CHECK(can_port_receive(1, &m, 0) == ESP_ERR_INVALID_STATE);
    CHECK(can_port_send(1, &m, 0) == ESP_ERR_INVALID_STATE);
    CHECK(can_port_status(1, &s) == ESP_ERR_INVALID_STATE);
    CHECK(can_port_status(1, NULL) == ESP_ERR_INVALID_ARG);
    can_port_service(1);
    CHECK(b.n == 0);
}

static void case_port_ops_pass_through(void)
{
    twai_message_t m;
    can_port_status_t s;
    memset(&m, 0, sizeof(m));
    ready();
    b.status.rx_frames = 42;
    CHECK(can_port_set_mode(0, CAN_PORT_MODE_LISTEN_ONLY) == ESP_OK
          && b.mode == CAN_PORT_MODE_LISTEN_ONLY);
    CHECK(can_port_receive(0, &m, 9) == ESP_OK && b.wait == 9 && m.identifier == 0x7E8);
    m.identifier = 0x51;
    CHECK(can_port_send(0, &m, 3) == ESP_OK && b.wait == 3 && b.sent.identifier == 0x51);
    can_port_service(0);
    CHECK(can_port_status(0, &s) == ESP_OK && s.rx_frames == 42);
    CHECK_CALLS("set_mode receive send service status");
}

static void case_enable(void)
{
    ready();
    CHECK(!can_is_enabled());
    can_enable();
    /* The stock settings, then the bring-up, then the receive queue flushed. */
    CHECK_CALLS("configure up flush_rx");
    CHECK(can_is_enabled());
    const twai_timing_config_t want = twai_timing_config[CAN_500K];
    CHECK(b.settings.timing.brp == want.brp && b.settings.timing.tseg_1 == want.tseg_1
          && b.settings.timing.tseg_2 == want.tseg_2 && b.settings.timing.sjw == want.sjw);
    CHECK(b.settings.timing.brp == 8 && b.settings.timing.tseg_1 == 15);
    CHECK(b.settings.mode == CAN_PORT_MODE_NORMAL);
    CHECK(b.settings.acceptance_code == 0 && b.settings.acceptance_mask == 0xFFFFFFFF
          && b.settings.single_filter);
    /* The stock tasks are let through once the enable timer runs. */
    CHECK(!(s_bits & CAN_ENABLE_BIT));
    fire_timer();
    CHECK(s_bits & CAN_ENABLE_BIT);

    /* Already on: nothing. */
    b.n = 0;
    can_enable();
    CHECK(b.n == 0);
}

static void case_enable_settings(void)
{
    ready();
    can_set_silent(1);
    can_set_bitrate(CAN_250K);
    can_set_filter(0x51);
    can_set_mask(0x7FF);
    CHECK(can_filter_narrowed() && can_get_bitrate() == CAN_250K && can_is_silent());
    can_set_loopback(1);
    can_enable();
    CHECK(b.settings.mode == CAN_PORT_MODE_LISTEN_ONLY);
    CHECK(b.settings.timing.brp == twai_timing_config[CAN_250K].brp);
    CHECK(b.settings.acceptance_code == 0x51 && b.settings.acceptance_mask == 0x7FF);

    /* While on, every setter is ignored. */
    can_set_silent(0);
    can_set_bitrate(CAN_500K);
    can_set_filter(0);
    can_set_mask(0xFFFFFFFF);
    can_set_loopback(0);
    CHECK(can_is_silent() && can_get_bitrate() == CAN_250K && can_filter_narrowed());
    CHECK(can_cfg.loopback == 1);
}

static void case_enable_fails(void)
{
    ready();
    b.up_ret = ESP_ERR_INVALID_STATE;
    can_enable();
    CHECK_CALLS("configure up");
    CHECK(!can_is_enabled() && stub_log_e == 1);
    fire_timer();
    CHECK(!(s_bits & CAN_ENABLE_BIT));

    reset(); ready();
    b.configure_ret = ESP_ERR_INVALID_STATE;
    can_enable();
    CHECK_CALLS("configure");
    CHECK(!can_is_enabled());
}

static void case_disable(void)
{
    /* Never enabled: the backend's teardown still runs (C5), and runs its
     * hook; the state stays not-enabled. */
    ready();
    can_disable();
    CHECK_CALLS("down");
    CHECK(!can_is_enabled());

    reset(); enabled();
    CHECK(s_bits & CAN_ENABLE_BIT);
    can_disable();
    CHECK_CALLS("down");
    CHECK(!can_is_enabled() && !(s_bits & CAN_ENABLE_BIT));

    /* Off: nothing more, not even the teardown. */
    b.n = 0;
    can_disable();
    CHECK(b.n == 0);
}

static void case_disable_refused(void)
{
    enabled();
    b.down_ret = ESP_ERR_INVALID_STATE;
    can_disable();
    CHECK_CALLS("down");
    CHECK(can_is_enabled() && stub_log_e == 1);
    /* The stock tasks are let through again. */
    fire_timer();
    CHECK(s_bits & CAN_ENABLE_BIT);
    /* And the setters still refuse: the bus is on. */
    can_set_silent(1);
    CHECK(!can_is_silent());
}

static void case_send(void)
{
    twai_message_t m;
    memset(&m, 0, sizeof(m));
    m.identifier = 0x7DF;

    ready();
    CHECK(can_send(&m, 5) == ESP_ERR_INVALID_STATE);     /* not let through yet */
    CHECK(b.n == 0);

    reset(); enabled();
    CHECK(can_send(&m, 5) == ESP_OK);
    CHECK_CALLS("send");
    CHECK(b.wait == 5 && b.sent.identifier == 0x7DF);
    b.send_ret = ESP_ERR_TIMEOUT;
    CHECK(can_send(&m, 0) == ESP_ERR_TIMEOUT);
}

/* C4. Last, because the rate limit's clock is the function's own static. */
static void case_owner_refusal(void)
{
    twai_message_t m;
    memset(&m, 0, sizeof(m));
    enabled();
    can_port_register_owner(owns, refused);
    CHECK(can_send(&m, 0) == ESP_OK);       /* not owned */
    s_owned = true;
    s_now_us = 10000000;
    CHECK(can_send(&m, 0) == ESP_ERR_INVALID_STATE);
    CHECK(can_send(&m, 0) == ESP_ERR_INVALID_STATE);
    s_now_us += 4999999;
    CHECK(can_send(&m, 0) == ESP_ERR_INVALID_STATE);
    CHECK(s_refusals == 3 && stub_log_w == 1);   /* every one counted, one log */
    s_now_us += 2;
    CHECK(can_send(&m, 0) == ESP_ERR_INVALID_STATE);
    CHECK(s_refusals == 4 && stub_log_w == 2);
    CHECK_CALLS("send");                        /* only the unowned one */

    /* The owner's own path is not touched. */
    CHECK(can_port_send(0, &m, 0) == ESP_OK);

    /* No hook: never owned. */
    can_port_register_owner(NULL, NULL);
    CHECK(can_send(&m, 0) == ESP_OK);
}

static void case_receive(void)
{
    twai_message_t m;
    enabled();
    CHECK(can_receive(&m, 0) == ESP_OK && m.identifier == 0x7E8 && b.wait == 0);
    b.receive_ret = ESP_ERR_TIMEOUT;
    CHECK(can_receive(&m, 7) == ESP_ERR_TIMEOUT && b.wait == 7);
}

static void case_queries(void)
{
    ready();
    b.status.rx_waiting = 3;
    b.status.rx_queue_len = 64;
    CHECK(can_msgs_to_rx() == 3 && can_rx_queue_len() == 64);
    b.status_ret = ESP_ERR_INVALID_STATE;
    CHECK(can_msgs_to_rx() == 0 && can_rx_queue_len() == 0);

    /* The flush only while on. */
    b.n = 0;
    can_flush_rx();
    CHECK(b.n == 0);
    can_enable();
    b.n = 0;
    can_flush_rx();
    CHECK_CALLS("flush_rx");

    reset();
    CHECK(!can_filter_narrowed());
    can_set_mask(0x7FF);
    CHECK(can_filter_narrowed());
    can_set_mask(0xFFFFFFFF);
    can_set_filter(1);
    CHECK(can_filter_narrowed());
}

static void case_init(void)
{
    can_init(CAN_AUTO);
    can_init(CAN_500K);
    CHECK(s_groups == 1);   /* the event group and timer once */
    CHECK(can_get_bitrate() == CAN_500K);
}

int main(void)
{
    static const struct { const char *name; void (*fn)(void); } cases[] = {
        { "register", case_register },
        { "no_backend", case_no_backend },
        { "port_ops_pass_through", case_port_ops_pass_through },
        { "enable", case_enable },
        { "enable_settings", case_enable_settings },
        { "enable_fails", case_enable_fails },
        { "disable", case_disable },
        { "disable_refused", case_disable_refused },
        { "send", case_send },
        { "receive", case_receive },
        { "queries", case_queries },
        { "init", case_init },
        { "owner_refusal", case_owner_refusal },
    };
    for (unsigned i = 0; i < sizeof(cases) / sizeof(cases[0]); i++)
    {
        s_case = cases[i].name;
        reset();
        cases[i].fn();
    }
    printf("%s: %d checks, %d failed, %u cases\n", s_fail ? "FAIL" : "PASS",
           s_checks, s_fail, (unsigned)(sizeof(cases) / sizeof(cases[0])));
    return s_fail ? 1 : 0;
}
