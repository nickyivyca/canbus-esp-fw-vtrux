"""slcan_spi's bench checks: the image on the X2, against x2_test_images
SPEC section 5 (T12, T13, T14) and T8a. NOT FOR VEHICLE (SPEC T14).

The charge-interposer tester's (SPEC T4). Runs on madhouse-debian, under the
bench lock held by "tester (charge-interposer)", with slcan_spi already on
the X2 (flashed through the shared flasher, SPEC T11) and canmain down. The
charger segment holds only canchg (an Innomaker, gs_usb) and the X2's
MCP2515, so canchg is the X2's only peer: it ACKs the X2's frames when up in
normal mode, and its own frames are ACKed only by the X2.

Every adapter step goes through canctl (sudo -n /usr/local/sbin/canctl).
The X2's tty is found by its USB IDs (DEVICES-SPEC S1, vtrux_bench.serial_dev)
from the settings' esptool.device; the kernel's slcan runs on canctl's canx2
role. At the end canx2 is detached and down, canchg is down, as found.

Checks (--checks, default all, in this order):
  identity  raw tty: N and V give slcan_spi_NOT_FOR_VEHICLE, a version and
            the ELF sha256 of the image built (--elf); B's banner shows
            CANSTAT 0x80 (T8); every line the image sent is a protocol
            reply or a console line ("# ...") (T10, T12). T14 puts NOT FOR
            VEHICLE in the image name and boot banner only (every console
            line until 2026-11-12, x2_test_images 5f835b93)
  attach    the boot banner in the kernel's slcan stream (charge-interposer
            reviewer, 2026-11-12): the kernel attached after a boot counts
            nothing; attached and up through a boot, it counts the ROM's
            one long line as one rx_over_errors and makes no frame, error
            or error frame of the boot banner; the link works after each
            (check_attach's docstring)
  silence   canchg sends one frame per phase and watches for its ACK
            (probe_ack: canchg reports an unACKed frame as an error frame
            with CAN_ERR_ACK): closed (attached, down): no ACK; L: no ACK;
            O: ACKed, and the X2 hands the frame to the host and sends one
            host frame; after C: no ACK (T8a, T12)
  reset     raw tty: S6, O, canchg's frames ACKed; then the X2 reset through
            its USB port (RTS); canchg's frames unACKed once it is back
            (T8a "a reset returns it to silent"). The boot output, read on
            the open tty: the image's boot line, NOT FOR VEHICLE and within
            the kernel's 33 characters (T14), and the ROM's own lines, a
            gap. Records when the boot line came and when canchg first
            reported an ACK error; canchg reports error states on a timer
            of about 1 s, so the latter bounds nothing finer
  rate-tx   host -> canx2 -> canchg at --fps for --seconds: every frame
            sent arrives, in order; drops reported = 0 (T12)
  rate-rx   canchg -> X2 -> host at --fps for --seconds: every frame canchg
            sent (ACKed by the X2) reaches the host, in order, no overrun
  drops     canchg down (no ACKer): the X2 goes error-passive and reports
            it; frames past its transmit queue are dropped and each is
            reported (e3OOO: kernel tx_errors and TX_OVERFLOW error
            frames); canchg up again: the queue drains, and received +
            reported == sent, and the state comes back to active (T12)
  busoff    canchg at 250 kbit/s destroys the X2's frames, taken down and up
            to reset its own counters until the X2 is bus-off: bus-off
            reported (kernel CAN_ERR_BUSOFF) (T12)
  overrun   raw tty, not read for a while during canchg's frames at --fps:
            the frames the USB link had no room for are reported, one e3ooo
            each, and frames + reports == frames canchg sent; F shows the
            receive-FIFO bit (T12)

The jamming checks (T13; the jam channel is README.md "Jamming", its host
side jam_host.py). Commands and replies go through the kernel's slcan on
canx2. The checks from jam-boot-silence on are judged by CAN1's witness,
the Kvaser (witness.py: the kernel's kvaser_usb, netdev kvaser, its link
brought up through canctl and read back; since 2026-11-13 no longer
CANlib through --can-tools, which is still parsed and not read);
jam-stop's expectation
follows the image on the X2 (dev, slcan_spi_a or slcan_spi_b):
  jam-channel      STATUS, a bad signature, the reply ID from the host, a
                   zero ms, a 1 s TRAIN and STOP, each answered by reply
                   frames joined into jam_pulse's lines, the TRAIN ended as
                   the image must end it (STOP_EXPECT); STATUS with the X2
                   listen-only too, on the raw tty (Linux's slcan sends
                   nothing while listen-only); canchg, listening, sees no
                   jam frame
  jam-concurrent   a TRAIN on CAN1 through a whole rate-tx run (at most
                   30 s): every frame arrives in order, none dropped, ACK and
                   DONE come back, the TRAIN ended as STOP_EXPECT says,
                   canchg sees no error state
  jam-boot-silence 3 resets: no CAN1 error frame from each to 5 s after the
                   boot line
  jam-port         one pulse: exactly one CAN1 error frame, up to 1 s past
                   its DONE
  jam-calibration  10 pulses at 1 s read as 10 groups 1 s apart; the control,
                   9 at 1.1 s, reads as itself and not as 10 at 1 s
  jam-stop         a 3 s sparse TRAIN: the last error frame within 0.5 s of
                   the deadline and none for 3 s after; DONE says the stop
                   the image must use (dev either, _a stop (a), _b stop (b))
  jam-cap          a 60 s TRAIN: CAPPED, stopped at 30 s as jam-stop judges
  jam-stop-cmd     STOP 3 s into a 20 s TRAIN: stopped within 0.5 s of the
                   host's STOP; DONE end=stopped stop_by=stop_cmd
Every witness check ends with a closing pulse (PULSES 20 1) after its last
window, which the witness must count exactly once, else the check is NOT-RUN
(INCONCLUSIVE); a witness whose thread failed or ended early makes the check
NOT-RUN too, and every witness check's detail records the witness's median
lag, counts and host-stamped frames (the gen-inhibit tester's review,
2026-11-13).

--can-fail <check>: the same check with one condition broken on the bench,
which the check must then FAIL (the can-fail; exit 0 only if it FAILs):
  identity  the expected ELF sha256 altered
  attach    the ROM's long line not allowed for: the line the kernel counts
            fails it (no bench condition makes the image itself send a long
            unprompted line)
  silence  the "closed" phase run with canx2 up normal (the X2 open)
  reset     no reset sent
  rate-tx   canchg listen-only: nobody ACKs the X2's frames
  rate-rx   canx2 listen-only: the X2 ACKs none of canchg's frames
  drops     canchg up throughout: nothing is held, so nothing is dropped
  busoff    canchg at 500 kbit/s: nothing destroys the X2's frames
  overrun   the tty read throughout: nothing overruns
  jam-channel      the commands sent on 0x1F4A4D44, beside the command ID:
                   no replies, and canchg sees them
  jam-concurrent   no TRAIN sent: no ACK or DONE
  jam-boot-silence a pulse commanded after the third boot line
  jam-port         two pulses sent
  jam-calibration  the 10 at 1 s judged as 9 at 1.1 s
  jam-stop, jam-cap, jam-stop-cmd   the same record judged against a
                   deadline (or STOP) 2 s earlier

    python3.13 -B slcan_spi_bench.py --settings <bench settings.json>
        --shared-bench <shared-<tag>/shared/bench> --elf <the image's .elf>
        --out <run folder, outside any git worktree>
        [--checks a,b] [--can-fail <check>] [--fps 1320] [--seconds 60]
        [--can-tools <scratch/can-tools/<commit>>]

Exit 0: every check PASS (or, with --can-fail, that check FAILED). The
result is <out>/result.json, and each check's detail is in it.
"""

import argparse
import hashlib
import json
import os
import re
import socket
import struct
import subprocess
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import jam_host  # noqa: E402  the jam channel's host side (SPEC T13)
import witness  # noqa: E402  CAN1's witness for the jamming checks (SPEC T13)

CANCTL = ["sudo", "-n", "/usr/local/sbin/canctl"]
X2 = "canx2"
PEER = "canchg"
BITRATE = 500000

CAN_FMT = "=IB3x8s"
CAN_SIZE = struct.calcsize(CAN_FMT)
EFF = socket.CAN_EFF_FLAG
ERRF = socket.CAN_ERR_FLAG
RTRF = 0x40000000
# linux/can/error.h
ERR_CRTL = 0x004
ERR_ACK = 0x020
ERR_BUSOFF = 0x040
CRTL_RX_OVERFLOW = 0x01
CRTL_TX_OVERFLOW = 0x02
CRTL_RX_PASSIVE = 0x10
CRTL_TX_PASSIVE = 0x20
CRTL_ACTIVE = 0x40

ID_PROBE = 0x123
ID_HOST = 0x456
ID_SEQ = 0x18FECA00 | EFF
CHECKS = ("identity", "attach", "silence", "reset", "rate-tx", "rate-rx",
          "drops", "busoff", "overrun", "jam-channel", "jam-concurrent",
          "jam-boot-silence", "jam-port", "jam-calibration", "jam-stop", "jam-cap",
          "jam-stop-cmd")

LOG = []


def say(msg):
    line = "%s %s" % (time.strftime("%H:%M:%S"), msg)
    print(line, flush=True)
    LOG.append(line)


class Fail(Exception):
    pass


# ---------------------------------------------------------------- adapters

def canctl(*args, check=True):
    r = subprocess.run(CANCTL + list(args), capture_output=True, text=True)
    if check and r.returncode != 0:
        raise Fail("canctl %s: exit %d: %s" % (" ".join(args), r.returncode,
                                                (r.stderr or r.stdout).strip()))
    return r


def iface_exists(name):
    return os.path.exists("/sys/class/net/%s" % name)


def iface_up(name):
    try:
        with open("/sys/class/net/%s/operstate" % name) as f:
            return f.read().strip() != "down"
    except OSError:
        return False


def up(name, bitrate=BITRATE, listen=False, berr=False):
    if iface_up(name):
        canctl("down", name)
    args = ["up", name, str(bitrate)]
    if listen:
        args.append("listen-only")
    if berr:
        args.append("berr")
    canctl(*args)


def down(name):
    if iface_exists(name) and iface_up(name):
        canctl("down", name)


def attach():
    if not iface_exists(X2):
        canctl("slcan", "attach", X2)
    down(X2)


def detach():
    if iface_exists(X2):
        canctl("slcan", "detach", X2)
        for _ in range(50):
            if not iface_exists(X2):
                break
            time.sleep(0.1)


def stats(name):
    out = {}
    for k in ("tx_packets", "rx_packets", "tx_errors", "rx_errors",
              "rx_over_errors", "tx_dropped", "rx_dropped"):
        try:
            with open("/sys/class/net/%s/statistics/%s" % (name, k)) as f:
                out[k] = int(f.read())
        except OSError:
            out[k] = None
    return out


def delta(a, b):
    return {k: (b[k] - a[k]) if a.get(k) is not None and b.get(k) is not None
            else None for k in a}


def can_state(name):
    r = subprocess.run(["ip", "-details", "-json", "link", "show", name],
                       capture_output=True, text=True)
    try:
        return json.loads(r.stdout)[0]["linkinfo"]["info_data"].get("state")
    except Exception:
        return None


def open_sock(name):
    s = socket.socket(socket.AF_CAN, socket.SOCK_RAW, socket.CAN_RAW)
    s.setsockopt(socket.SOL_CAN_RAW, socket.CAN_RAW_ERR_FILTER, 0x1FFFFFFF)
    s.bind((name,))
    s.settimeout(0.05)
    return s


def pack(can_id, data=b""):
    return struct.pack(CAN_FMT, can_id, len(data), data.ljust(8, b"\0"))


def unpack(raw):
    can_id, dlc, data = struct.unpack(CAN_FMT, raw[:CAN_SIZE])
    return can_id, data[:dlc]


def send(sock, can_id, data=b"", retry_s=1.0):
    """Send one frame; on ENOBUFS (the host's queue full) wait and retry,
    up to retry_s. Returns the number of retries, or -1 if never sent."""
    tries = 0
    t_end = time.monotonic() + retry_s
    while True:
        try:
            sock.send(pack(can_id, data))
            return tries
        except OSError as e:
            if e.errno not in (105, 11) or time.monotonic() > t_end:  # ENOBUFS, EAGAIN
                return -1
            tries += 1
            time.sleep(0.0005)


class Reader(threading.Thread):
    """Collects every frame and error frame on one interface."""

    def __init__(self, name):
        super().__init__(daemon=True)
        self.sock = open_sock(name)
        self.frames = []        # (t, can_id, data)
        self.errors = []        # (t, can_id, data)
        self.sock_errors = []   # (t, errno): ENETDOWN once per link down
        self.stop = threading.Event()

    def run(self):
        while not self.stop.is_set():
            try:
                raw = self.sock.recv(CAN_SIZE)
            except socket.timeout:
                continue
            except OSError as e:
                # the socket reports ENETDOWN once when its link goes down
                # (canctl's up of a link that was up goes through down); it
                # keeps receiving once the link is up again
                self.sock_errors.append((time.monotonic(), e.errno))
                if e.errno == 100 and len(self.sock_errors) < 100:
                    continue
                break
            t = time.monotonic()
            can_id, data = unpack(raw)
            (self.errors if can_id & ERRF else self.frames).append((t, can_id, data))

    def finish(self):
        self.stop.set()
        self.join(2)
        self.sock.close()
        return self

    def last_rx(self):
        return self.frames[-1][0] if self.frames else None


def wait_quiet(reader, quiet_s=1.5, max_s=30.0):
    t_end = time.monotonic() + max_s
    while time.monotonic() < t_end:
        n = len(reader.frames)
        time.sleep(quiet_s)
        if len(reader.frames) == n:
            return
    raise Fail("frames still arriving after %.0f s" % max_s)


def err_count(errors, can_bit, crtl_bit=None):
    n = 0
    for _, cid, data in errors:
        if not cid & can_bit:
            continue
        if crtl_bit is not None and not (len(data) > 1 and data[1] & crtl_bit):
            continue
        n += 1
    return n


# ---------------------------------------------------------------- the tty

def tty_open(port):
    import serial
    s = serial.Serial()
    s.port = port
    s.baudrate = 115200
    s.timeout = 0.05
    # The ESP32-S3's USB Serial/JTAG resets the chip while RTS is high and
    # DTR low. The tty's open raises both; pyserial then sets DTR, then RTS.
    # DTR low first would pass through that state and reset the X2 (seen on
    # the bench, 2026-11-12), so DTR stays high and RTS goes low: no reset.
    s.dtr = True
    s.rts = False
    s.open()
    return s


def tty_reset(s):
    """Reset the X2 through its USB port, as esptool's hard reset does: DTR
    low, RTS high for 100 ms, then both low, so it boots its app."""
    s.dtr = False
    s.rts = True
    time.sleep(0.1)
    s.rts = False


def tty_cmd(s, text, wait=0.3):
    s.write(text.encode())
    s.flush()
    t_end = time.monotonic() + wait
    got = b""
    while time.monotonic() < t_end:
        got += s.read(4096)
    return got


def tty_drain(s, seconds):
    t_end = time.monotonic() + seconds
    got = b""
    while time.monotonic() < t_end:
        got += s.read(65536)
    return got


# The image's name: dev's, or one of the stop test variants' (README.md
# "Variants"); the ELF hash pins which build it is.
NAME_RE = r"slcan_spi(?:_a|_b)?_NOT_FOR_VEHICLE"
IDENT_RE = re.compile(NAME_RE + r" (\S+) elf=([0-9a-f]{64})")
REPLY_RE = re.compile(r"\A(|z|Z|V0100 .*|N.*|F[0-9A-F]{2}|e3OOO|e3ooo|s[awpb]\d{6}"
                      r"|t[0-9A-F]{3}[0-8][0-9A-F]*|T[0-9A-F]{8}[0-8][0-9A-F]*"
                      r"|r[0-9A-F]{3}[0-8]|R[0-9A-F]{8}[0-8])\Z")


def lines_of(text):
    """The image's lines: CR or BEL ends one; a leading LF belongs to the
    console line it starts."""
    out = []
    for part in re.split(r"[\r\a]", text):
        out.append(part.lstrip("\n"))
    return out


def console_violations(text):
    """Lines that are neither a protocol reply nor one of the image's
    console lines ("# ..."). T14 puts NOT FOR VEHICLE only in the image name
    and the boot banner (every console line until 2026-11-12, dropped by the
    user, x2_test_images 5f835b93)."""
    bad = []
    for ln in lines_of(text):
        if REPLY_RE.match(ln):
            continue
        if ln.startswith("# "):
            continue
        bad.append(ln)
    return bad


# Linux 6.12 slcan's SLCAN_MTU: a longer line counts one rx_over_error
# (slcan-core.c slcan_unesc), so nothing the image sends unprompted, which
# may reach the kernel, is longer. The leading LF counts.
UNPROMPTED_MAX = 33
BOOT_LINE = "\n# NOT FOR VEHICLE slcan_spi\r"


def split_boot(text):
    """Output read after a boot: (the ROM's lines, the image's output). The
    image's output starts at its boot line; what comes before it is the
    ESP32-S3 ROM's (a gap: README.md, 'Console lines')."""
    i = text.find(BOOT_LINE)
    if i < 0:
        return [ln for ln in lines_of(text) if ln], ""
    return [ln for ln in lines_of(text[:i]) if ln], text[i:]


def boot_problems(image_text, need_boot_line):
    """The image's unprompted output after a boot: its boot line, the boot
    banner, says NOT FOR VEHICLE (T14), no line is longer than the kernel
    takes, and every line is a reply form or a console line."""
    problems = []
    if need_boot_line and not image_text.startswith(BOOT_LINE):
        problems.append("no boot line %r" % BOOT_LINE)
    long_ = [p for p in re.split(r"(?<=[\r\a])", image_text)
             if len(p.rstrip("\r\a")) > UNPROMPTED_MAX]
    if long_:
        problems.append("unprompted lines longer than %d characters (the kernel "
                        "counts each as an rx_over_error): %r" % (UNPROMPTED_MAX, long_[:3]))
    bad = console_violations(image_text)
    if bad:
        problems.append("unprompted lines that are neither replies nor "
                        "console lines: %r" % bad[:5])
    return problems


# ---------------------------------------------------------------- probes

def probe_ack(window=1.0):
    """canchg, fresh, sends one frame and watches for its ACK. Returns
    {acked, ack_errors, state, tx_packets}.

    canchg's gs_usb refuses bus-error reporting (berr), and counts
    tx_packets for its frame whether or not it was ACKed (both seen on the
    bench, 2026-11-12). An unACKed frame is retransmitted until canchg goes
    error-warning and error-passive, and each state change comes up as an
    error frame carrying CAN_ERR_ACK. So: not ACKed = an error frame with
    CAN_ERR_ACK; ACKed = none, and canchg still error-active at the end."""
    up(PEER)
    r = Reader(PEER)
    r.start()
    s0 = stats(PEER)
    sock = open_sock(PEER)
    send(sock, ID_PROBE, b"SILENCE?")
    time.sleep(window)
    sock.close()
    r.finish()
    d = delta(s0, stats(PEER))
    state = can_state(PEER)
    down(PEER)
    acks = err_count(r.errors, ERR_ACK)
    return {"acked": not acks and state == "ERROR-ACTIVE", "ack_errors": acks,
            "state": state, "tx_packets": d["tx_packets"]}


# ---------------------------------------------------------------- checks

def check_identity(ctx, can_fail):
    detach()
    expect = ctx["elf_sha256"]
    if can_fail:
        expect = ("0" if expect[0] != "0" else "1") + expect[1:]
    s = tty_open(ctx["port"])
    try:
        pending = tty_drain(s, 0.5)     # whatever waited: a boot's output
        first = tty_cmd(s, "\r", 0.3)
        n = tty_cmd(s, "N\r")
        v = tty_cmd(s, "V\r")
        b = tty_cmd(s, "B\r", 0.6)
    finally:
        s.close()
    rom, boot_text = split_boot(pending.decode("ascii", "replace"))
    text = (first + n + v + b).decode("ascii", "replace")
    nm = re.search(r"N(" + NAME_RE + r" \S+ elf=[0-9a-f]{64})\r", n.decode("ascii", "replace"))
    vm = re.search(r"V0100 (" + NAME_RE + r" \S+ elf=[0-9a-f]{64})\r", v.decode("ascii", "replace"))
    banner = re.findall(r"\n(# [^\r\n]*)\r", b.decode("ascii", "replace"))
    detail = {"N": n.decode("ascii", "replace"), "V": v.decode("ascii", "replace"),
              "banner": banner, "expect_elf": expect,
              "waiting_at_open": {"rom_lines_gap": rom, "image": boot_text}}
    problems = boot_problems(boot_text, need_boot_line=False)
    if rom and not boot_text:
        problems.append("output waiting at open with no boot line: %r" % rom[:5])
    if not nm:
        problems.append("no N identity reply")
    else:
        m = IDENT_RE.search(nm.group(1))
        detail["version"] = m.group(1)
        if m.group(2) != expect:
            problems.append("N gives elf %s, the image built is %s" % (m.group(2), expect))
    if not vm or (nm and vm.group(1) != nm.group(1)):
        problems.append("V does not give the same identity as N")
    if len(banner) < 5:
        problems.append("banner has %d lines, not 5" % len(banner))
    if not any("CANSTAT 0x80" in ln for ln in banner):
        problems.append("banner does not show CANSTAT 0x80")
    bad = console_violations(text)
    if bad:
        problems.append("lines that are neither replies nor console lines: %r" % bad[:5])
    return problems, detail


def link_works():
    """canx2 up (C, S6, O from the kernel), canchg up: canchg's frame
    reaches the host through the X2 and the host's reaches canchg. The X2
    reader also sees the host's own frame by local loopback, so only
    canchg's ID counts there."""
    up(X2)
    xr = Reader(X2)
    xr.start()
    up(PEER)
    pr = Reader(PEER)
    pr.start()
    ps = open_sock(PEER)
    send(ps, ID_PROBE, b"ATTACHED")
    time.sleep(0.3)
    xs = open_sock(X2)
    send(xs, ID_HOST, b"HOST->X2")
    time.sleep(0.5)
    ps.close()
    xs.close()
    pr.finish()
    xr.finish()
    down(PEER)
    return {"host_got_peer_frame": any(c == ID_PROBE for _, c, _ in xr.frames),
            "peer_got_host_frame": any(c == ID_HOST for _, c, _ in pr.frames),
            "x2_error_frames": len(xr.errors), "state": can_state(X2)}


def reset_under_kernel(port):
    """Reset the X2 while the kernel's slcan holds its tty: a second open
    of the tty, with no termios change and no flush, sets the modem lines
    as tty_reset does, then puts them back as the kernel's open left them
    (DTR and RTS high). Its close is not the tty's last, so it drops
    nothing."""
    import fcntl
    import termios
    fd = os.open(port, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
    try:
        def mset(on, off):
            if on:
                fcntl.ioctl(fd, termios.TIOCMBIS, struct.pack("I", on))
            if off:
                fcntl.ioctl(fd, termios.TIOCMBIC, struct.pack("I", off))
        mset(0, termios.TIOCM_DTR)
        mset(termios.TIOCM_RTS, 0)
        time.sleep(0.1)
        mset(0, termios.TIOCM_RTS)
        time.sleep(1.5)                 # booted
        mset(termios.TIOCM_DTR | termios.TIOCM_RTS, 0)
    finally:
        os.close(fd)


def check_attach(ctx, can_fail):
    """The boot banner shares the kernel's slcan stream (README.md,
    'Console lines'; charge-interposer reviewer, 2026-11-12):

    after: the X2 reset with nobody on the port, then the kernel attached
      and up. Output written while no host holds the port does not reach
      the next opener (seen on the bench, 2026-11-12), so the kernel's
      counters stay 0 and the link works.
    through: the kernel attached and up (it drops input while down), then
      the X2 reset under it. The boot output lands in the kernel's stream:
      the ROM's one long line counts one rx_over_errors (the gap), the boot
      banner nothing: no frame, no rx_errors, no error frame. Exactly one
      also shows the boot output reached the kernel. The X2 is closed after
      its boot (T8a), so canx2 is taken down and up, and the link works.

    --can-fail: the ROM's long line is not allowed for, so the line the
    kernel counts must fail it: a long line in its stream is seen."""
    detail = {}
    problems = []
    # after
    detach()
    s = tty_open(ctx["port"])
    try:
        tty_cmd(s, "C\r")
        tty_reset(s)
    finally:
        s.close()
    time.sleep(2.0)
    attach()
    up(X2)
    time.sleep(1.0)
    after = stats(X2)           # a new netdev: its counters start at attach
    detail["after_boot"] = {"x2_stats": after}
    if after["rx_over_errors"] or after["rx_errors"] or after["rx_packets"]:
        problems.append("after: the kernel counted %r from before it attached" % after)
    link = link_works()
    detail["after_boot"]["link"] = link
    if not (link["host_got_peer_frame"] and link["peer_got_host_frame"]):
        problems.append("after: the link does not work: %r" % link)
    # through
    xr = Reader(X2)
    xr.start()
    s0 = stats(X2)
    reset_under_kernel(ctx["port"])
    time.sleep(0.5)
    xr.finish()
    d = delta(s0, stats(X2))
    allowed = 0 if can_fail else 1      # the ROM's rst:... line
    detail["through_boot"] = {"x2_stats": d, "rom_long_lines_allowed": allowed,
                              "frames": [(hex(c), dd.hex()) for _, c, dd in xr.frames][:10],
                              "error_frames": [(hex(c), dd.hex()) for _, c, dd in xr.errors][:10]}
    if d["rx_over_errors"] != allowed:
        problems.append("through: rx_over_errors %s, not the ROM's %d long line"
                        % (d["rx_over_errors"], allowed))
    if d["rx_errors"] or d["rx_packets"] or xr.frames or xr.errors:
        problems.append("through: the kernel made frames, errors or error frames of "
                        "the boot output: %r" % detail["through_boot"])
    down(X2)
    link = link_works()
    detail["through_boot"]["link"] = link
    if not (link["host_got_peer_frame"] and link["peer_got_host_frame"]):
        problems.append("through: the link does not work after down and up: %r" % link)
    down(X2)
    detach()
    return problems, detail


def check_silence(ctx, can_fail):
    detach()
    attach()
    phases = {}
    if can_fail:
        up(X2)                  # the X2 open where it must be closed
    phases["closed"] = probe_ack()
    down(X2)
    up(X2, listen=True)
    phases["listen"] = probe_ack()
    down(X2)

    up(X2)
    xr = Reader(X2)
    xr.start()
    phases["normal"] = probe_ack()
    up(PEER)
    pr = Reader(PEER)
    pr.start()
    xs = open_sock(X2)
    send(xs, ID_HOST, b"HOST->X2")
    time.sleep(0.5)
    xs.close()
    pr.finish()
    xr.finish()
    down(PEER)
    phases["normal"]["host_got_probe"] = any(c == ID_PROBE for _, c, _ in xr.frames)
    phases["normal"]["peer_got_host_frame"] = any(c == ID_HOST for _, c, _ in pr.frames)
    down(X2)                    # C
    phases["after_close"] = probe_ack()

    problems = []
    for p in ("closed", "listen", "after_close"):
        if phases[p]["acked"]:
            problems.append("%s: canchg's frame was ACKed" % p)
        if not phases[p]["ack_errors"]:
            problems.append("%s: canchg saw no ACK error" % p)
    nm = phases["normal"]
    if not nm["acked"]:
        problems.append("normal: canchg's frame was not ACKed")
    if not nm["host_got_probe"]:
        problems.append("normal: the X2 did not hand canchg's frame to the host")
    if not nm["peer_got_host_frame"]:
        problems.append("normal: the host's frame did not reach canchg")
    return problems, phases


def check_reset(ctx, can_fail):
    detach()
    port = ctx["port"]
    s = tty_open(port)
    r = sock = None
    t_reset = None
    sent = 0
    try:
        tty_cmd(s, "C\r")
        rep = tty_cmd(s, "S6\rO\r")
        detail = {"open_reply": rep.decode("ascii", "replace")}
        # canchg sends a frame every 5 ms from before the reset to after it.
        up(PEER)
        r = Reader(PEER)
        r.start()
        sock = open_sock(PEER)
        sock.setblocking(False)
        t_end = time.monotonic() + 6.0
        s0 = stats(PEER)
        reset_at = time.monotonic() + 1.0
        # The USB link stays up through the chip reset (rst:0x15,
        # USB_UART_CHIP_RESET), so the boot output arrives on this open tty
        # and is read here, with the time each piece came.
        s.timeout = 0
        chunks = []
        while time.monotonic() < t_end:
            if t_reset is None and time.monotonic() >= reset_at:
                s.read(65536)           # the frame lines from before
                t_reset = time.monotonic()
                if not can_fail:
                    tty_reset(s)
            if t_reset is not None:
                got = s.read(65536)
                if got:
                    chunks.append((time.monotonic() - t_reset, got))
            try:
                sock.send(pack(ID_PROBE, struct.pack("<I", sent)))
                sent += 1
            except OSError:
                pass
            time.sleep(0.005)
    finally:
        for c in (sock, s):
            try:
                if c is not None:
                    c.close()
            except Exception:
                pass
        if r is not None:
            r.finish()
    # canchg's ACK errors come up with its state changes (probe_ack): the
    # first after the reset is when its frames stopped being ACKed.
    d = delta(s0, stats(PEER))
    ack_err_times = [t for t, cid, _ in r.errors if cid & ERR_ACK]
    after = [t - t_reset for t in ack_err_times if t >= t_reset]
    before = [t for t in ack_err_times if t < t_reset]
    detail.update({"peer_tx_packets": d["tx_packets"], "sent_attempts": sent,
                   "ack_errors_before_reset": len(before),
                   "ack_errors_after_reset": len(after),
                   "peer_state": can_state(PEER),
                   "first_ack_error_after_reset_s": round(after[0], 3) if after else None})
    down(PEER)
    # Once back, silent: a fresh probe gets no ACK.
    time.sleep(2.0)
    detail["after_reset"] = probe_ack()
    boot = b"".join(c for _, c in chunks).decode("ascii", "replace")
    detail["boot_output"] = boot
    t_line = None
    acc = ""
    for t, c in chunks:
        acc += c.decode("ascii", "replace")
        if BOOT_LINE in acc:
            t_line = round(t, 3)
            break
    detail["boot_line_after_reset_s"] = t_line
    rom, boot_text = split_boot(boot)
    # frame lines still in flight at the reset are not the ROM's
    detail["frame_lines_before_boot"] = [ln for ln in rom if REPLY_RE.match(ln)]
    rom = [ln for ln in rom if not REPLY_RE.match(ln)]
    detail["rom_lines_gap"] = rom
    problems = boot_problems(boot_text, need_boot_line=True)
    if not d["tx_packets"]:
        problems.append("canchg sent nothing while the X2 was open")
    if not after:
        problems.append("canchg's frames were still ACKed after the reset")
    if before:
        problems.append("%d ACK errors before the reset, while the X2 was open" % len(before))
    if detail["after_reset"]["acked"]:
        problems.append("after the reset canchg's frame was ACKed: not silent")
    if not detail["after_reset"]["ack_errors"]:
        problems.append("after the reset canchg saw no ACK error")
    return problems, detail


def seq_frames(frames):
    return [struct.unpack("<I", d[:4])[0] for _, c, d in frames
            if c == ID_SEQ and len(d) == 8]


def order_problems(seqs, label):
    for a, b in zip(seqs, seqs[1:]):
        if b <= a:
            return ["%s: out of order (%d after %d)" % (label, b, a)]
    return []


def peak_fps(frames):
    ts = [t for t, c, _ in frames if c == ID_SEQ]
    if not ts:
        return 0
    best, j = 0, 0
    for i in range(len(ts)):
        while ts[i] - ts[j] > 1.0:
            j += 1
        best = max(best, i - j + 1)
    return best


def paced_send(sock, fps, seconds):
    """Frames with a sequence number at fps for seconds. A frame the host
    cannot queue within 50 ms is counted lost; after 200 in a row the
    sending stops (nobody is taking frames: a can-fail, or a stuck bus)."""
    n = int(fps * seconds)
    sent, retries, lost, in_row = 0, 0, 0, 0
    t0 = time.monotonic()
    for i in range(n):
        target = t0 + i / fps
        while True:
            now = time.monotonic()
            if now >= target:
                break
            time.sleep(min(target - now, 0.002))
        r = send(sock, ID_SEQ, struct.pack("<II", i, i ^ 0xFFFFFFFF), 0.05)
        if r < 0:
            lost += 1
            in_row += 1
            if in_row >= 200:
                break
        else:
            sent += 1
            retries += r
            in_row = 0
    return sent, lost, retries, time.monotonic() - t0


def check_rate_tx(ctx, can_fail):
    detach()
    attach()
    up(PEER, listen=can_fail)
    up(X2)
    pr = Reader(PEER)
    pr.start()
    xr = Reader(X2)
    xr.start()
    s0 = stats(X2)
    sock = open_sock(X2)
    sent, lost, retries, elapsed = paced_send(sock, ctx["fps"], ctx["seconds"])
    sock.close()
    try:
        wait_quiet(pr, 2.0, 60.0)
    except Fail:
        pass
    pr.finish()
    xr.finish()
    d = delta(s0, stats(X2))
    down(X2)
    down(PEER)
    seqs = seq_frames(pr.frames)
    reported = d["tx_errors"]
    tx_over = err_count(xr.errors, ERR_CRTL, CRTL_TX_OVERFLOW)
    detail = {"fps": ctx["fps"], "seconds": ctx["seconds"], "sent": sent,
              "host_not_sent": lost, "host_retries": retries,
              "send_rate": round(sent / elapsed, 1), "received": len(seqs),
              "reported_drops": reported, "tx_overflow_error_frames": tx_over,
              "peak_fps_1s": peak_fps(pr.frames), "x2_stats": d}
    problems = []
    if lost:
        problems.append("%d frames the host could not send" % lost)
    if sent / elapsed < 0.98 * ctx["fps"]:
        problems.append("sent at %.0f/s, below %.0f/s" % (sent / elapsed, ctx["fps"]))
    unreported = sent - len(seqs) - (reported or 0)
    if unreported:
        problems.append("%d frames neither received nor reported" % unreported)
    if reported:
        problems.append("%d frames dropped (reported)" % reported)
    if reported != tx_over:
        problems.append("tx_errors %s but %d TX_OVERFLOW error frames" % (reported, tx_over))
    problems += order_problems(seqs, "canchg")
    return problems, detail


def check_rate_rx(ctx, can_fail):
    detach()
    attach()
    up(PEER)
    up(X2, listen=can_fail)
    xr = Reader(X2)
    xr.start()
    s0p, s0x = stats(PEER), stats(X2)
    sock = open_sock(PEER)
    sent, lost, retries, elapsed = paced_send(sock, ctx["fps"], ctx["seconds"])
    sock.close()
    try:
        wait_quiet(xr, 2.0, 60.0)
    except Fail:
        pass
    time.sleep(1.0)
    xr.finish()
    dp, dx = delta(s0p, stats(PEER)), delta(s0x, stats(X2))
    down(X2)
    down(PEER)
    seqs = seq_frames(xr.frames)
    acked = dp["tx_packets"]
    overruns = err_count(xr.errors, ERR_CRTL, CRTL_RX_OVERFLOW)
    detail = {"fps": ctx["fps"], "seconds": ctx["seconds"], "written": sent,
              "peer_not_written": lost, "peer_acked": acked,
              "received_by_host": len(seqs), "overrun_reports": overruns,
              "rx_over_errors": dx["rx_over_errors"],
              "peak_fps_1s": peak_fps(xr.frames), "x2_stats": dx}
    problems = []
    want = 0.98 * ctx["fps"] * ctx["seconds"]
    if (acked or 0) < want:
        problems.append("canchg got %s frames ACKed, fewer than %.0f" % (acked, want))
    if len(seqs) != acked:
        problems.append("host received %d of the %s frames canchg sent" % (len(seqs), acked))
    if overruns or dx["rx_over_errors"]:
        problems.append("%d overrun reports" % overruns)
    problems += order_problems(seqs, "host")
    return problems, detail


def check_drops(ctx, can_fail):
    detach()
    attach()
    down(PEER)
    if can_fail:
        up(PEER)                    # an ACKer throughout: nothing held
    up(X2)
    xr = Reader(X2)
    xr.start()
    s0 = stats(X2)
    sock = open_sock(X2)
    n = 700
    sent = 0
    for i in range(n):
        if send(sock, ID_SEQ, struct.pack("<II", i, i ^ 0xFFFFFFFF), 2.0) >= 0:
            sent += 1
        time.sleep(0.001)
    time.sleep(3.0)
    held_state = can_state(X2)
    d_held = delta(s0, stats(X2))
    # Now an ACKer: what the X2 held drains to canchg. The reader is bound
    # before canchg comes up, or the first frames drain before it listens
    # (10 of 513 missed so on the bench, 2026-11-12).
    pr = Reader(PEER)
    pr.start()
    if not can_fail:
        up(PEER)
    try:
        wait_quiet(pr, 2.0, 30.0)
    except Fail:
        pass
    time.sleep(1.0)
    sock.close()
    pr.finish()
    xr.finish()
    end_state = can_state(X2)
    d = delta(s0, stats(X2))
    down(X2)
    down(PEER)
    seqs = seq_frames(pr.frames)
    reported = d["tx_errors"]
    tx_over = err_count(xr.errors, ERR_CRTL, CRTL_TX_OVERFLOW)
    passive = err_count(xr.errors, ERR_CRTL, CRTL_TX_PASSIVE)
    active = err_count(xr.errors, ERR_CRTL, CRTL_ACTIVE)
    detail = {"sent": sent, "state_while_held": held_state,
              "reported_while_held": d_held["tx_errors"], "reported": reported,
              "tx_overflow_error_frames": tx_over, "drained_to_peer": len(seqs),
              "passive_reports": passive, "active_reports": active,
              "state_at_end": end_state}
    problems = []
    if not reported:
        problems.append("no frame was reported dropped")
    if reported != tx_over:
        problems.append("tx_errors %s but %d TX_OVERFLOW error frames" % (reported, tx_over))
    if len(seqs) + (reported or 0) != sent:
        problems.append("received %d + reported %s != sent %d" % (len(seqs), reported, sent))
    if held_state != "ERROR-PASSIVE" or not passive:
        problems.append("held without an ACKer: state %s, %d passive reports" % (held_state, passive))
    if end_state != "ERROR-ACTIVE" or not active:
        problems.append("after draining: state %s, %d active reports" % (end_state, active))
    problems += order_problems(seqs, "canchg")
    return problems, detail


def check_busoff(ctx, can_fail):
    detach()
    attach()
    # canchg at the wrong rate flags errors in the X2's frames (X2 TEC +8
    # each) until its own receive errors make it error-passive, after which
    # its error flags no longer destroy anything (seen on the bench,
    # 2026-11-12: the X2 stopped at error-passive). Taking canchg down and up
    # resets its counters, so each cycle destroys more of the X2's frames
    # while the X2's TEC, with no frame sent successfully, only climbs.
    rate = BITRATE if can_fail else 250000
    up(X2)
    xr = Reader(X2)
    xr.start()
    sock = open_sock(X2)
    t0 = time.monotonic()
    for i in range(20):
        send(sock, ID_HOST, bytes([i]))
    cycles = 0
    for cycles in range(1, 13):
        up(PEER, bitrate=rate)
        time.sleep(0.3)
        down(PEER)
        if any(cid & ERR_BUSOFF for _, cid, _ in xr.errors):
            break
    time.sleep(1.0)
    sock.close()
    xr.finish()
    busoff = [t - t0 for t, cid, _ in xr.errors if cid & ERR_BUSOFF]
    state = can_state(X2)
    down(X2)
    down(PEER)
    detail = {"busoff_reports": len(busoff), "peer_rate": rate,
              "peer_cycles": cycles,
              "first_busoff_s": round(busoff[0], 3) if busoff else None,
              "state": state, "error_frames": len(xr.errors)}
    problems = []
    if not busoff:
        problems.append("no bus-off reported (state %s)" % state)
    return problems, detail


def check_overrun(ctx, can_fail):
    detach()
    s = tty_open(ctx["port"])
    got = b""
    try:
        tty_cmd(s, "C\r")
        tty_cmd(s, "F\r")           # clear the flags
        tty_cmd(s, "S6\rO\r")
        up(PEER)
        s0 = stats(PEER)
        sock = open_sock(PEER)
        stop = threading.Event()
        res = {}

        def tx():
            res["r"] = paced_send(sock, ctx["fps"], 20.0)
            stop.set()

        t = threading.Thread(target=tx, daemon=True)
        t.start()
        if not can_fail:
            time.sleep(15.0)            # the tty not read
        while not stop.is_set():
            got += s.read(65536)
        t.join()
        got += tty_drain(s, 5.0)
        sock.close()
        dp = delta(s0, stats(PEER))
        down(PEER)
        flags = tty_cmd(s, "F\r")
        tty_cmd(s, "C\r")
    finally:
        s.close()
    text = got.decode("ascii", "replace")
    frames = re.findall(r"T18FECA008([0-9A-F]{16})\r", text)
    seqs = [struct.unpack("<I", bytes.fromhex(h[:8]))[0] for h in frames]
    reports = text.count("e3ooo\r")
    fm = re.search(r"F([0-9A-F]{2})\r", flags.decode("ascii", "replace"))
    fval = int(fm.group(1), 16) if fm else None
    detail = {"peer_acked": dp["tx_packets"], "frames_read": len(seqs),
              "overrun_reports": reports, "F": fval}
    problems = []
    if not reports:
        problems.append("no overrun reported")
    if len(seqs) + reports != dp["tx_packets"]:
        problems.append("frames %d + reports %d != frames canchg sent %s"
                        % (len(seqs), reports, dp["tx_packets"]))
    if fval is None or not fval & 0x01:
        problems.append("F does not show the receive-FIFO bit: %r" % fval)
    problems += order_problems(seqs, "host")
    return problems, detail


# ---------------------------------------------------------------- jamming

def open_sender(name):
    """A sending socket whose frames are not looped back to this host's
    other sockets (CAN_RAW_LOOPBACK 0), so the X2-side reader sees only what
    the image sent, never the host's own jam frames."""
    s = open_sock(name)
    s.setsockopt(socket.SOL_CAN_RAW, socket.CAN_RAW_LOOPBACK, 0)
    return s


def jam_exchange(sock, reader, joiner, can_id, data, want_kinds, timeout=3.0):
    """Send one frame on can_id through the kernel's slcan and collect the
    reply lines (joined from reply frames in reader) until one of
    want_kinds arrives or timeout. Returns the new lines."""
    seen = len(reader.frames)
    send(sock, can_id | EFF, data)
    lines = []
    t_end = time.monotonic() + timeout
    while time.monotonic() < t_end:
        while seen < len(reader.frames):
            _, cid, d = reader.frames[seen]
            seen += 1
            if cid == jam_host.REPLY_ID | EFF:
                line = joiner.feed(d)
                if line is not None:
                    lines.append(line)
        if any((jam_host.parse_reply(ln) or {}).get("kind") in want_kinds for ln in lines):
            break
        time.sleep(0.02)
    return lines


def reply_of(lines, kind, sub=None):
    for ln in lines:
        r = jam_host.parse_reply(ln)
        if r and r["kind"] == kind and (sub is None or r["sub"] == sub):
            return r
    return None


def jam_frames_on(frames):
    """Frames on either jam identifier among frames (a reader's)."""
    ids = (jam_host.CMD_ID | EFF, jam_host.REPLY_ID | EFF, (jam_host.CMD_ID + 1) | EFF)
    return [(round(t, 3), hex(c), d.hex()) for t, c, d in frames if c in ids]


def tty_jam_listen_only(port, cmd_id, data):
    """One jam command on the raw tty (canx2 detached) with the X2 open
    listen-only (C, S6, L), then C. Returns (reply lines joined from the
    reply-ID frames the image wrote, the raw bytes read)."""
    s = tty_open(port)
    try:
        tty_drain(s, 0.3)
        tty_cmd(s, "C\rS6\rL\r", 0.3)
        got = tty_cmd(s, "T%08X%d%s\r" % (cmd_id, len(data), data.hex().upper()), 1.0)
        tty_cmd(s, "C\r", 0.2)
    finally:
        s.close()
    j = jam_host.Joiner()
    lines = []
    for m in re.finditer(r"T%08X([0-8])([0-9A-F]*)\r" % jam_host.REPLY_ID,
                         got.decode("ascii", "replace")):
        n = int(m.group(1))
        line = j.feed(bytes.fromhex(m.group(2)[:2 * n]))
        if line is not None:
            lines.append(line)
    return lines, got


def check_jam_channel(ctx, can_fail):
    """The jam channel through the kernel's slcan (SPEC T13): each command
    frame is answered by reply frames that join into jam_pulse's lines; a
    frame that does not decode, and a host frame on the reply identifier,
    are answered ERR; the 1 s TRAIN ends as the image must end it
    (STOP_EXPECT: on dev, through either stop at the deadline; jammer/README
    "end=", and a TRAIN that reaches its deadline is never end=complete on
    the bench); the channel works with the X2 open listen-only too, shown on
    the raw tty, since Linux's slcan sends nothing while listen-only (seen on
    the bench 2026-11-13: the command counted in canx2's tx_dropped, no
    reply; slcan_netdev_xmit's can_dev_dropped_skb()); and no jam frame ever
    reaches CAN2 (canchg, listening, sees none).
    can-fail: the commands sent on 0x1F4A4D44, beside the command
    identifier: the image must not take them, so no replies come and canchg
    sees them."""
    cmd_id = jam_host.CMD_ID + 1 if can_fail else jam_host.CMD_ID
    name = image_name(ctx)
    attach()
    up(PEER)
    pr = Reader(PEER)
    pr.start()
    up(X2)
    xr = Reader(X2)
    xr.start()
    s0 = stats(X2)
    sock = open_sender(X2)
    j = jam_host.Joiner()
    steps = {}
    try:
        steps["status"] = jam_exchange(sock, xr, j, cmd_id, jam_host.status(), ("STATUS",))
        bad = bytearray(jam_host.status())
        bad[1] = ord("X")
        steps["bad_signature"] = jam_exchange(sock, xr, j, cmd_id, bytes(bad), ("ERR",))
        steps["reply_id_from_host"] = jam_exchange(
            sock, xr, j, jam_host.REPLY_ID, jam_host.status(), ("ERR",))
        steps["ms_zero"] = jam_exchange(sock, xr, j, cmd_id, jam_host.train(20, 1000, 0), ("ERR",))
        steps["train_1s"] = jam_exchange(sock, xr, j, cmd_id, jam_host.train(20, 1000, 1000),
                                         ("DONE", "ERR"), 5.0)
        steps["stop_idle"] = jam_exchange(sock, xr, j, cmd_id, jam_host.stop(), ("ACK",))
    finally:
        sock.close()
        time.sleep(0.5)
        xr.finish()
    d = delta(s0, stats(X2))
    down(X2)
    detach()
    try:
        steps["status_listen_only"], raw_l = tty_jam_listen_only(ctx["port"], cmd_id,
                                                                 jam_host.status())
    finally:
        time.sleep(0.5)
        pr.finish()
        down(PEER)
    on_can2 = jam_frames_on(pr.frames)
    expect = {
        "status": reply_of(steps["status"], "STATUS", "idle"),
        "bad_signature": reply_of(steps["bad_signature"], "ERR", "bad_jam_frame"),
        "reply_id_from_host": reply_of(steps["reply_id_from_host"], "ERR", "bad_jam_frame"),
        "ms_zero": reply_of(steps["ms_zero"], "ERR", "ms_zero"),
        "train_1s": reply_of(steps["train_1s"], "ACK", "TRAIN")
                    and reply_of(steps["train_1s"], "DONE", "TRAIN"),
        "stop_idle": reply_of(steps["stop_idle"], "ACK", "STOP"),
        "status_listen_only": reply_of(steps["status_listen_only"], "STATUS", "idle"),
    }
    done = reply_of(steps["train_1s"], "DONE", "TRAIN") or {}
    detail = {"command_id": hex(cmd_id), "replies": steps, "joiner_bad": j.bad,
              "jam_frames_on_can2": on_can2[:10], "jam_frames_on_can2_count": len(on_can2),
              "canchg_error_frames": len(pr.errors), "x2_stats": d,
              "train_done": done, "image": name,
              "listen_only_raw_tty": raw_l.decode("ascii", "replace")[:600]}
    problems = ["%s: no %s reply (got %r)" % (k, k, steps[k]) for k, v in expect.items() if not v]
    if done and not STOP_EXPECT[name](done):
        problems.append("TRAIN 1 s ended end=%s stop_by=%s, not as %s must" % (
            done.get("end"), done.get("stop_by"), name))
    if j.bad:
        problems.append("%d reply frames out of form" % j.bad)
    if on_can2:
        problems.append("%d jam frames reached CAN2 (canchg saw them)" % len(on_can2))
    if d["tx_errors"]:
        problems.append("kernel tx_errors %d" % d["tx_errors"])
    return problems, detail


def check_jam_concurrent(ctx, can_fail):
    """Jamming and the SLCAN at the same time (SPEC T13): a TRAIN on CAN1
    for the whole of a rate-tx run (host -> canx2 -> canchg at --fps for
    --seconds, at most 30 s), commanded and answered through the same
    kernel slcan: every frame arrives at canchg, in order, none dropped, the
    TRAIN is ACKed and DONE, and canchg reports no error state (jamming CAN1
    puts nothing on CAN2). can-fail: no TRAIN sent, so no ACK or DONE."""
    seconds = min(ctx["seconds"], 30.0)
    name = image_name(ctx)
    attach()
    up(PEER)
    up(X2)
    pr = Reader(PEER)
    pr.start()
    xr = Reader(X2)
    xr.start()
    s0 = stats(X2)
    sock = open_sender(X2)
    j = jam_host.Joiner()
    ack = []
    if not can_fail:
        # p 1000 us: 1,000 pulses a second on CAN1 for the whole run
        ack = jam_exchange(sock, xr, j, jam_host.CMD_ID,
                           jam_host.train(20, 1000, int(seconds * 1000) + 500), ("ACK", "ERR"))
    sent, lost, retries, elapsed = paced_send(sock, ctx["fps"], seconds)
    time.sleep(3.0)             # the TRAIN ends 0.5 s after the frames; its DONE
    sock.close()
    try:
        wait_quiet(pr, 2.0, 60.0)
    except Fail:
        pass
    pr.finish()
    xr.finish()
    # join every reply frame the host got, in order
    j2 = jam_host.Joiner()
    for _, cid, d in xr.frames:
        if cid == jam_host.REPLY_ID | EFF:
            j2.feed(d)
    d = delta(s0, stats(X2))
    peer_state = can_state(PEER)
    down(X2)
    down(PEER)
    seqs = seq_frames(pr.frames)
    reported = d["tx_errors"]
    done = reply_of(j2.lines, "DONE", "TRAIN")
    detail = {"fps": ctx["fps"], "seconds": seconds, "sent": sent,
              "host_not_sent": lost, "received": len(seqs), "reported_drops": reported,
              "replies": j2.lines, "joiner_bad": j2.bad,
              "canchg_error_frames": len(pr.errors), "canchg_state": peer_state,
              "jam_frames_on_can2": len(jam_frames_on(pr.frames)), "x2_stats": d,
              "image": name}
    problems = []
    if not reply_of(j2.lines, "ACK", "TRAIN"):
        problems.append("TRAIN not ACKed (replies %r)" % j2.lines)
    if not done:
        problems.append("no DONE TRAIN")
    elif not STOP_EXPECT[name](done):
        problems.append("TRAIN ended end=%s stop_by=%s, not as %s must" % (
            done.get("end"), done.get("stop_by"), name))
    if lost:
        problems.append("%d frames the host could not send" % lost)
    if sent - len(seqs):
        problems.append("%d frames sent but not received by canchg" % (sent - len(seqs)))
    if reported:
        problems.append("%d frames dropped (reported)" % reported)
    problems += order_problems(seqs, "canchg")
    if j2.bad:
        problems.append("%d reply frames out of form" % j2.bad)
    if pr.errors or peer_state != "ERROR-ACTIVE":
        problems.append("canchg: %d error frames, state %s" % (len(pr.errors), peer_state))
    if detail["jam_frames_on_can2"]:
        problems.append("jam frames reached CAN2")
    return problems, detail


# ---------------------------------------------------------------- jamming, judged by CAN1's witness

def image_name(ctx):
    """The image's name from its N reply on the raw tty (canx2 detached)."""
    detach()
    s = tty_open(ctx["port"])
    try:
        tty_drain(s, 0.3)
        n = tty_cmd(s, "N\r").decode("ascii", "replace")
    finally:
        s.close()
    m = re.search(r"N(" + NAME_RE + r") ", n)
    if not m:
        raise Fail("no slcan_spi identity from N: %r" % n)
    return m.group(1)


def feed_replies(reader, joiner):
    """Feeds the joiner the reply frames reader got since it last looked."""
    start = getattr(joiner, "seen_frames", 0)
    frames = reader.frames
    end = len(frames)
    for _, cid, d in frames[start:end]:
        if cid == jam_host.REPLY_ID | EFF:
            joiner.feed(d)
    joiner.seen_frames = end


def open_witness(ctx):
    """CAN1's witness, or Fail (the check NOT-RUN) if it cannot be the
    bench's Kvaser. Since 2026-11-13 the kernel's kvaser_usb: its link
    brought up here in normal mode at BITRATE through canctl (up(): down
    first if up, so no earlier opener's listen-only is kept), and
    witness.open_kvaser reads IFF_UP back before it opens netdev kvaser."""
    try:
        up(witness.NETDEV)
        return witness.open_kvaser()
    except Exception as e:
        raise Fail("CAN1 witness: %s" % e)


def wit_errs(wit, closing=None):
    """The witness's error-frame times, or Fail (the check NOT-RUN) if it
    did not hear the whole check (Dead), its clock is off (Void), or the
    closing pulse sent in closing = (t0, t1) was not counted exactly once
    (INCONCLUSIVE: this run's witness may be deaf)."""
    try:
        errs = wit.err_times()
    except witness.Dead as e:
        raise Fail("witness dead: %s; %s" % (e, wit.summary()))
    except witness.Void as e:
        raise Fail("VOID: %s; %s" % (e, wit.summary()))
    if closing is None:
        raise Fail("INCONCLUSIVE: no closing pulse was sent; %s" % wit.summary())
    ok, why = witness.judge_closing(errs, *closing)
    if not ok:
        raise Fail("INCONCLUSIVE: %s; %s" % (why, wit.summary()))
    return errs


class JamSession:
    """canx2 attached and up normal (canchg down: the X2 alone on the
    charger segment), CAN1's witness running, a sender and a reader on
    canx2, and the reply joiner. cmd() sends one command frame and returns
    (host epoch time it was sent, its reply lines)."""

    def __init__(self, ctx):
        self.name = image_name(ctx)
        attach()
        down(PEER)
        up(X2)
        self.wit = open_witness(ctx)
        self.wit.start()
        self.xr = Reader(X2)
        self.xr.start()
        self.sock = open_sender(X2)
        self.j = jam_host.Joiner()
        self.closing_window = None
        time.sleep(0.5)

    def cmd(self, data, want, timeout=3.0):
        feed_replies(self.xr, self.j)
        n0 = len(self.j.lines)
        t = time.time()
        send(self.sock, jam_host.CMD_ID | EFF, data)
        t_end = time.monotonic() + timeout
        while time.monotonic() < t_end:
            feed_replies(self.xr, self.j)
            if any((jam_host.parse_reply(ln) or {}).get("kind") in want
                   for ln in self.j.lines[n0:]):
                break
            time.sleep(0.02)
        return t, self.j.lines[n0:]

    def wait_for(self, kind, sub, timeout):
        """Waits for a reply line of kind (and sub) among all replies so far;
        returns the parsed reply or None."""
        t_end = time.monotonic() + timeout
        while True:
            feed_replies(self.xr, self.j)
            r = reply_of(self.j.lines, kind, sub)
            if r or time.monotonic() >= t_end:
                return r
            time.sleep(0.02)

    def closing(self):
        """The closing pulse, after the check's last window: one PULSES 20 1,
        counted from its send to 1 s after its DONE."""
        t, _ = self.cmd(jam_host.pulses(20, 1, 10), ("DONE", "ERR"), 3.0)
        time.sleep(1.0)
        self.closing_window = (t - 0.05, time.time())

    def finish(self):
        self.sock.close()
        time.sleep(0.3)
        self.xr.finish()
        feed_replies(self.xr, self.j)
        self.wit.finish()
        down(X2)
        return self

    def errs(self):
        return wit_errs(self.wit, self.closing_window)


def check_jam_boot_silence(ctx, can_fail):
    """T13 "it boots silent": the X2 reset 3 times through its USB port; CAN1's
    witness sees no error frame from each reset to 5 s after the image's
    boot line. can-fail: one PULSES 20 1 10, sent as a command frame on the
    raw tty after the third boot line, which the witness must see. The
    closing pulse is sent on the raw tty the same way, after the third
    window."""
    detach()
    down(PEER)
    wit = open_witness(ctx)
    wit.start()
    windows = []
    s = tty_open(ctx["port"])
    try:
        for i in range(3):
            t0 = time.time()
            tty_reset(s)
            got = b""
            t_end = time.monotonic() + 5.0
            while BOOT_LINE.encode() not in got and time.monotonic() < t_end:
                got += s.read(4096)
            t_boot = time.time() if BOOT_LINE.encode() in got else None
            if can_fail and i == 2:
                line = "T%08X8%s\r" % (jam_host.CMD_ID, jam_host.pulses(20, 1, 10).hex().upper())
                s.write(line.encode())
            time.sleep(5.0)
            windows.append((t0, (t_boot or t0 + 5.0) + 5.0, t_boot is not None))
        tty_drain(s, 0.5)
        # the closing pulse, after the third window
        tc = time.time()
        s.write(("T%08X8%s\r" % (jam_host.CMD_ID, jam_host.pulses(20, 1, 10).hex().upper())).encode())
        tty_drain(s, 1.5)
        closing = (tc - 0.05, time.time())
    finally:
        s.close()
        wit.finish()
    errs = wit_errs(wit, closing)
    counts = [witness.judge_count(errs, a, b, 0)[1] for a, b, _ in windows]
    detail = {"windows": [(round(a, 3), round(b, 3), ok) for a, b, ok in windows],
              "error_frames": counts, "closing": [round(x, 3) for x in closing],
              "witness_record": wit.summary()}
    problems = []
    if not all(ok for _, _, ok in windows):
        problems.append("a reset with no boot line")
    if any(counts):
        problems.append("CAN1 error frames after resets: %s" % counts)
    return problems, detail


def check_jam_port(ctx, can_fail):
    """One pulse through the jam channel is exactly one error frame on CAN1
    (the gen-inhibit tester's port check, asked for slcan_spi itself,
    2026-11-13), counted from the command to 1 s after its DONE (the
    window up to the next command). can-fail: two pulses sent."""
    js = JamSession(ctx)
    n = 2 if can_fail else 1
    try:
        t, lines = js.cmd(jam_host.pulses(20, n, 300), ("DONE", "ERR"), 5.0)
        time.sleep(1.0)
        t_end = time.time()
        js.closing()
    finally:
        js.finish()
    errs = js.errs()
    ok, got = witness.judge_count(errs, t - 0.05, t_end, 1)
    detail = {"image": js.name, "pulses_sent": n, "replies": lines, "error_frames": got,
              "witness_record": js.wit.summary()}
    problems = [] if ok else ["%d error frames for one pulse" % got]
    if not reply_of(lines, "DONE", "PULSES"):
        problems.append("no DONE PULSES")
    return problems, detail


def check_jam_calibration(ctx, can_fail):
    """10 pulses at 1 s: 10 groups of one error frame, 1 s apart; then the
    control, 9 at 1.1 s, which must read as 9 at 1.1 s and not as 10 at 1 s
    (the gen-inhibit tester's short calibration, 2026-11-13). can-fail: the
    10 at 1 s judged as 9 at 1.1 s."""
    js = JamSession(ctx)
    try:
        t1, l1 = js.cmd(jam_host.pulses(20, 10, 1000), ("DONE", "ERR"), 15.0)
        time.sleep(1.0)
        e1 = time.time()
        t2, l2 = js.cmd(jam_host.pulses(20, 9, 1100), ("DONE", "ERR"), 15.0)
        time.sleep(1.0)
        e2 = time.time()
        js.closing()
    finally:
        js.finish()
    errs = js.errs()
    want = (9, 1.1) if can_fail else (10, 1.0)
    ok1, why1 = witness.judge_groups(errs, t1, e1, *want)
    ok2, why2 = witness.judge_groups(errs, t2, e2, 9, 1.1)
    ctl, whyc = witness.judge_groups(errs, t2, e2, 10, 1.0)
    detail = {"image": js.name, "ten_at_1s": why1, "nine_at_1_1s": why2,
              "control_as_ten": whyc, "replies": l1 + l2,
              "witness_record": js.wit.summary()}
    problems = []
    if not ok1:
        problems.append("10 at 1 s: %s" % why1)
    if not ok2:
        problems.append("9 at 1.1 s: %s" % why2)
    if ctl:
        problems.append("the 9-at-1.1 s control passed as 10 at 1 s")
    return problems, detail


# What ends a TRAIN at its deadline on each image (README.md "Variants").
STOP_EXPECT = {
    "slcan_spi_NOT_FOR_VEHICLE": lambda d: d.get("stop_by") == "deadline_timer"
    or d.get("end") == "deadline_layer_b",
    "slcan_spi_a_NOT_FOR_VEHICLE": lambda d: d.get("stop_by") == "deadline_timer",
    "slcan_spi_b_NOT_FOR_VEHICLE": lambda d: d.get("end") == "deadline_layer_b"
    and d.get("stop_by") == "none",
}


def judge_train_stop(js, t_sent, ms, can_fail, quiet_s=3.0):
    """The witness's verdict on a TRAIN sent at t_sent for ms: the last error
    frame within 0.5 s of the deadline and none from then to quiet_s after it
    (past stop_a_only's backstop at deadline + 2 s). can-fail: the deadline
    shifted 2 s early."""
    return witness.judge_stop(js.errs(), **witness.stop_windows(
        t_sent, t_sent + ms / 1000.0, 2.0 if can_fail else 0.0, quiet_s))


def check_jam_stop(ctx, can_fail):
    """A sparse TRAIN (W 20 us, P 5000 us, 3 s) stops at its deadline, judged
    by the witness (T13's stops at the deadline). Which stop must end it
    depends on the image: dev, either; slcan_spi_a, stop (a), the
    esp_timer, alone (stop_by=deadline_timer); slcan_spi_b, stop (b), the
    chunk sizing, alone (end=deadline_layer_b). can-fail: the same record
    judged against a deadline 2 s early."""
    js = JamSession(ctx)
    try:
        t, _ = js.cmd(jam_host.train(20, 5000, 3000), ("ACK", "ERR"))
        done = js.wait_for("DONE", "TRAIN", 8.0)
        time.sleep(3.5)
        js.closing()
    finally:
        js.finish()
    verdict, why = judge_train_stop(js, t, 3000, can_fail)
    detail = {"image": js.name, "witness": verdict, "why": why, "done": done,
              "replies": js.j.lines, "witness_record": js.wit.summary()}
    problems = []
    if verdict != "PASS":
        problems.append("witness %s: %s" % (verdict, why))
    if not done:
        problems.append("no DONE TRAIN")
    elif not STOP_EXPECT[js.name](done):
        problems.append("%s: ended end=%s stop_by=%s" % (js.name, done.get("end"), done.get("stop_by")))
    return problems, detail


def check_jam_cap(ctx, can_fail):
    """A TRAIN asked for 60 s is capped at 30 s (T13: every command capped at
    30 s): CAPPED, and the witness's last error frame within 0.5 s of 30 s.
    can-fail: judged against a deadline 2 s early."""
    js = JamSession(ctx)
    try:
        t, _ = js.cmd(jam_host.train(20, 5000, 60000), ("ACK", "ERR"))
        done = js.wait_for("DONE", "TRAIN", 35.0)
        time.sleep(3.5)
        js.closing()
    finally:
        js.finish()
    verdict, why = judge_train_stop(js, t, 30000, can_fail)
    detail = {"image": js.name, "witness": verdict, "why": why, "done": done,
              "replies": js.j.lines, "witness_record": js.wit.summary()}
    problems = []
    if verdict != "PASS":
        problems.append("witness %s: %s" % (verdict, why))
    if not reply_of(js.j.lines, "CAPPED"):
        problems.append("no CAPPED reply")
    if not done:
        problems.append("no DONE TRAIN")
    return problems, detail


def check_jam_stop_cmd(ctx, can_fail):
    """STOP mid-train: a 20 s TRAIN, STOP sent 3 s in; the witness's last
    error frame within 0.5 s of when the host sent STOP (not of the reply,
    gen-inhibit tester 2026-11-13) and none for 3 s after; DONE end=stopped
    stop_by=stop_cmd. can-fail: judged against a STOP 2 s earlier."""
    js = JamSession(ctx)
    try:
        t, _ = js.cmd(jam_host.train(20, 5000, 20000), ("ACK", "ERR"))
        time.sleep(3.0)
        t_stop, _ = js.cmd(jam_host.stop(), ("ACK", "ERR"))
        done = js.wait_for("DONE", "TRAIN", 3.0)
        time.sleep(3.5)
        js.closing()
    finally:
        js.finish()
    verdict, why = witness.judge_stop(js.errs(), **witness.stop_windows(
        t, t_stop, 2.0 if can_fail else 0.0, 3.0))
    ack = reply_of(js.j.lines, "ACK", "STOP")
    detail = {"image": js.name, "witness": verdict, "why": why, "done": done,
              "ack_stop": ack, "replies": js.j.lines,
              "witness_record": js.wit.summary()}
    problems = []
    if verdict != "PASS":
        problems.append("witness %s: %s" % (verdict, why))
    if not ack or ack.get("busy") != "1":
        problems.append("STOP not ACKed busy=1: %r" % ack)
    if not done or done.get("end") != "stopped" or done.get("stop_by") != "stop_cmd":
        problems.append("DONE not end=stopped stop_by=stop_cmd: %r" % done)
    return problems, detail


RUNNERS = {"identity": check_identity, "attach": check_attach,
           "silence": check_silence,
           "reset": check_reset, "rate-tx": check_rate_tx,
           "rate-rx": check_rate_rx, "drops": check_drops,
           "busoff": check_busoff, "overrun": check_overrun,
           "jam-channel": check_jam_channel, "jam-concurrent": check_jam_concurrent,
           "jam-boot-silence": check_jam_boot_silence, "jam-port": check_jam_port,
           "jam-calibration": check_jam_calibration, "jam-stop": check_jam_stop,
           "jam-cap": check_jam_cap, "jam-stop-cmd": check_jam_stop_cmd}


# ---------------------------------------------------------------- the run

def resolve_port(ctx):
    for _ in range(50):
        try:
            return ctx["serial_dev"].resolve(ctx["device"], "the X2")["port"]
        except ctx["serial_dev"].Refused as e:
            last = e
            time.sleep(0.2)
    raise Fail(str(last))


def preflight(args, settings):
    r = subprocess.run([sys.executable, "-B", "-m", "vtrux_bench.bench_lock",
                        "--log", settings["bench_lock_log"], "status"],
                       capture_output=True, text=True,
                       env=dict(os.environ, PYTHONPATH=args.shared_bench))
    # bench_lock status prints "holder: held by <who>, pid ..." (shared/bench
    # 2026.11.12.2, read on madhouse-debian 2026-11-12)
    if not re.search(r"^holder: held by tester \(charge-interposer\),", r.stdout, re.M):
        raise Fail("the bench lock is not held by tester (charge-interposer):\n" + r.stdout)
    if iface_up("canmain"):
        raise Fail("canmain is up: it is kept down during ESP runs")
    return r.stdout


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--settings", required=True)
    ap.add_argument("--shared-bench", required=True)
    ap.add_argument("--elf", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--checks", default=",".join(CHECKS))
    ap.add_argument("--can-fail", choices=CHECKS)
    ap.add_argument("--fps", type=float, default=1320.0)
    ap.add_argument("--seconds", type=float, default=60.0)
    ap.add_argument("--can-tools", help="no longer read: CAN1's witness is the kernel's "
                    "kvaser_usb since 2026-11-13 (witness.open_kvaser); kept so earlier "
                    "command lines still parse")
    args = ap.parse_args()

    out = os.path.abspath(args.out)
    if subprocess.run(["git", "-C", os.path.dirname(out), "rev-parse"],
                      capture_output=True).returncode == 0:
        print("REFUSED: %s is inside a git worktree" % out)
        return 2
    os.makedirs(out, exist_ok=True)
    with open(args.settings) as f:
        settings = json.load(f)
    sys.path.insert(0, os.path.abspath(args.shared_bench))
    from vtrux_bench import serial_dev
    with open(args.elf, "rb") as f:
        elf_sha = hashlib.sha256(f.read()).hexdigest()
    checks = [args.can_fail] if args.can_fail else [c for c in args.checks.split(",") if c]
    for c in checks:
        if c not in RUNNERS:
            print("REFUSED: no check %r" % c)
            return 2
    ctx = {"serial_dev": serial_dev, "device": settings["esptool"]["device"],
           "elf_sha256": elf_sha, "fps": args.fps, "seconds": args.seconds,
           "can_tools": args.can_tools}
    result = {"started": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
              "elf": os.path.abspath(args.elf), "elf_sha256": elf_sha,
              "can_fail": args.can_fail, "fps": args.fps, "seconds": args.seconds,
              "can_tools": args.can_tools,
              "checks": {}}
    rc = 0
    try:
        result["lock"] = preflight(args, settings)
        ctx["port"] = resolve_port(ctx)
        result["port"] = ctx["port"]
        for c in checks:
            say("check %s%s" % (c, " (can-fail)" if args.can_fail else ""))
            try:
                problems, detail = RUNNERS[c](ctx, bool(args.can_fail))
                verdict = "FAIL" if problems else "PASS"
            except Fail as e:
                problems, detail, verdict = [str(e)], {}, "NOT-RUN"
            result["checks"][c] = {"verdict": verdict, "problems": problems,
                                   "detail": detail}
            say("  %s %s" % (verdict, "; ".join(problems)))
            if args.can_fail:
                rc = 0 if verdict == "FAIL" else 1
            elif verdict != "PASS":
                rc = 1
    except Fail as e:
        result["refused"] = str(e)
        say("REFUSED: %s" % e)
        rc = 2
    finally:
        for step in (lambda: down(X2), detach, lambda: down(PEER)):
            try:
                step()
            except Exception as e:
                say("cleanup: %s" % e)
        result["ended"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        result["exit"] = rc
        result["log"] = LOG
        with open(os.path.join(out, "result.json"), "w") as f:
            json.dump(result, f, indent=2)
    say("result: %s exit %d" % (os.path.join(out, "result.json"), rc))
    return rc


if __name__ == "__main__":
    sys.exit(main())
