// Bench only: turn this board into a plain SLCAN transmitter on CAN1.
//
// WHY THIS EXISTS. The generator-inhibit bench needs a SECOND transmitter on the
// powertrain segment, and the reason is CAN arbitration:
//
//   Frames queued in ONE adapter do not arbitrate against each other. They leave
//   in queue order, so low-priority filler queued ahead of a 0x051 delays that
//   0x051 regardless of CAN priority. That is why the e4 harness paces its filler
//   by wire time, and it is still not enough: at 97.6 % carried load the PCAN
//   accepted every frame and 384 of 8998 commands never reached the wire, with no
//   refusal reported (2026-09-29).
//
//   Filler from a SEPARATE controller arbitrates properly. 0x7E0 loses to every
//   truck id and to 0x051, so it fills gaps and can never delay a command, while
//   still beating the diag pages on 0x7F1-0x7F8 -- which the telemetry criterion
//   requires. Arbitration decides placement, as it would on the truck.
//
// This board already sits on that segment and its interposer work is parked, so
// it is the transmitter. CAN1 (TWAI, GPIO7/GPIO6) is the vehicle segment; CAN2 and
// the MCP2515 are not touched here at all.
//
// >> IT IS SILENT UNTIL TOLD TO OPEN. Nothing is transmitted before an `O`, and a
// >> reset returns it to silent. This board shares a bus with a real truck; a
// >> firmware that starts transmitting at power-on is not acceptable on it.
//
// PROTOCOL: the SLCAN subset python-can actually sends, and nothing more.
//
//     S6           500 kbit/s. THE ONLY RATE ACCEPTED -- see below.
//     O            open (start transmitting)
//     L            open listen-only (receives, never ACKs, cannot transmit)
//     C            close
//     tIIILDD..    standard-id data frame, e.g. t7E08AAAAAAAAAAAAAAAA
//     V / N        version / serial, for tools that ask
//     ?            counters. NOT SLCAN -- see below.
//
// Replies: `\r` on success, `\a` on error, EXCEPT for `t`, which replies nothing.
// python-can's slcan writes and never reads an acknowledgement, and at ~2000
// frames/s a per-frame ack is pure USB traffic competing with the frames.
//
// FOUR DELIBERATE REFUSALS, each because silence would be worse:
//
//   * Only `S6`. Any other rate is an error rather than a best effort: this wire
//     is 500 kbit/s and a controller brought up at the wrong rate on a live
//     powertrain bus produces error frames on every message, which is how a bus
//     gets taken down.
//   * Only `t`. Extended (`T`) and remote (`r`/`R`) frames are rejected. A filler
//     needs none of them, and accepting them would let a typo put an
//     unexpected frame onto a shared bus.
//   * RECEIVED FRAMES ARE NOT FORWARDED to the host. This board is a transmitter;
//     the Kvaser is the witness and counts far better than a CDC link would. At
//     ~4400 frames/s, forwarding would spend the USB bandwidth the filler needs
//     and add a second, worse account of the same wire. The frames are still
//     drained and counted so the controller cannot overrun.
//   * No `F` (status flags). `?` reports more, and honestly.
//
// `?` is not part of SLCAN and is here because the standard protocol has no way
// to ask "did every frame I sent actually go out". It prints queued/sent/dropped
// and the bus state, so a harness can read the counters before and after a run
// and know whether its own stimulus was complete. python-can leaves the serial
// port reachable as `bus.serialPortOrig`, which is how the harness sends it.
//
// BUILD:  pio run -e slcan -t upload
// The interposer bridge firmware is NOT in this build (build_src_filter), so
// flashing this replaces the bridge. Restore it with `pio run -e esp32-can-x2 -t
// upload`; a byte-exact backup of the flash as it was before this firmware first
// went on is in notes/artifacts/interposer-firmware/flash-backup/.

#include <Arduino.h>

#include "port_twai.h"

using interposer::RxFrame;
using interposer::TwaiPort;

// CAN1, the vehicle segment. Same pins main.cpp uses, from the Arduino
// `aslcanx2` variant.
static const int CAN1_TX_GPIO = 7;
static const int CAN1_RX_GPIO = 6;

static const uint32_t BITRATE = 500000UL;

// A filler run offers frames as fast as the host can write them and lets
// arbitration place them, so the transmit queue is the thing under pressure. 32
// is the TwaiPort default; 64 costs nothing here and absorbs a USB burst without
// reporting a drop that is really just a burst.
static TwaiPort can1(CAN1_TX_GPIO, CAN1_RX_GPIO, 16, 64);

static bool g_open = false;      // controller up and allowed to transmit
static bool g_listen_only = false;

static uint32_t g_accepted = 0;  // `t` commands parsed and enqueued
static uint32_t g_rejected = 0;  // malformed or refused commands
static uint32_t g_full = 0;      // enqueue refused: transmit queue full

static char g_line[64];
static uint8_t g_len = 0;

static int hexVal(char c)
{
    if (c >= '0' && c <= '9') return c - '0';
    if (c >= 'a' && c <= 'f') return c - 'a' + 10;
    if (c >= 'A' && c <= 'F') return c - 'A' + 10;
    return -1;
}

static void ok()  { Serial.write('\r'); }
static void err() { g_rejected++; Serial.write('\a'); }

// tIIILDD.. -- standard id, one length digit, then len*2 hex nibbles.
static void handleTransmit(const char *s, uint8_t n)
{
    if (!g_open || g_listen_only) { err(); return; }
    if (n < 5) { err(); return; }

    int i0 = hexVal(s[1]), i1 = hexVal(s[2]), i2 = hexVal(s[3]);
    int dl = hexVal(s[4]);
    if (i0 < 0 || i1 < 0 || i2 < 0 || dl < 0 || dl > 8) { err(); return; }

    const uint32_t id = ((uint32_t)i0 << 8) | ((uint32_t)i1 << 4) | (uint32_t)i2;
    const uint8_t len = (uint8_t)dl;
    if (n < (uint8_t)(5 + len * 2)) { err(); return; }

    uint8_t data[8];
    for (uint8_t k = 0; k < len; k++)
    {
        const int hi = hexVal(s[5 + k * 2]);
        const int lo = hexVal(s[6 + k * 2]);
        if (hi < 0 || lo < 0) { err(); return; }
        data[k] = (uint8_t)((hi << 4) | lo);
    }

    // No reply on success: see the protocol note above.
    if (can1.send(id, false, data, len)) { g_accepted++; }
    else                                 { g_full++; }
}

static void report()
{
    Serial.printf(
        "slcan1 open=%d listen_only=%d accepted=%lu queue_full=%lu rejected=%lu "
        "tx=%lu tx_dropped=%lu rx=%lu rx_dropped=%lu bus_errors=%lu bus_off=%d\r\n",
        g_open ? 1 : 0, g_listen_only ? 1 : 0,
        (unsigned long)g_accepted, (unsigned long)g_full,
        (unsigned long)g_rejected,
        (unsigned long)can1.txCount(), (unsigned long)can1.txDropped(),
        (unsigned long)can1.rxCount(), (unsigned long)can1.rxDropped(),
        (unsigned long)can1.busErrors(), can1.busOff() ? 1 : 0);
}

static void handleLine(char *s, uint8_t n)
{
    if (n == 0) return;

    switch (s[0])
    {
    case 'S':
        // Only 500 kbit/s, deliberately. See the header.
        if (n >= 2 && s[1] == '6') { ok(); } else { err(); }
        break;

    case 'O':
    case 'L':
        if (g_open) { ok(); break; }
        g_listen_only = (s[0] == 'L');
        can1.setListenOnly(g_listen_only);
        if (can1.begin(BITRATE)) { g_open = true; ok(); }
        else                     { err(); }
        break;

    case 'C':
        if (g_open) { can1.end(); g_open = false; g_listen_only = false; }
        ok();
        break;

    case 't':
        handleTransmit(s, n);
        break;

    case 'V': Serial.print("V0101\r"); break;
    case 'N': Serial.print("NF001\r"); break;

    case '?': report(); break;

    // Everything else, T/r/R included, is refused rather than ignored.
    default: err(); break;
    }
}

void setup()
{
    //
    // A BIG RECEIVE BUFFER, AND IT IS NOT OPTIONAL AT FILLER RATES. The host
    // offers ~2600 frames/s, which is ~57 kB/s of 22-byte commands arriving in
    // bursts of 32. With the default buffer, measured 2026-09-29: of 12,930 frames
    // written, the board saw only 7,058 -- the rest were lost to receive overflow,
    // and 856 of what did arrive was rejected because the overflow had cut lines
    // in half. Both show up as a filler that will not fill, which reads in the
    // result as a bus that is already full.
    //
    // Must be called BEFORE begin() to have any effect.
    //
    Serial.setRxBufferSize(8192);
    Serial.begin(115200);
    // No wait for a host: this board must reach its silent, not-transmitting
    // state whether or not anyone is listening.
    g_open = false;
}

void loop()
{
    while (Serial.available() > 0)
    {
        const int c = Serial.read();
        if (c < 0) break;
        if (c == '\r' || c == '\n')
        {
            g_line[g_len] = '\0';
            handleLine(g_line, g_len);
            g_len = 0;
        }
        else if (g_len < sizeof(g_line) - 1)
        {
            g_line[g_len++] = (char)c;
        }
        else
        {
            // Overlong line: drop it and resynchronise at the next terminator
            // rather than truncating it into a different valid command.
            g_len = 0;
            err();
        }
    }

    if (g_open)
    {
        can1.service(millis());
        // Drain and discard. Not forwarded on purpose (see the header); draining
        // is still required or the controller overruns and starts flagging errors
        // on a bus it shares with the truck.
        RxFrame f;
        while (can1.receive(f)) { }
    }
}
