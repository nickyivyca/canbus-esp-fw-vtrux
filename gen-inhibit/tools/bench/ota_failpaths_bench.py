#!/usr/bin/env python3
"""Gen-inhibit spec 9.2 items 4-6 (shared/wican W12-W14) on the bench: the OTA handler
against a real client. Gen-inhibit tester, 2026-11-23, stage (b) batch 1.

The handler's logic is judged on the host (test/wican_ota_host). This is the hardware leg
(spec 12.3: "the OTA handler against a real client" stays hardware-only). A raw socket
client sends the stock interface, a multipart POST of field `ota_file` to /upload/ota.bin,
and reads the kernel's TCP_INFO for its own connection every 20 ms. That gives it the
device's ACKs as they arrive (tcpi_bytes_acked) and the window the device offers
(tcpi_snd_wnd), with no capture and no root.

Kinds (--kind):
  stall    the headers and the first --cut bytes of the image's body, then nothing, with
           the connection held open. W13: the device drops it in about 20 s, timed from
           the moment the device had ACKed every byte sent (the last chunk received) to
           its reply, FIN or RST.
  restart  --cut bytes, then 3 gaps of --gap s (12: two of the server's 5 s timeouts each)
           with a 1 KiB chunk after each, then a stall. W13's count restarts on each
           chunk, so nothing ends before the final stall, which is dropped about 20 s
           after its last chunk.
  fin      --cut bytes, then a half-close (FIN). W12's bench consequence: the device
           answers (or closes) and its page answers afterwards.
  rst      --cut bytes, ACKed, then a reset (SO_LINGER 0).
  full     the whole image, which the device must take: it replies, restarts into it, and
           the host rejoins. The end-to-end OTA by a plain client (9.2's interface).
W14 is judged on every kind's samples: no interval of --window-limit s (2) or more in which
the device ACKed nothing while bytes were outstanding. Before W14, every good upload paused
4.9-6.5 s with the window closed while the whole slot was erased (VERIFICATION.md,
2026-10-17 captures).

Every kind reads /gen_inhibit before (refused unless mode 0 and running --image) and after.
After a failed kind the device must answer within --answer-s s of the end, on the same image,
uptime continuous, mode 0. After `full`, the image is the same (it is the running image) and
uptime restarted. Only mode, uptime_ms, elf_sha256 and reset_reason of the page are kept.

    python3.13 -B ota_failpaths_bench.py --kind stall|restart|fin|rst|full --image <running .bin>
        --out <record.json> [--cut 65536] [--gap 12] [--band 18,23] [--window-limit 2]
Run with GI_OTA_SETTINGS set (the suite's settings.ota block) for `full`'s rejoin. Exit 0
every row passes, 1 a row fails, 2 refused (nothing sent). Tested by
test_ota_failpaths_bench.py on loopback.
"""
import argparse
import json
import os
import socket
import struct
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import image_elf_sha as IMG   # noqa: E402

HOST, PORT, PATH = "192.168.80.1", 80, "/upload/ota.bin"
BOUNDARY = "----vtruxfailpaths7d1c0e"
PAGE_KEEP = ("mode", "uptime_ms", "elf_sha256", "reset_reason", "worker")
SAMPLE_S = 0.02


# ---------------------------------------------------------------- the request --

def multipart(data, name="ota.bin"):
    head = ("--%s\r\nContent-Disposition: form-data; name=\"ota_file\"; filename=\"%s\"\r\n"
            "Content-Type: application/octet-stream\r\n\r\n" % (BOUNDARY, name)).encode()
    tail = ("\r\n--%s--\r\n" % BOUNDARY).encode()
    return head + data + tail, len(head)


def request_head(content_len, host):
    return ("POST %s HTTP/1.1\r\nHost: %s\r\nContent-Type: multipart/form-data; boundary=%s\r\n"
            "Content-Length: %d\r\nConnection: close\r\n\r\n" % (PATH, host, BOUNDARY, content_len)).encode()


# ------------------------------------------------------------------ TCP_INFO --

TCP_INFO = getattr(socket, "TCP_INFO", 11)
_OFF = {"last_ack_recv_ms": (56, "I"), "unacked": (24, "I"), "rtt_us": (68, "I"),
        "bytes_acked": (120, "Q"), "notsent": (144, "I"), "snd_wnd": (228, "I")}


def tcp_info(sock):
    """{bytes_acked, snd_wnd, unacked, notsent, last_ack_recv_ms, rtt_us} (Linux), or {} when
    the kernel's struct is too short for a field (that field left out)."""
    raw = sock.getsockopt(socket.IPPROTO_TCP, TCP_INFO, 256)
    out = {}
    for k, (off, f) in _OFF.items():
        n = struct.calcsize(f)
        if len(raw) >= off + n:
            out[k] = struct.unpack_from("<" + f, raw, off)[0]
    return out


class Sampler(threading.Thread):
    def __init__(self, sock, t0):
        super().__init__(daemon=True)
        self.sock, self.t0, self.rows, self.stop = sock, t0, [], threading.Event()
        self.sent = 0                          # bytes the client has handed to the kernel

    def run(self):
        while not self.stop.is_set():
            try:
                ti = tcp_info(self.sock)
            except OSError:
                return
            ti["t"] = round(time.time() - self.t0, 4)
            ti["sent"] = self.sent
            self.rows.append(ti)
            time.sleep(SAMPLE_S)


# ------------------------------------------------------------------ the kinds --

def wait_acked(sock, target, timeout):
    """Seconds (epoch) when bytes_acked first reached target, or None."""
    end = time.time() + timeout
    while time.time() < end:
        if tcp_info(sock).get("bytes_acked", 0) >= target:
            return time.time()
        time.sleep(0.005)
    return None


def read_end(sock, timeout):
    """(epoch, kind, first bytes): the device's reply or close, kind 'reply', 'fin', 'rst' or
    'none' (nothing within timeout)."""
    sock.settimeout(timeout)
    try:
        b = sock.recv(512)
    except socket.timeout:
        return time.time(), "none", b""
    except ConnectionResetError:
        return time.time(), "rst", b""
    except OSError as e:
        return time.time(), "error:%s" % type(e).__name__, b""
    return time.time(), ("reply" if b else "fin"), b


def run_kind(kind, data, host=HOST, port=PORT, cut=65536, gap=12.0, gaps=3, stall_timeout=150.0,
             sndbuf=16384):
    """The client side of one kind; a record of times (epoch), events and samples."""
    body, pre = multipart(data)
    head = request_head(len(body), host)
    rec = {"kind": kind, "host": host, "image_len": len(data), "cut": cut if kind != "full" else len(data),
           "events": []}
    ev = lambda name, **kw: rec["events"].append(dict(kw, name=name, epoch=time.time()))
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, sndbuf)
    s.settimeout(10)
    t0 = time.time()
    rec["epoch_start"] = t0
    s.connect((host, port))
    ev("connected")
    smp = Sampler(s, t0)
    smp.start()

    def send(b):
        # In segments, each counted before it is handed over, so bytes blocked in a send
        # count as outstanding while the device ACKs nothing.
        for i in range(0, len(b), 1436):
            seg = b[i:i + 1436]
            smp.sent += len(seg)
            s.sendall(seg)

    try:
        if kind == "full":
            send(head)
            ev("headers_sent")
            send(body)
            ev("all_sent")
            rec["epoch_last_acked"] = wait_acked(s, len(head) + len(body), 120)
            rec["epoch_end"], rec["end"], first = read_end(s, 120)
        else:
            send(head + body[:pre + cut])
            ev("cut_sent")
            if kind == "fin":
                rec["epoch_last_acked"] = wait_acked(s, smp.sent, 30)
                s.shutdown(socket.SHUT_WR)
                ev("fin_sent")
                rec["epoch_end"], rec["end"], first = read_end(s, 60)
            elif kind == "rst":
                rec["epoch_last_acked"] = wait_acked(s, smp.sent, 30)
                s.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
                smp.stop.set()
                smp.join()
                s.close()
                ev("rst_sent")
                rec["epoch_end"], rec["end"], first = time.time(), "client_rst", b""
            else:
                off = pre + cut
                if kind == "restart":
                    rec["gaps"] = []
                    for i in range(gaps):
                        acked = wait_acked(s, smp.sent, 30)
                        s.settimeout(gap)
                        try:
                            early = s.recv(512)
                            rec["early_end"] = {"gap": i, "kind": "reply" if early else "fin",
                                                "first": early[:200].decode("latin-1"), "epoch": time.time()}
                            break
                        except socket.timeout:
                            pass
                        except ConnectionResetError:
                            rec["early_end"] = {"gap": i, "kind": "rst", "first": "", "epoch": time.time()}
                            break
                        send(body[off:off + 1024])
                        off += 1024
                        rec["gaps"].append({"acked_before": acked, "chunk_sent": time.time()})
                if "early_end" in rec:
                    rec["epoch_last_acked"] = None
                    rec["epoch_end"], rec["end"], first = rec["early_end"]["epoch"], rec["early_end"]["kind"], b""
                else:
                    rec["epoch_last_acked"] = wait_acked(s, smp.sent, 30)
                    ev("stall_from")
                    rec["epoch_end"], rec["end"], first = read_end(s, stall_timeout)
        rec["first_bytes"] = first[:200].decode("latin-1")
        rec["status"] = first.split(b"\r\n", 1)[0].decode("latin-1") if first.startswith(b"HTTP/") else None
    finally:
        smp.stop.set()
        if smp.is_alive():
            smp.join()
        try:
            s.close()
        except OSError:
            pass
    rec["samples"] = smp.rows
    rec["t0"] = t0
    return rec


# -------------------------------------------------------------------- judging --

def longest_ack_stall(samples, t_from=0.0, t_to=None):
    """(seconds, t start) of the longest run of samples in which bytes_acked did not
    advance while the client had bytes outstanding (sent > acked)."""
    best, best_t, run_t, last = 0.0, None, None, None
    for r in samples:
        if "bytes_acked" not in r or r["t"] < t_from or (t_to is not None and r["t"] > t_to):
            continue
        outstanding = r["sent"] > r["bytes_acked"]
        if last is not None and r["bytes_acked"] == last and outstanding:
            if run_t is None:
                run_t = prev_t
            if r["t"] - run_t > best:
                best, best_t = r["t"] - run_t, run_t
        else:
            run_t = None
        last, prev_t = r["bytes_acked"], r["t"]
    return round(best, 3), best_t


def judge(rec, band=(18.0, 23.0), window_limit=2.0):
    """[(row, ok, detail)] for one kind's record."""
    rows = []
    t0 = rec["t0"]
    t_end = rec.get("epoch_end", t0) - t0
    if not any("snd_wnd" in r for r in rec["samples"]):
        rows.append(("harness", False, "no TCP_INFO samples with snd_wnd: W14 cannot be read"))
    else:
        # the upload phase: to the end, or for stall kinds to the stall's start (an idle client
        # has nothing outstanding, so the stall itself cannot count)
        stall, at = longest_ack_stall(rec["samples"], 0.0, t_end)
        wnds = [r["snd_wnd"] for r in rec["samples"] if "snd_wnd" in r and r["t"] <= t_end]
        rows.append(("W14", stall < window_limit,
                     "longest ACK stall with bytes outstanding %.3f s (at %s s), limit %.1f s; smallest window "
                     "offered %s bytes" % (stall, at, window_limit, min(wnds) if wnds else None)))
    k = rec["kind"]
    if k in ("stall", "restart"):
        if k == "restart":
            early = rec.get("early_end")
            rows.append(("W13", early is None,
                         "the upload ended during gap %s (%s)" % (early["gap"], early["kind"]) if early else
                         "no end in %d gaps of 2 timeouts each, a chunk after each" % len(rec.get("gaps", []))))
        la = rec.get("epoch_last_acked")
        if rec.get("early_end"):
            pass                                    # the gap row above already failed it
        elif la is None:
            rows.append(("W13", False, "the device never ACKed every byte sent before the stall"))
        elif rec.get("end") not in ("reply", "fin", "rst"):
            rows.append(("W13", False, "not dropped within %.0f s of the last chunk (%s)" % (t_end - (la - t0), rec.get("end"))))
        else:
            d = rec["epoch_end"] - la
            rows.append(("W13", band[0] <= d <= band[1],
                         "dropped %.2f s after the last chunk was ACKed (band %.1f-%.1f s), by %s, %s"
                         % (d, band[0], band[1], rec["end"], rec.get("status"))))
    elif k == "full":
        st = rec.get("status") or ""
        rows.append(("OTA", st.split(" ")[1:2] in (["200"], ["303"]),
                     "the whole image: %s" % (st or rec.get("end"))))
    else:
        rows.append(("W12", None, "recorded: the device's end %s, %s" % (rec.get("end"), rec.get("status"))))
    return rows


def judge_after(kind, before, after, answer_s):
    """[(row, ok, detail)]: the device after the kind."""
    rows = []
    if not after.get("ok"):
        return [("after", False, "no page within %.0f s: %s" % (answer_s, after.get("error")))]
    a, b = after["page"], before
    if kind == "full":
        restarted = isinstance(a.get("uptime_ms"), int) and a["uptime_ms"] < 120000
        rows.append(("after", a.get("elf_sha256") == b.get("elf_sha256") and restarted,
                     "after the OTA: elf %s..., uptime %s ms (restarted), answered %.1f s after the end"
                     % (str(a.get("elf_sha256"))[:8], a.get("uptime_ms"), after["delay"])))
    else:
        cont = isinstance(a.get("uptime_ms"), int) and isinstance(b.get("uptime_ms"), int) and a["uptime_ms"] > b["uptime_ms"]
        ok = (a.get("elf_sha256") == b.get("elf_sha256") and cont and a.get("mode") == 0
              and after["delay"] <= answer_s)
        rows.append(("after", ok, "same image %s, uptime %s -> %s ms, mode %s, worker %s, answered %.1f s after the end"
                     % (a.get("elf_sha256") == b.get("elf_sha256"), b.get("uptime_ms"), a.get("uptime_ms"),
                        a.get("mode"), a.get("worker"), after["delay"])))
    return rows


# ---------------------------------------------------------------- the device --

def page(host, timeout=8):
    import urllib.request
    with urllib.request.urlopen("http://%s/gen_inhibit" % host, timeout=timeout) as r:
        txt = r.read().decode("utf-8", "replace")
    pg, _ = json.JSONDecoder().raw_decode(txt.lstrip())
    return {k: pg.get(k) for k in PAGE_KEEP}


def page_until(host, since, limit, rejoin=None):
    """{ok, page, delay (s after since), error}: polls until the page answers or limit s."""
    err = None
    while time.time() - since < limit:
        try:
            pg = page(host, timeout=5)
            return {"ok": True, "page": pg, "delay": round(time.time() - since, 2)}
        except Exception as e:      # noqa: BLE001
            err = "%s: %s" % (type(e).__name__, e)
            if rejoin:
                rejoin()
            time.sleep(1)
    return {"ok": False, "error": err, "delay": None}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--kind", required=True, choices=("stall", "restart", "fin", "rst", "full"))
    ap.add_argument("--image", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--host", default=HOST)
    ap.add_argument("--port", type=int, default=PORT)
    ap.add_argument("--cut", type=int, default=65536)
    ap.add_argument("--gap", type=float, default=12.0)
    ap.add_argument("--band", default="18,23")
    ap.add_argument("--window-limit", type=float, default=2.0)
    ap.add_argument("--answer-s", type=float, default=10.0)
    ap.add_argument("--stall-timeout", type=float, default=150.0)
    ap.add_argument("--no-device-page", action="store_true", help="tests only")
    a = ap.parse_args(argv)
    band = tuple(float(x) for x in a.band.split(","))
    try:
        data = open(a.image, "rb").read()
        desc = IMG.read_desc(data)
    except (OSError, ValueError) as e:
        print("REFUSED: the image: %s" % e)
        return 2
    if a.kind != "full" and not 0 < a.cut < len(data):
        print("REFUSED: --cut %d is not strictly inside the %d-byte image" % (a.cut, len(data)))
        return 2
    before = None
    if not a.no_device_page:
        try:
            before = page(a.host)
        except Exception as e:      # noqa: BLE001
            print("REFUSED: /gen_inhibit before: %s" % e)
            return 2
        if before.get("elf_sha256") != desc["elf_sha256"]:
            print("REFUSED: the device runs %s..., the image is %s..."
                  % (str(before.get("elf_sha256"))[:8], desc["elf_sha256"][:8]))
            return 2
        if before.get("mode") != 0:
            print("REFUSED: the device is in mode %s, not 0" % before.get("mode"))
            return 2
    rec = run_kind(a.kind, data, a.host, a.port, a.cut, a.gap, stall_timeout=a.stall_timeout)
    rec.update(image=os.path.basename(a.image), image_elf_sha256=desc["elf_sha256"], page_before=before,
               band=band, window_limit=a.window_limit)
    rows = judge(rec, band, a.window_limit)
    if not a.no_device_page:
        rejoin = None
        if a.kind == "full":
            import gi_wifi as GW
            cfg = GW.from_env()
            time.sleep(3)
            rejoin = lambda: GW.rejoin(cfg)
        after = page_until(a.host, rec["epoch_end"], 90 if a.kind == "full" else max(a.answer_s, 30), rejoin)
        rec["page_after"] = after
        rows += judge_after(a.kind, before, after, a.answer_s)
    rec["rows"] = [list(r) for r in rows]
    with open(a.out, "w", encoding="utf-8", newline="\n") as f:
        json.dump(rec, f, indent=1)
    for row, ok, detail in rows:
        print("%-5s %-7s %s" % ("REC" if ok is None else "PASS" if ok else "FAIL", row, detail))
    return 1 if any(ok is False for _, ok, _ in rows) else 0


if __name__ == "__main__":
    sys.exit(main())
