/*
 * host_runner -- drive gen_inhibit_core on a PC and print a deterministic
 * trace.
 *
 * This compiles THE SAME gen_inhibit_core.c the ESP32 runs. That makes it a
 * regression harness, not a differential one: it pins behaviour and catches
 * change, and it cannot catch a rule that is wrong here and wrong in the
 * golden too. Accepted deliberately -- see the header of gen_inhibit_core.h.
 *
 * WHAT IS EMULATED
 *
 * The worker loop in gen_inhibit.c, faithfully enough that frame ORDER and
 * the interaction between the periodic tick and frame arrival are reproduced,
 * because that is where all three bugs found by hand on 2026-09-19 lived:
 *
 *   OFF            gi_notify_off(), advance 50 ms, receive nothing. Frames
 *                  that arrive while OFF are DROPPED, exactly as they are on
 *                  the device, where the worker is not inside twai_receive().
 *   armed          gi_tick(), then a receive that blocks up to 200 ms. If the
 *                  next frame falls inside that window, time advances to it
 *                  and gi_on_frame() runs; otherwise time advances by the
 *                  full timeout and the next iteration ticks again.
 *
 * One frame is consumed per iteration so that a tick runs between any two
 * frames, as it does on the device.
 *
 * WHAT IS NOT EMULATED, AND THEREFORE NOT TESTED HERE
 *
 *   - the TWAI driver, can_enable()/can_disable(), and the listen-only dance
 *     for OBSERVE. Those live in the shim and are hardware-only.
 *   - real latency. Every transmit is instantaneous and succeeds unless a
 *     txfail or txstall window says otherwise, so the response histogram is
 *     all zeros. Timing is what the bench ESP-to-ESP test measures; it is not
 *     something a host replay can speak to.
 *   - the controller. Since review C1 the core distinguishes "queued" from
 *     "completed on the wire", and on the device the difference comes from
 *     the driver's TX alerts. Here it is MODELLED: by default every queued
 *     frame completes immediately, which is the friendliest possible
 *     controller. `txstall` models one that never answers and `txdone`
 *     models one that answers "failed"; neither is evidence about the real
 *     peripheral, only about what the core does when told.
 *   - preemption and the races between the worker and the HTTP handlers.
 *
 * INPUT (stdin), one directive per line, times in microseconds, ascending:
 *
 *   # comment
 *   cfg <field> <value>            before anything else; see cfg_set()
 *   mode <t> <mode> <offset_us>    0 off, 1 observe, 2 respond, 3 inhibit
 *   bus  <t> <en> <ours> <errvalid> <errcount>
 *   txfail <t_from> <t_to>         transmits in [from,to) are REFUSED by
 *                                  the queue (twai_transmit() != ESP_OK)
 *   txstall <t_from> <t_to>        transmits in [from,to) are queued and the
 *                                  controller never answers -- the case
 *                                  review C1 exists for, where the frame is
 *                                  outstanding when the VCM's next 0x051
 *                                  arrives
 *   load <t> <rx_depth> <kbit>     E4: model the bus and the controller --
 *                                  frames through an rx_depth-deep RX queue,
 *                                  transmits through a single-buffer FIFO
 *                                  behind a bus made busy by the capture's own
 *                                  traffic. OFF unless a scenario asks, because
 *                                  it re-times everything.
 *   cachestall <t_from> <t_to>     the instruction cache is off in [from,to),
 *                                  so the TWAI ISR cannot run either and the
 *                                  only buffer is the hardware FIFO. Spec 9.3:
 *                                  a flash erase does this for up to 20 ms.
 *   preempt <t_from> <t_to>        the worker is off-CPU in [from,to) -- as a
 *                                  WiFi burst does on the device (measured
 *                                  worst 2.39 ms). Frames keep arriving into
 *                                  the RX queue and are dropped once it fills.
 *                                  Only meaningful with `load`.
 *   skip <t> <trip>                the transmit scheduler skipped an inhibit
 *                                 frame; `trip` is its 3-within-1s verdict.
 *                                 Supplied rather than computed -- the window
 *                                 lives in gi_sched, and a copy here could
 *                                 disagree with it.
 *   txdone <t_from> <t_to>         transmits in [from,to) are queued and the
 *                                  controller answers FAILED
 *   f <t> <id_hex> <dlc> <hexbytes>
 *   end <t>                        stop; defaults to last frame + 1 s
 *
 * OUTPUT (stdout), one record per line, all values decimal unless noted.
 * Deterministic: no host clock, no addresses, no float.
 */
#include "gen_inhibit_core.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define MAX_FRAMES  4000000
#define MAX_DIRS    4096

typedef struct { int64_t t; uint32_t id; uint8_t dlc; uint8_t data[8]; } rxf_t;

typedef enum { D_MODE, D_BUS, D_TXFAIL, D_TXSTALL, D_TXDONE, D_SKIP,
               D_LOAD, D_PREEMPT, D_CACHESTALL } dkind_t;
typedef struct { int64_t t; dkind_t k; int64_t a, b, c, d; } dir_t;

static rxf_t *g_f;
static long   g_nf, g_fi;
static dir_t  g_d[MAX_DIRS];
static int    g_nd, g_di;

/* ------------------------------------------------------- E4: the load -- */

/*
 * THE BUS AND THE CONTROLLER, modelled only when a scenario asks.
 *
 * `load <rx_depth> <bitrate_kbit>` switches it on. Without it every frame is
 * delivered the instant it is due and every transmit completes immediately,
 * which is what all 70 existing goldens were blessed against.
 *
 * WHAT IS MODELLED
 *
 *   Air time. A standard data frame is 47 bits of overhead plus 8*DLC of data,
 *   plus worst-case bit stuffing on the 34 + 8*DLC stuffable bits, plus the
 *   3-bit intermission. At 500 kbit that is ~236 us for DLC 6 and ~276 us for
 *   DLC 8. Worst-case stuffing is chosen over average because the question
 *   this model exists to answer is a deadline question.
 *
 *   Bus occupancy, taken from the CAPTURE rather than invented. Every replayed
 *   frame really was on the wire, so each one occupies it for its own air
 *   time; our transmit waits for the bus to go idle. On a 2250 fps capture that
 *   is ~56 % occupancy, and it comes from measured traffic instead of a
 *   guessed load factor -- which is the one thing a host model can do honestly
 *   here.
 *
 *   The RX queue, `rx_depth` deep (5 on the device -- the
 *   TWAI_GENERAL_CONFIG_DEFAULT the build uses). A frame arriving at a full
 *   queue is DROPPED, and a dropped 0x051 is an unanswered one: the inverter
 *   acts on the VCM's torque for that slot.
 *
 *   The TX FIFO. A frame queued while another is still going out is `behind`
 *   -- spec 5's hazard, which this harness previously hard-coded to false
 *   because it had no controller model. Completion is deferred to the air
 *   time, so the core's own trip 7 decides whether a frame arrived late.
 *
 * WHAT IS NOT. Arbitration is not modelled: a frame waits for the bus to be
 * idle but does not lose arbitration to a higher-priority ID mid-attempt. Nor
 * is the real driver's ISR, nor error frames and their retransmissions. This
 * is A MODEL, and review A4 is explicit that the full-replay bench run is what
 * calibrates it. Treat a green run here as "the deadline holds under the
 * modelled load", never as "the deadline holds on the truck".
 */
static int     g_load;              /* 0 = model off, the default */
static int     g_rx_depth = 5;
static int     g_bitrate_kbit = 500;

static int64_t g_preempt_from = -1, g_preempt_to = -1;

/*
 * THE FLASH-CACHE STALL, which is a different hazard from task preemption and
 * has a different remedy.
 *
 * Spec 9.3: CONFIG_SPI_FLASH_YIELD_DURING_ERASE=y with
 * ERASE_YIELD_DURATION_MS=20 disables the instruction cache in blocks of up to
 * 20 ms, which at 2250 fps is ~45 unserviced frames.
 *
 * During task preemption the ISR still runs, so frames keep reaching the
 * driver's software queue and its depth is the buffer. During a cache stall the
 * ISR cannot run at all -- it is not in IRAM -- so the software queue is not
 * being filled and the ONLY buffer is the controller's hardware FIFO. The
 * ESP32-C3's TWAI FIFO is 64 bytes, which is about five standard data frames.
 *
 * That asymmetry is the whole reason this is modelled separately: deepening the
 * software queue removes the preemption hazard and does nothing whatever for
 * this one.
 */
static int64_t g_stall_from = -1, g_stall_to = -1;
static int     g_fifo_depth = 5;

static unsigned g_stall_dropped;
static unsigned g_stall_dropped_cmd;

#define RXQ_MAX 64
static rxf_t  g_rxq[RXQ_MAX];
static int    g_rxq_n;

#define TXQ_MAX 32
typedef struct
{
    gi_frame_t f;
    int64_t    t_queued;
    int64_t    t_done;
    int        ok;
    int        behind;
} txq_t;
static txq_t g_txq[TXQ_MAX];
static int   g_txq_n;

static int64_t g_bus_free_until;    /* the wire is busy until here */
static int64_t g_tx_free_until;     /* our controller is busy until here */

static unsigned g_rx_dropped;
static unsigned g_rx_dropped_cmd;
static unsigned g_tx_late_past_next;
static unsigned g_tx_behind;
static unsigned g_rxq_high_water;

/*
 * Air time in microseconds for a standard data frame of `dlc` bytes.
 *
 * The fields listed here -- SOF, 11-bit ID, RTR, IDE, r0, DLC, CRC + delim,
 * ACK slot + delim, EOF -- come to 44 bits, not the 47 the expression uses, and
 * the difference is a 3-bit interframe space already folded in. The `+ 3` below
 * therefore counts the intermission a second time; see air_time_us().
 *
 * Plus 8*dlc data bits and worst-case stuffing of one bit per five identical
 * over the 34 + 8*dlc stuffable bits.
 */
static int64_t air_time_us(uint8_t dlc)
{
    /*
     * MEASURED AGAINST HARDWARE, which matters because this formula is the one
     * number in the load model that is calculated rather than measured.
     *
     * Bench segment, IXXAT witness, 300-frame back-to-back bursts per DLC,
     * three runs agreeing to ~2 us, 2026-09-26. Against a 0x00 payload, the
     * more heavily stuffed of the two arms:
     *
     *     DLC        4      5      6      7      8
     *     wire   177.9  196.0  214.1  239.8  254.2 us   (IXXAT, p50)
     *     here     196    216    236    256    276 us
     *
     * So the model runs 7-11 % long at every DLC, not the ~4 % an earlier
     * revision of this comment claimed. Two reasons, and the first is a plain
     * mistake left in place deliberately:
     *
     *   - THE INTERFRAME SPACE IS COUNTED TWICE. A standard frame is 44 bits
     *     SOF..EOF, so the 47 below is already 44 + 3 bits of intermission, and
     *     the trailing `+ 3` adds it again -- 6 us per frame before stuffing.
     *   - Worst-case stuffing against the real stuffing of a real payload.
     *
     * KEPT AS IT IS, on purpose. Long is the right direction for a deadline
     * model -- it can report a frame late that was not, never on time one that
     * was -- and this margin is not doing the work in any result here: the
     * preemption sweep's first loss sits at 20 ms against the 7.2 ms spec 5.1
     * item 5 requires, which 6 us a frame cannot account for. Changing it would
     * churn every golden for no fidelity that matters.
     *
     * Two further calibration figures, same run. Truck-rate bus occupancy
     * measured 49.3-59.9 % of the wire against the 62.1 % this model implies at
     * 2250 frames/s, so the model loads the bus slightly harder than the
     * capture's busiest sustained epoch. And 4.8 % of that epoch's frames are
     * 29-bit extended, whose arbitration field is 20 bits longer than anything
     * this function can express -- the scenarios here are all standard IDs.
     *
     * The earlier corroboration, kept because it is still true and is the only
     * cross-check taken on the device itself: the bench Kvaser sat a constant
     * ~266 us above the device's own timestamp in every arm, one 0x7F0 frame's
     * air time at 500 kbit (the device timestamps at queue, the Kvaser at the
     * wire) -- gen-inhibit-wican-firmware.md, the RX-to-probe overhead table.
     * Read it knowing the same calibration found python-can hardcodes the
     * Kvaser's timestamp resolution to 10 us and caught it reporting gaps of
     * 79.9 us, where a DLC-4 frame cannot occupy less than 158 us. Sound at
     * millisecond scale, weak at this one.
     *
     * Artifact: projects/vtrux/notes/artifacts/gen-inhibit/e4_rig_calibration.py
     * (phase `airtime`), runs/e4_rig_airtime.json, in the reverse-it repo.
     */
    const int stuffable = 34 + 8 * (int)dlc;
    const int bits = 47 + 8 * (int)dlc + (stuffable - 1) / 4 + 3;
    return ((int64_t)bits * 1000) / (int64_t)g_bitrate_kbit;
}

static int cache_stalled(int64_t now)
{
    return g_stall_from >= 0 && now >= g_stall_from && now < g_stall_to;
}

static int preempted(int64_t now)
{
    if (cache_stalled(now)) return 1;    /* the task is not running either */
    return g_preempt_from >= 0 && now >= g_preempt_from && now < g_preempt_to;
}

/*
 * How many frames can be buffered right now.
 *
 * Under a cache stall the ISR is not moving frames into the software queue, so
 * the hardware FIFO is the ceiling however deep the software queue is. This one
 * function is what makes "a deeper queue fixes preemption but not this"
 * measurable rather than asserted.
 */
static int effective_depth(int64_t now)
{
    return cache_stalled(now) ? g_fifo_depth : g_rx_depth;
}

/*
 * A replayed frame really was on the wire, so it occupied it. Advancing the
 * bus clock from the capture is what makes the transmit-delay figures mean
 * anything: at truck load our frame waits because the truck's own traffic is
 * there, not because a load factor was picked.
 */
static void bus_occupy(int64_t t, uint8_t dlc)
{
    const int64_t start = t > g_bus_free_until ? t : g_bus_free_until;
    g_bus_free_until = start + air_time_us(dlc);
}

static int64_t g_txfail_from = -1, g_txfail_to = -1;
static int64_t g_txstall_from = -1, g_txstall_to = -1;
static int64_t g_txdone_from = -1, g_txdone_to = -1;
static gi_bus_t g_bus;

/* ------------------------------------------------------------ trace out -- */

static int      g_last_valid;
static gi_state_t g_last;

static void hexdump(const uint8_t *d, int n, char *out)
{
    static const char *H = "0123456789ABCDEF";
    for (int i = 0; i < n; i++)
    {
        out[i * 2]     = H[(d[i] >> 4) & 0xF];
        out[i * 2 + 1] = H[d[i] & 0xF];
    }
    out[n * 2] = 0;
}

/*
 * STATE lines are emitted only when one of these fields changes. That keeps a
 * 100 Hz replay's trace readable and makes a diff point at the transition
 * that moved rather than at every frame after it.
 */
static int g_last_key;

static void state_line(const gi_state_t *st, int64_t now)
{
    /*
     * `key` is the EFFECTIVE spec 7.1 gate -- value and freshness together --
     * because that is the term the transmit dispatch actually tests. It is
     * tracked outside g_last because it is not a field of the state: it
     * depends on `now`, so a key that merely went stale moves it with no
     * frame having arrived, and that transition is one worth seeing.
     */
    int key = gi_key_on(st, now) ? 1 : 0;

    if (g_last_valid
        && g_last.mode == st->mode
        && g_last.inhibit_live == st->inhibit_live
        && g_last.arm_block == st->arm_block
        && g_last.abort_reason == st->abort_reason
        && g_last.abort_latched == st->abort_latched
        && g_last.disabled == st->disabled
        && g_last.disable_code == st->disable_code
        && g_last.shutdown_suppressed == st->shutdown_suppressed
        && g_last.fb_ever == st->fb_ever
        && g_last.soc_valid == st->soc_valid
        && g_last.soc_since_valid == st->soc_since_valid
        && g_last_key == key)
    {
        return;
    }
    printf("%lld STATE mode=%d live=%d block=%s abort=%s latched=%d disabled=%d"
           " dcode=%s susp=%d fb_ever=%d key=%d socv=%d socsince=%d\n",
           (long long)now, (int)st->mode, st->inhibit_live ? 1 : 0,
           gi_block_name(st->arm_block), gi_abort_name(st->abort_reason),
           st->abort_latched ? 1 : 0,
           st->disabled ? 1 : 0, gi_disable_name(st->disable_code),
           st->shutdown_suppressed ? 1 : 0, st->fb_ever ? 1 : 0, key,
           st->soc_valid ? 1 : 0, st->soc_since_valid ? 1 : 0);
    g_last = *st;
    g_last_key = key;
    g_last_valid = 1;
}

static void dump_events(const gi_events_t *ev)
{
    for (int i = 0; i < ev->n; i++)
    {
        const gi_event_t *e = &ev->e[i];
        printf("%lld EV %s a=%d b=%d c=%d\n", (long long)e->t_us,
               gi_event_name((gi_event_kind_t)e->kind), e->a, e->b, e->c);
    }
    if (ev->dropped)
    {
        printf("!! EVENT LIST OVERFLOW %u -- GI_EVENT_MAX too small\n",
               (unsigned)ev->dropped);
    }
}

/*
 * Transmit the core's emitted frames and feed the outcome back, the way
 * dispatch_emits() does on the device. due_us is honoured by moving the
 * simulated clock, not by spinning.
 */
static void dispatch(gi_state_t *st, const gi_emit_t *em, int64_t now)
{
    static const char *KIND[] = { "PROBE", "INHIBIT", "DIAG" };
    for (int i = 0; i < em->n; i++)
    {
        const gi_frame_t *f = &em->f[i];
        char hex[17];
        int64_t t_tx = f->have_due && f->due_us > now ? f->due_us : now;
        int ok = !(g_txfail_from >= 0 && t_tx >= g_txfail_from
                   && t_tx < g_txfail_to);

        hexdump(f->data, f->dlc, hex);
        printf("%lld TX %s id=%03X dlc=%u data=%s ok=%d\n",
               (long long)t_tx, KIND[f->kind], f->id, (unsigned)f->dlc,
               hex, ok);

        /*
         * `behind` was hard-coded false here until E4, because a core-only
         * replay had no controller to be behind. With the load model on it is
         * the real thing: our own frame still going out when this one is
         * queued, which is spec 5's hazard.
         */
        int behind = 0;
        if (g_load && ok)
        {
            behind = t_tx < g_tx_free_until;
            if (behind && f->kind == GI_TX_INHIBIT) g_tx_behind++;
        }

        /*
         * EVERY FRAME ENTERS THE MODEL'S QUEUE, DIAG INCLUDED, and getting
         * this wrong made spec 5's transmit hazard structurally invisible.
         *
         * The first version skipped diag before reaching here -- correctly, as
         * far as the CORE is concerned: a diag frame's outcome is best-effort
         * and is not counted (gi_on_tx_result returns early for it anyway). But
         * "not counted" is not "not transmitted". The diag page still occupies
         * the single TX buffer and still occupies the wire, and spec 5's hazard
         * is precisely "a diag frame sitting in the buffer waiting for an idle
         * bus delays an inhibit 0x051 queued behind it".
         *
         * With diag skipped, g_tx_free_until never advanced for it, so no
         * inhibit could ever be behind one and tx_behind read 0 across 510 diag
         * pages and 496 inhibits. A measurement that cannot move is worse than
         * no measurement: a zero there reads as "hazard absent".
         */
        if (g_load && ok && g_txq_n < TXQ_MAX)
        {
            /*
             * Start when the controller is free AND the wire is idle. The wire
             * clock comes from the capture's own frames (bus_occupy), so the
             * wait here is the truck's traffic rather than an invented load.
             */
            int64_t start = t_tx;
            if (start < g_tx_free_until) start = g_tx_free_until;
            if (start < g_bus_free_until) start = g_bus_free_until;

            txq_t *q = &g_txq[g_txq_n++];
            q->f = *f;
            q->t_queued = t_tx;
            q->t_done = start + air_time_us(f->dlc);
            q->ok = ok;
            q->behind = behind;

            g_tx_free_until = q->t_done;
            g_bus_free_until = q->t_done;   /* our frame occupies the wire too */

            /*
             * The core still learns the queue result now, for everything but
             * diag; only COMPLETION is deferred to tx_drain().
             */
            if (f->kind != GI_TX_DIAG)
            {
                gi_events_t ev = { 0 };
                gi_on_tx_result(st, f, ok != 0, behind != 0, t_tx, &ev);
                dump_events(&ev);
            }
            continue;
        }

        if (f->kind == GI_TX_DIAG)
        {
            continue;   /* best-effort on the device; not counted */
        }
        gi_events_t ev = { 0 };
        gi_on_tx_result(st, f, ok != 0, behind != 0, t_tx, &ev);
        dump_events(&ev);

        /*
         * Review C1: the controller's verdict, which on the device comes from
         * the driver's TX alerts. Only the inhibit frame has one -- the probe
         * is counted at the queue, as it always was.
         *
         * The DEFAULT is "completes immediately", which is deliberately the
         * friendliest controller there is: it keeps every pre-C1 scenario
         * measuring what it was written to measure instead of silently
         * becoming a test of the new pending path. txstall and txdone are how
         * a scenario asks for the other two.
         */
        if (f->kind == GI_TX_INHIBIT && ok) {
            int stalled = (g_txstall_from >= 0 && t_tx >= g_txstall_from
                           && t_tx < g_txstall_to);
            if (!stalled) {
                int failed = (g_txdone_from >= 0 && t_tx >= g_txdone_from
                              && t_tx < g_txdone_to);
                gi_events_t dev = { 0 };
                gi_on_tx_done(st, !failed, t_tx, &dev);
                dump_events(&dev);
            }
        }
    }
    if (em->dropped)
    {
        printf("!! EMIT LIST OVERFLOW %u -- GI_EMIT_MAX too small\n",
               (unsigned)em->dropped);
    }
}

/* ---------------------------------------------------------------- input -- */

/*
 * Complete every queued transmit whose air time has elapsed.
 *
 * Deferring this is the point. The core decides trip 7 for itself: an inhibit
 * frame that has not completed by the time the VCM's next 0x051 arrives loses
 * the counter race, and gi_on_frame() trips it. So this does not judge
 * lateness -- it just tells the truth about when the frame left, and lets the
 * rule under test do the judging.
 */
static void tx_drain(gi_state_t *st, int64_t now)
{
    while (g_txq_n > 0 && g_txq[0].t_done <= now)
    {
        const txq_t q = g_txq[0];
        memmove(&g_txq[0], &g_txq[1], (size_t)(g_txq_n - 1) * sizeof(g_txq[0]));
        g_txq_n--;

        if (q.f.kind == GI_TX_DIAG) continue;

        /*
         * The next VCM frame after ours was queued. If our frame was still
         * going out when it arrived, the VCM won the counter race for that
         * slot -- counted here as well as tripped by the core, because the
         * count is the number spec 5 asks for and a trip only fires once.
         */
        if (q.f.kind == GI_TX_INHIBIT)
        {
            for (long i = 0; i < g_nf; i++)
            {
                if (g_f[i].t <= q.t_queued) continue;
                if (g_f[i].id != GI_VCM_ID) continue;
                if (q.t_done > g_f[i].t)
                {
                    g_tx_late_past_next++;
                    printf("%lld !! TX LATE id=%03X queued=%lld done=%lld "
                           "but the VCM's next 0x051 arrived at %lld\n",
                           (long long)now, q.f.id, (long long)q.t_queued,
                           (long long)q.t_done, (long long)g_f[i].t);
                }
                break;
            }

            int stalled = (g_txstall_from >= 0 && q.t_queued >= g_txstall_from
                           && q.t_queued < g_txstall_to);
            if (!stalled)
            {
                int failed = (g_txdone_from >= 0 && q.t_queued >= g_txdone_from
                              && q.t_queued < g_txdone_to);
                gi_events_t dev = { 0 };
                gi_on_tx_done(st, !failed, q.t_done, &dev);
                dump_events(&dev);
            }
        }
    }
}

/*
 * A frame arrives at the controller. With the model off it is handed straight
 * to the worker; with it on it joins the RX queue, and a full queue DROPS it.
 *
 * A dropped 0x051 is the outcome that matters and it is reported by name: the
 * inverter acts on the VCM's torque for that slot, because there was no
 * answer. That is the whole reason spec 5 calls "late but never lossy"
 * unproven at truck load.
 */
static void rx_arrive(const rxf_t *f)
{
    const int depth = effective_depth(f->t);
    const int stalled = cache_stalled(f->t);

    if (g_rxq_n >= depth)
    {
        g_rx_dropped++;
        if (stalled) g_stall_dropped++;
        if (f->id == GI_VCM_ID)
        {
            g_rx_dropped_cmd++;
            if (stalled) g_stall_dropped_cmd++;
            printf("%lld !! RX DROPPED id=%03X -- %s was full at depth %d, so "
                   "this command went UNANSWERED\n",
                   (long long)f->t, f->id,
                   stalled ? "the hardware FIFO (cache stalled, ISR cannot run)"
                           : "the RX queue", depth);
        }
        else
        {
            printf("%lld !! RX DROPPED id=%03X (%s full, depth %d)\n",
                   (long long)f->t, f->id,
                   stalled ? "hardware FIFO, cache stalled" : "queue", depth);
        }
        return;
    }
    g_rxq[g_rxq_n++] = *f;
    if ((unsigned)g_rxq_n > g_rxq_high_water) g_rxq_high_water = (unsigned)g_rxq_n;
}

static int cfg_set(gi_config_t *c, const char *k, long long v)
{
    if      (!strcmp(k, "soc_min_raw"))     c->soc_min_raw     = (uint32_t)v;
    else if (!strcmp(k, "soc_debounce"))    c->soc_debounce    = (uint32_t)v;
    else if (!strcmp(k, "start_abort_rpm")) c->start_abort_rpm = (int32_t)v;
    else if (!strcmp(k, "rpm_debounce_us")) c->rpm_debounce_us = v;
    else if (!strcmp(k, "fresh_us"))        c->fresh_us        = v;
    else if (!strcmp(k, "err_window_us"))   c->err_window_us   = v;
    else if (!strcmp(k, "err_min_trip"))    c->err_min_trip    = (uint32_t)v;
    else if (!strcmp(k, "diag_period_ms"))  c->diag_period_ms  = (uint32_t)v;
    else if (!strcmp(k, "max_rx_errors"))   c->max_rx_errors   = (uint32_t)v;
    else if (!strcmp(k, "fw_version"))      c->fw_version      = (uint16_t)v;
    else if (!strcmp(k, "git_hash"))        c->git_hash        = (uint32_t)v;
    else if (!strcmp(k, "autoarm"))         c->autoarm_configured = v != 0;
    else return 0;
    return 1;
}

static uint8_t hexnib(char c)
{
    if (c >= '0' && c <= '9') return (uint8_t)(c - '0');
    if (c >= 'a' && c <= 'f') return (uint8_t)(c - 'a' + 10);
    if (c >= 'A' && c <= 'F') return (uint8_t)(c - 'A' + 10);
    return 0;
}

int main(void)
{
    gi_config_t cfg;
    gi_state_t  st;
    char line[512];
    int64_t t_end = -1;

    gi_config_defaults(&cfg);
    g_f = malloc(sizeof(rxf_t) * MAX_FRAMES);
    if (!g_f) { fprintf(stderr, "oom\n"); return 2; }

    /* Default bus: up, ours, error counter valid and quiet. */
    g_bus.can_enabled = true;
    g_bus.bus_ours = true;
    g_bus.err_valid = true;
    g_bus.bus_error_count = 0;

    while (fgets(line, sizeof(line), stdin))
    {
        char a[64], b[64];
        long long t, x, y, z, w;
        if (line[0] == '#' || line[0] == '\n') continue;

        if (sscanf(line, "cfg %63s %lld", a, &x) == 2)
        {
            if (!cfg_set(&cfg, a, x))
            {
                fprintf(stderr, "unknown cfg field '%s'\n", a);
                return 2;
            }
            continue;
        }
        if (sscanf(line, "mode %lld %lld %lld", &t, &x, &y) == 3)
        {
            if (g_nd >= MAX_DIRS) { fprintf(stderr, "too many dirs\n"); return 2; }
            g_d[g_nd++] = (dir_t){ t, D_MODE, x, y, 0, 0 };
            continue;
        }
        if (sscanf(line, "load %lld %lld %lld", &t, &x, &y) == 3)
        {
            if (g_nd >= MAX_DIRS) { fprintf(stderr, "too many dirs\n"); return 2; }
            g_d[g_nd++] = (dir_t){ t, D_LOAD, x, y, 0, 0 };
            continue;
        }
        if (sscanf(line, "preempt %lld %lld", &t, &x) == 2)
        {
            if (g_nd >= MAX_DIRS) { fprintf(stderr, "too many dirs\n"); return 2; }
            g_d[g_nd++] = (dir_t){ t, D_PREEMPT, t, x, 0, 0 };
            continue;
        }
        if (sscanf(line, "cachestall %lld %lld", &t, &x) == 2)
        {
            if (g_nd >= MAX_DIRS) { fprintf(stderr, "too many dirs\n"); return 2; }
            g_d[g_nd++] = (dir_t){ t, D_CACHESTALL, t, x, 0, 0 };
            continue;
        }
        if (sscanf(line, "bus %lld %lld %lld %lld %lld", &t, &x, &y, &z, &w) == 5)
        {
            if (g_nd >= MAX_DIRS) { fprintf(stderr, "too many dirs\n"); return 2; }
            g_d[g_nd++] = (dir_t){ t, D_BUS, x, y, z, w };
            continue;
        }
        if (sscanf(line, "txfail %lld %lld", &t, &x) == 2)
        {
            if (g_nd >= MAX_DIRS) { fprintf(stderr, "too many dirs\n"); return 2; }
            g_d[g_nd++] = (dir_t){ t, D_TXFAIL, t, x, 0, 0 };
            continue;
        }
        if (sscanf(line, "txstall %lld %lld", &t, &x) == 2)
        {
            g_d[g_nd++] = (dir_t){ t, D_TXSTALL, t, x, 0, 0 };
            continue;
        }
        if (sscanf(line, "txdone %lld %lld", &t, &x) == 2)
        {
            g_d[g_nd++] = (dir_t){ t, D_TXDONE, t, x, 0, 0 };
            continue;
        }
        /*
         * skip <t> <trip>  -- the transmit scheduler skipped an inhibit frame at
         * `t`, and `trip` is its "3 within 1 s" verdict.
         *
         * THE VERDICT IS SUPPLIED, NOT COMPUTED. The sliding window belongs to
         * gi_sched, which this harness does not compile; recomputing it here would
         * be a second implementation that could disagree with the real one, and
         * then a golden would pin the copy. What the core owns -- count the skip,
         * and abort only when told the window is met -- is exactly what this
         * exercises.
         */
        /* skip <t> <trip> [kind]  -- kind defaults to 0 (withdrawn). */
        if (sscanf(line, "skip %lld %lld %lld", &t, &x, &y) == 3)
        {
            g_d[g_nd++] = (dir_t){ t, D_SKIP, x, y, 0, 0 };
            continue;
        }
        if (sscanf(line, "skip %lld %lld", &t, &x) == 2)
        {
            g_d[g_nd++] = (dir_t){ t, D_SKIP, x, 0, 0, 0 };
            continue;
        }
        if (sscanf(line, "end %lld", &t) == 1) { t_end = t; continue; }

        if (sscanf(line, "f %lld %63s %lld %63s", &t, a, &x, b) == 4)
        {
            if (g_nf >= MAX_FRAMES) { fprintf(stderr, "too many frames\n"); return 2; }
            rxf_t *f = &g_f[g_nf++];
            memset(f, 0, sizeof(*f));
            f->t = t;
            f->id = (uint32_t)strtoul(a, NULL, 16);
            f->dlc = (uint8_t)x;
            for (int i = 0; i < f->dlc && b[i * 2] && b[i * 2 + 1]; i++)
            {
                f->data[i] = (uint8_t)((hexnib(b[i * 2]) << 4)
                                       | hexnib(b[i * 2 + 1]));
            }
            continue;
        }
        fprintf(stderr, "unparsed: %s", line);
        return 2;
    }

    /*
     * DIRECTIVES IN TIME ORDER, whatever order the file lists them in.
     *
     * The replay loop consumes them with a single advancing index and stops at
     * the first one whose time is in the future -- so a directive listed out of
     * order silently blocks every directive after it. That is a footgun, and it
     * fired the first time a new directive type was added: make_scenarios
     * emitted `load`(t=0), `preempt`(t=2507010), `mode`(t=0) in that order, and
     * the `mode` line behind the preemption never ran. The device sat in OFF for
     * the first 2.5 s of a scenario whose whole subject was the RX queue, and
     * reported a perfectly clean queue because nothing was armed to fill it.
     *
     * Sorting here rather than teaching the generator about each new keyword
     * puts the fix where the assumption lives. Stable, so directives that share
     * a timestamp keep their listed order -- `load` before `mode` at t=0 still
     * means the model is on before the arm.
     */
    for (int i = 1; i < g_nd; i++)
    {
        dir_t key = g_d[i];
        int j = i - 1;
        while (j >= 0 && g_d[j].t > key.t)
        {
            g_d[j + 1] = g_d[j];
            j--;
        }
        g_d[j + 1] = key;
    }

    gi_init(&st, &cfg);

    int64_t now = 0;
    if (g_nf > 0) now = g_f[0].t;
    if (g_nd > 0 && g_d[0].t < now) now = g_d[0].t;
    if (t_end < 0) t_end = (g_nf > 0 ? g_f[g_nf - 1].t : now) + 1000000;

    printf("# host_runner: %ld frames, %d directives, t=[%lld,%lld]\n",
           g_nf, g_nd, (long long)now, (long long)t_end);
    state_line(&st, now);

    for (;;)
    {
        /* Directives due now. */
        while (g_di < g_nd && g_d[g_di].t <= now)
        {
            const dir_t *d = &g_d[g_di++];
            gi_events_t ev = { 0 };
            switch (d->k)
            {
            case D_MODE:
                gi_set_mode(&st, (gi_mode_t)d->a, (uint32_t)d->b, now, &ev);
                break;
            case D_BUS:
                g_bus.can_enabled = d->a != 0;
                g_bus.bus_ours = d->b != 0;
                g_bus.err_valid = d->c != 0;
                g_bus.bus_error_count = (uint32_t)d->d;
                break;
            case D_TXFAIL:
                g_txfail_from = d->a;
                g_txfail_to = d->b;
                break;
            case D_TXSTALL:
                g_txstall_from = d->a;
                g_txstall_to = d->b;
                break;
            case D_TXDONE:
                g_txdone_from = d->a;
                g_txdone_to = d->b;
                break;
            case D_SKIP:
            {
                gi_events_t ev = { 0 };
                gi_on_inhibit_skip(&st, (gi_skip_kind_t)d->b, d->a != 0, d->t,
                                   &ev);
                dump_events(&ev);
                break;
            }
            case D_LOAD:
                g_load = 1;
                if (d->a > 0) g_rx_depth = (int)d->a;
                if (g_rx_depth > RXQ_MAX) g_rx_depth = RXQ_MAX;
                if (d->b > 0) g_bitrate_kbit = (int)d->b;
                printf("# E4 load model ON: rx_depth=%d bitrate=%d kbit "
                       "(air time: DLC6 %lld us, DLC8 %lld us)\n",
                       g_rx_depth, g_bitrate_kbit,
                       (long long)air_time_us(6), (long long)air_time_us(8));
                break;
            case D_PREEMPT:
                g_preempt_from = d->a;
                g_preempt_to = d->b;
                break;
            case D_CACHESTALL:
                g_stall_from = d->a;
                g_stall_to = d->b;
                printf("# E4 cache stall %lld..%lld us: the ISR cannot run, so "
                       "the buffer is the %d-frame hardware FIFO, not the "
                       "%d-deep software queue\n",
                       (long long)d->a, (long long)d->b, g_fifo_depth,
                       g_rx_depth);
                break;
            }
            dump_events(&ev);
            state_line(&st, now);
        }

        if (now >= t_end && g_fi >= g_nf) break;

        if (st.mode == GI_OFF)
        {
            gi_notify_off(&st);
            state_line(&st, now);
            /*
             * Frames arriving while OFF are dropped: the device's worker is
             * not inside twai_receive() on this path.
             */
            int64_t nxt = now + 50000;
            while (g_fi < g_nf && g_f[g_fi].t < nxt) g_fi++;
            now = nxt;
            if (now > t_end && g_fi >= g_nf) break;
            continue;
        }

        /*
         * Completions BEFORE the tick, for the same reason the device collects
         * them before gi_tick(): the tick withholds a diag page while an
         * inhibit is outstanding, and a stale flag withholds it for nothing.
         */
        if (g_load) tx_drain(&st, now);

        {
            gi_emit_t em = { 0 };
            gi_events_t ev = { 0 };
            gi_tick(&st, now, &g_bus, &em, &ev);
            dispatch(&st, &em, now);
            dump_events(&ev);
            state_line(&st, now);
        }

        int64_t deadline = now + 200000;   /* GEN_INHIBIT_RX_TIMEOUT_MS */
        int64_t nxt = deadline;
        if (g_fi < g_nf && g_f[g_fi].t < nxt) nxt = g_f[g_fi].t;
        if (g_di < g_nd && g_d[g_di].t < nxt) nxt = g_d[g_di].t;

        if (g_load)
        {
            /*
             * A BACKLOG MUST DRAIN AT THE WORKER'S PACE, not at the pace of
             * the next scheduled event. Without this the queue emptied one
             * frame per 200 ms timeout and the model reported floods of
             * drops that the device would never see -- a model wrong in the
             * same direction as the defect, which this suite has been caught
             * by before.
             *
             * 40 us is the measured RX->TX turnaround's order (spec 5: 5 us
             * mean, 38 us max on the device's own clock), so it stands in for
             * one pass round the worker loop.
             */
            if (g_rxq_n > 0 && !preempted(now) && nxt > now + 40)
            {
                nxt = now + 40;
            }
            /* Nor may the clock jump past a pending completion. */
            if (g_txq_n > 0 && g_txq[0].t_done < nxt) nxt = g_txq[0].t_done;
            /* Or past the end of a preemption, or of a cache stall. */
            if (g_preempt_to > now && g_preempt_to < nxt) nxt = g_preempt_to;
            if (g_stall_to > now && g_stall_to < nxt) nxt = g_stall_to;
        }

        if (nxt < now) nxt = now;
        now = nxt;

        if (!g_load)
        {
            if (g_fi < g_nf && g_f[g_fi].t <= now)
            {
                const rxf_t *f = &g_f[g_fi++];
                gi_emit_t em = { 0 };
                gi_events_t ev = { 0 };
                gi_on_rx_ok(&st);
                gi_on_frame(&st, f->id, f->dlc, f->data, now, &em, &ev);
                dispatch(&st, &em, now);
                dump_events(&ev);
                state_line(&st, now);
            }
            continue;
        }

        /*
         * WITH THE LOAD MODEL ON, arrival and consumption are separate events.
         *
         * Everything due by `now` lands in the RX queue -- including while the
         * worker is preempted, which is exactly when the queue fills. Then the
         * worker takes ONE frame, because that is what it does per pass round
         * its loop, and a queue that grows faster than one frame per pass is
         * the receive-loss hazard spec 5 describes.
         */
        while (g_fi < g_nf && g_f[g_fi].t <= now)
        {
            const rxf_t *f = &g_f[g_fi++];
            bus_occupy(f->t, f->dlc);   /* it really was on the wire */
            rx_arrive(f);
        }

        if (!preempted(now) && g_rxq_n > 0)
        {
            const rxf_t f = g_rxq[0];
            memmove(&g_rxq[0], &g_rxq[1],
                    (size_t)(g_rxq_n - 1) * sizeof(g_rxq[0]));
            g_rxq_n--;

            gi_emit_t em = { 0 };
            gi_events_t ev = { 0 };
            gi_on_rx_ok(&st);
            gi_on_frame(&st, f.id, f.dlc, f.data, now, &em, &ev);
            dispatch(&st, &em, now);
            dump_events(&ev);
            state_line(&st, now);
        }
    }

    printf("%lld FINAL mode=%d live=%d tx_ok=%u tx_fail=%u other=%u"
           " ctr_ok=%u ctr_bad=%u rx_gap_n=%u resp_n=%u disabled=%d"
           " dcode=%s block=%s abort=%s latched=%d soc=%u shift=%u key=%d"
           " socv=%d mainc=%u would_tx=%u emit_refused=%u\n",
           (long long)now, (int)st.mode, st.inhibit_live ? 1 : 0,
           st.tx_ok, st.tx_fail, st.other_frames,
           st.ctr_steps_ok, st.ctr_steps_bad,
           st.rx_gap.count, st.response.count, st.disabled ? 1 : 0,
           gi_disable_name(st.disable_code), gi_block_name(st.arm_block),
           gi_abort_name(st.abort_reason), st.abort_latched ? 1 : 0,
           st.soc_raw, (unsigned)st.last_shift_pos,
           gi_key_on(&st, now) ? 1 : 0,
           st.soc_valid ? 1 : 0, (unsigned)st.mainc_stat,
           st.would_tx, st.emit_refused);

    if (g_load)
    {
        printf("%lld E4 rx_dropped=%u rx_dropped_cmd=%u rxq_high_water=%u"
               " tx_behind=%u tx_late_past_next=%u"
               " stall_dropped=%u stall_dropped_cmd=%u\n",
               (long long)now, g_rx_dropped, g_rx_dropped_cmd,
               g_rxq_high_water, g_tx_behind, g_tx_late_past_next,
               g_stall_dropped, g_stall_dropped_cmd);
        printf("# E4 is a MODEL. Review A4: the full-replay bench run is what "
               "calibrates it. A green line above means the deadline held "
               "under the modelled load, not on the truck.\n");
    }

    free(g_f);
    return 0;
}
