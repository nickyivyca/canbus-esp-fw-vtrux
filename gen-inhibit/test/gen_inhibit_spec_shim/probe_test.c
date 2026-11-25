/*
 * Spec 7 trip 7, "What the device can see of lateness", and spec 11's RESPOND probe
 * on the same path (both changed 2026-11-23, user; f1961dcf and ab74c3da), on the
 * mock-HAL shim: the product as shipped, against the implementor's harness on
 * twai_port's host fake, as nonode_test.c builds it. Gen-inhibit tester,
 * 2026-11-23, for the reviewer's build message on gen-inhibit/main cca06f36:
 * "show RESPOND's probe on the shared path: a probe found late (its VCM 0x051
 * among the waiting frames) is in neither on-time count and is still sent."
 *
 * Each case sends one VCM command N. Our frame for it (the probe in RESPOND, the
 * inhibit frame in INHIBIT) completes on the wire while the worker waits on an
 * empty receive queue, so the device has not yet seen it complete. Then frames
 * arrive together: a filler, which wakes the worker, and the frames behind it:
 *   late     a filler and the next command N+1 behind it (2 behind)
 *   waiting  two frames behind it, neither an 0x051 (2 behind)
 *   empty    nothing behind it
 *   between  nothing behind it, and N+1 arrives between the device's completion
 *            read and its count of the queue (the harness's ft_deliver_at_count(),
 *            ../gen_inhibit_shim/deliver_at_count.c, linked by run_spec_shim.py
 *            with the wraps it needs). The spec: "It counts them after it sees the
 *            completion, not before. Thus it also counts a frame that arrives
 *            between the two reads."
 * The worker can see the completion only once the filler wakes it, so the frames
 * waiting at that moment are the ones behind the filler. Where N+1 came, the device
 * answers it too, and its frame completes while the worker again waits on an empty
 * queue: a last filler lets the device see that completion with nothing waiting.
 *
 * Three page readings: p0 before N, p1 after the decision and before N+1's frame
 * is seen, p2 settled. probe_judge.c judges them (probe_judge.h has the spec's
 * reading). One case per process (`probe_test <case>`; no argument lists them),
 * as nonode_test.c, because the firmware's statics outlive ft_reset().
 *
 * One line per verdict: RESULT <case> <req> PASS|NOT-AS-SPECIFIED|VOID <detail>.
 * Exit 0 only when every line is PASS. NN_PAGE=1 prints the pages.
 */
#include "shim_harness.h"
#include "gen_inhibit.h"
#include "driver/twai.h"
#include "esp_log.h"
#include "can.h"
#include "spec_layer.h"
#include "probe_judge.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define MS 1000LL

static int g_pass, g_bad;

/* ------------------------------------------------------- the status page -- */

static const char *page(void)
{
    static char buf[8192];
    buf[0] = 0;
    gen_inhibit_get_stats_json(buf, sizeof(buf));
    return buf;
}

/* A number in "sched", before its "cls" block. */
static long sched_num(const char *p, const char *key)
{
    const char *s = strstr(p, "\"sched\":{");
    if (!s) return -1;
    const char *end = strstr(s, "\"cls\":");
    char k[64];
    snprintf(k, sizeof(k), "\"%s\":", key);
    const char *q = strstr(s, k);
    if (!q || (end && q > end)) return -1;
    return strtol(q + strlen(k), NULL, 10);
}

/* A number in one class of "sched"."cls". */
static long cls_num(const char *p, const char *cls, const char *key)
{
    const char *s = strstr(p, "\"cls\":");
    if (!s) return -1;
    char c[64], k[64];
    snprintf(c, sizeof(c), "\"%s\":{", cls);
    s = strstr(s, c);
    if (!s) return -1;
    const char *end = strchr(s + strlen(c), '}');
    snprintf(k, sizeof(k), "\"%s\":", key);
    const char *q = strstr(s, k);
    if (!q || (end && q > end)) return -1;
    return strtol(q + strlen(k), NULL, 10);
}

/* One reading; false if a field is missing from the page. */
static bool read_page(pj_page_t *r, const char *tag)
{
    const char *p = page();
    if (getenv("NN_PAGE")) printf("PAGE %s %s\n", tag, p);
    r->on = sched_num(p, "ontime");
    r->unv = sched_num(p, "ontime_unverified");
    r->mx = sched_num(p, "unverified_max_backlog");
    r->skip = sched_num(p, "skipped");
    r->psent = cls_num(p, "probe", "sent");
    r->pwithdrawn = cls_num(p, "probe", "withdrawn");
    return r->on >= 0 && r->unv >= 0 && r->mx >= 0 && r->skip >= 0 && r->psent >= 0 && r->pwithdrawn >= 0;
}

/* ----------------------------------------------------------- the traffic -- */
/* The interlock set and the VCM command, as nonode_test.c sends them (its payloads
 * are checked against the DBCs by run_spec_shim.py). */
static const uint8_t KEY_ON[8] = { 0x10 };
static const uint8_t CONT[8]   = { 11 << 2 };
static const uint8_t SOC[8]    = { 0x4E, 0x20 };
static const uint8_t FLT[8]    = { 0, 0, 0, 0, 0, 0, 0, 0xC8 };
static const uint8_t SHF[8]    = { 0, 0, 0, 0, 0, 0, 2 << 4 };
static const uint8_t VCM[6]    = { 0x08, 0x00, 0x80, 0xFF, 0x7F, 0x00 };

static uint8_t g_ctr;

static void command(uint8_t *cmd)
{
    memcpy(cmd, VCM, 6);
    cmd[5] = (uint8_t)(g_ctr++ & 0x0F);
}

static void feed(uint32_t id, const uint8_t *d, uint8_t dlc, int64_t us)
{
    ft_deliver(id, d, dlc);
    ft_run(us);
    if (ft_rx_pending() > 0 && ft_in_receive()) ft_run(0);
}

static void traffic(int64_t us)
{
    const int64_t end = ft_now() + us;
    uint8_t cmd[6];
    while (ft_now() < end)
    {
        feed(0x592, KEY_ON, 8, 1500);
        feed(0x440, CONT, 8, 1500);
        feed(0x411, SOC, 8, 1500);
        feed(0x617, FLT, 8, 1500);
        feed(0x639, SHF, 8, 1500);
        command(cmd);
        feed(0x051, cmd, 6, 1500);
    }
}

/* Run until the receive queue is empty, at most `max` steps of `us`. */
static void drain(int max, int64_t us)
{
    for (int i = 0; i < max && ft_rx_pending() > 0; i++) ft_run(us);
}

/* ------------------------------------------------------------- the cases -- */

typedef struct
{
    const char *name;
    bool        respond;
    pj_kind_t   kind;
} case_t;

static const case_t CASES[] = {
    { "respond_late", true, PJ_LATE },
    { "respond_between", true, PJ_BETWEEN },
    { "respond_waiting", true, PJ_WAITING },
    { "respond_empty", true, PJ_EMPTY },
    { "respond_queued", true, PJ_QUEUED },
    { "inhibit_late", false, PJ_LATE },
    { "inhibit_between", false, PJ_BETWEEN },
    { "inhibit_waiting", false, PJ_WAITING },
    { "inhibit_empty", false, PJ_EMPTY },
    { "inhibit_queued", false, PJ_QUEUED },
};

static void void_(pj_obs_t *o, const char *why)
{
    if (o->pre_ok) snprintf(o->pre_why, sizeof(o->pre_why), "%s", why);
    o->pre_ok = false;
}

static void run_case(const case_t *c)
{
    const uint32_t ours = c->respond ? 0x7F0 : 0x051;
    pj_obs_t o;
    uint8_t cmd[6];
    memset(&o, 0, sizeof(o));
    o.respond = c->respond;
    o.kind = c->kind;
    o.behind = (c->kind == PJ_LATE || c->kind == PJ_WAITING) ? 2 : 0;
    o.pre_ok = true;

    ft_reset();
    sl_reset(NULL);
    ml_clear();
    gen_inhibit_port_register();          /* port 0, as app_main sets it up (spec 3.3) */
    gen_inhibit_init(CAN_500K, 0);
    ft_start_worker();
    ft_run(1000);
    if (c->respond)
    {
        /* RESPOND: a probe 500 us after each 0x051 (spec 3); the key gates only the
         * real 0x051 (spec 7.1 item 2), so no interlock traffic is needed. One
         * command first, seen complete with nothing waiting, so RESPOND is shown
         * answering before the case's own. */
        gen_inhibit_set_mode(GEN_INHIBIT_RESPOND, 500);
        ft_run(1000);
        command(cmd);
        ft_deliver(0x051, cmd, 6);
        ft_run(2000);
        ft_deliver(0x617, FLT, 8);
        ft_run(500);
        if (ft_wire_count_id(0x7F0) != 1) void_(&o, "RESPOND did not answer the first command with a probe");
    }
    else
    {
        gen_inhibit_set_mode(GEN_INHIBIT_INHIBIT, 500);
        traffic(1500 * MS);
        if (!strstr(page(), "\"inhibit_live\":true")) void_(&o, "never went live on the interlock set");
        ft_run(2000);
        ft_deliver(0x617, FLT, 8);        /* the last answer seen, nothing waiting */
        ft_run(500);
        drain(10, 500);
    }
    if (!read_page(&o.p0, "p0")) void_(&o, "a sched field is missing from the page");

    const int wire0 = ft_wire_count_id(ours), sent0 = ft_sent_count_id(ours);
    command(cmd);
    ft_deliver(0x051, cmd, 6);            /* N */
    if (c->kind == PJ_QUEUED)
    {
        /* N+1 is already waiting when the worker takes N, so our frame for N cannot be
         * seen complete before N+1 is dequeued (trip 7: "its completion was seen only
         * after the VCM's next 0x051 had been dequeued"). No filler stage. */
        uint8_t next[6];
        command(next);
        ft_deliver(0x051, next, 6);
        ft_run(500);
        drain(10, 500);
    }
    else if (c->kind == PJ_BETWEEN)
    {
        /* Arm only once our frame is with the driver, so the arrival is placed after
         * its completion read; and no page read until the filler is taken, since the
         * page's own read happens on this thread. */
        for (int i = 0; i < 40 && ft_sent_count_id(ours) == sent0; i++) ft_run(50);
        if (ft_sent_count_id(ours) != sent0 + 1 || ft_wire_count_id(ours) != wire0)
            void_(&o, "our frame for N was not with the driver, uncompleted, when the arrival was armed");
        uint8_t next[6];
        command(next);
        ft_deliver_at_count(0x051, next, 6);
    }
    ft_run(2000);                         /* our frame for N completes; the worker waits */
    if (c->kind != PJ_QUEUED && (ft_wire_count_id(ours) != wire0 + 1 || ft_rx_pending() != 0))
        void_(&o, "our frame for N did not complete with the queue empty before the filler");
    if (c->kind != PJ_BETWEEN && c->kind != PJ_QUEUED)
    {
        pj_page_t pre;
        if (!read_page(&pre, "pre") || pre.on != o.p0.on || pre.unv != o.p0.unv || pre.skip != o.p0.skip)
            void_(&o, "the device booked our frame for N before the filler woke it");
    }

    if (c->kind != PJ_QUEUED) ft_deliver(0x617, FLT, 8);   /* the filler: the worker wakes */
    if (c->kind == PJ_LATE)
    {
        ft_deliver(0x440, CONT, 8);
        uint8_t next[6];
        command(next);
        ft_deliver(0x051, next, 6);       /* N+1, behind the filler */
    }
    else if (c->kind == PJ_WAITING)
    {
        ft_deliver(0x440, CONT, 8);
        ft_deliver(0x411, SOC, 8);
    }
    ft_run(500);
    drain(10, 500);
    if (c->kind == PJ_BETWEEN && ft_delivered_at_count() != 1)
        void_(&o, "N+1 was not delivered between the completion read and the count");
    ft_run(2000);                         /* N+1's frame, where it came, completes; the worker waits */
    if (ft_rx_pending() != 0) void_(&o, "the queue did not drain after the filler");
    if (!read_page(&o.p1, "p1")) void_(&o, "a sched field is missing from the page");
    if (ft_wire_count_id(ours) != wire0 + pj_expected_ours(c->kind))
        void_(&o, "our frame for N+1 did not complete before the last filler");

    ft_deliver(0x617, FLT, 8);            /* the last filler: every completion seen */
    ft_run(500);
    drain(10, 500);
    ft_run(2000);
    if (!read_page(&o.p2, "p2")) void_(&o, "a sched field is missing from the page");
    o.ours_wire = ft_wire_count_id(ours) - wire0;

    pj_res_t r[PJ_MAX_RES];
    const int n = pj_judge(&o, r);
    for (int i = 0; i < n; i++)
    {
        printf("RESULT %s %s %s %s\n", c->name, r[i].req, r[i].v, r[i].detail);
        if (!strcmp(r[i].v, "PASS")) g_pass++;
        else g_bad++;
    }
    fflush(stdout);

    static const uint8_t PAD[8] = { 0 };
    gen_inhibit_set_mode(GEN_INHIBIT_OFF, 500);
    ft_deliver(0x7FF, PAD, 8);
    ft_run(1000);
    ft_run(100000);
    ft_stop_worker();
}

int main(int argc, char **argv)
{
    const int n = (int)(sizeof(CASES) / sizeof(CASES[0]));
    if (argc < 2)
    {
        for (int i = 0; i < n; i++) puts(CASES[i].name);
        return 0;
    }
    for (int i = 0; i < n; i++)
        if (!strcmp(argv[1], CASES[i].name))
        {
            run_case(&CASES[i]);
            printf("%s: %d PASS, %d not PASS\n", CASES[i].name, g_pass, g_bad);
            return g_bad ? 1 : 0;
        }
    fprintf(stderr, "no case %s\n", argv[1]);
    return 2;
}
