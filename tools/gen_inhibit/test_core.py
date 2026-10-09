"""Unit tests for the inhibitor core. No CAN, no captures, ~1 s."""
import sys
sys.path.insert(0, "projects/vtrux/tools/gen_inhibit")

from sync import SlotSync
from inhibit import Inhibitor, TORQUE_ZERO
import models as M

PASS = []
FAIL = []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  %-4s %s%s" % ("ok" if cond else "FAIL", name,
                           ("  -- " + detail) if detail and not cond else ""))


def drive(inh, n, period=0.010, jitter=0.0, t0=0.0, payload=None, seed=3,
          drop=()):
    """Run n VCM slots through an inhibitor, returning what it transmitted."""
    import random
    rnd = random.Random(seed)
    out = []
    # One global timeline: slot index is derived from t0 so that consecutive
    # drive() calls continue the same rolling counter. Restarting the counter
    # mid-run is a discontinuity the core is right to reject, and doing it by
    # accident in the harness looks exactly like a bug in the core.
    k0 = int(round(t0 / period))
    for i in range(n):
        k = k0 + i
        tk = k * period + (rnd.uniform(-jitter, jitter) if jitter else 0.0)
        if i not in drop:
            p = payload(k) if payload else (0x08, 0x00, 0x80, 0xFF, 0x7F, k % 16)
            inh.on_genuine(tk, p)
        u = tk
        while u < tk + period:
            for _id, pl in inh.poll(u):
                out.append((u, pl))
            u += 0.00005
    return out


print("== SlotSync ==")
s = SlotSync()
for k in range(300):
    s.observe(k * 0.010, k % 16)
check("locks on a clean 100 Hz stream", s.locked)
check("period estimate within 1 us", abs(s.period - 0.010) < 1e-6,
      "period=%.9f" % s.period)
check("predicts the next counter", s.predict_next()[1] == 300 % 16)

# a dropped frame must not slip the phase -- this is what the counter buys us
s = SlotSync()
for k in range(200):
    if k in (50, 51, 52, 120):
        continue
    s.observe(k * 0.010, k % 16)
check("survives dropped frames without slipping", s.locked and s.cycle_slips == 0,
      "slips=%d locked=%s" % (s.cycle_slips, s.locked))
check("phase still accurate after drops",
      abs(s.predict_next()[0] - 0.010 * 200) < 2e-4,
      "pred=%.6f want=%.6f" % (s.predict_next()[0], 2.0))

# a gap longer than the counter wrap must be resolved by elapsed time
s = SlotSync()
for k in range(100):
    s.observe(k * 0.010, k % 16)
s.observe(100 * 0.010 + 0.25, (100 + 25) % 16)   # 25 slots later, counter wrapped
check("resolves a >16-slot gap using elapsed time",
      abs(s.predict_next()[0] - (1.25 + 0.010)) < 1e-3,
      "pred=%.6f" % s.predict_next()[0])

s = SlotSync()
for k in range(300):
    s.observe(k * 0.010, k % 16)
check("does not steer on a repeated counter",
      s.observe(3.0 + 0.010, s.last_counter) is None and s.counter_repeats == 1)

s = SlotSync()
for k in range(300):
    s.observe(k * 0.011, k % 16)   # a 90.9 Hz talker
check("period clamp holds at the tolerance limit",
      abs(s.period - 0.0102) < 1e-6, "period=%.6f" % s.period)

s = SlotSync()
for k in range(300):
    s.observe(k * 0.010, k % 16)
check("goes stale when the talker stops", s.stale(3.0 + 0.2))

print("\n== Inhibitor: refuses to transmit unless it can do so safely ==")
inh = Inhibitor(SlotSync(), strategy="lead")
out = drive(inh, 20)
check("lead is silent before lock", len(out) == 0 and inh.suppressed_unlocked == 20)

inh = Inhibitor(SlotSync(), strategy="trail")
out = drive(inh, 8)
check("trail is silent before it is even tracking", len(out) == 0)

# trail must survive a phase estimate too poor to lock -- that is its whole
# reason for existing, and gating it on lock silently disabled it in the field
inh = Inhibitor(SlotSync(), strategy="trail")
out = drive(inh, 400, jitter=0.003)
check("trail keeps transmitting on a bus it cannot phase-lock",
      len(out) > 300 and not inh.sync.locked,
      "sent %d locked=%s" % (len(out), inh.sync.locked))
inh2 = Inhibitor(SlotSync(), strategy="lead")
drive(inh2, 400, jitter=0.003)
check("lead still refuses on that same bus", inh2.sent == 0,
      "sent %d" % inh2.sent)

inh = Inhibitor(SlotSync(), strategy="lead")
out = drive(inh, 400, jitter=0.004)
check("lead silent on a bus too jittery to predict", len(out) == 0,
      "sent %d" % len(out))

inh = Inhibitor(SlotSync(), strategy="lead", lead=0.0003)
out = drive(inh, 400)
check("refuses a lead that cannot clear the frame time", len(out) == 0,
      "sent %d" % len(out))

inh = Inhibitor(SlotSync(), strategy="trail", trail_delay=0.0098)
out = drive(inh, 400)
check("refuses a trail that would run into the next slot", len(out) == 0,
      "sent %d" % len(out))

inh = Inhibitor(SlotSync())
drive(inh, 200)
inh.disarm()
before = inh.sent
drive(inh, 200, t0=2.0)
check("disarm stops transmission", inh.sent == before)

print("\n== Inhibitor: what it puts on the wire ==")
inh = Inhibitor(SlotSync(), strategy="lead", payload_mode="mirror")
out = drive(inh, 400, payload=lambda k: (0x0B, 0x48, 0x81, 0x1F, 0x83, k % 16))
check("transmits once locked", len(out) > 300, "sent %d" % len(out))
pl = out[-1][1]
check("torque field zeroed", ((pl[2] << 8) | pl[1]) == TORQUE_ZERO,
      "raw=0x%04X" % ((pl[2] << 8) | pl[1]))
check("mirror keeps the VCM's state byte", pl[0] == 0x0B)
check("mirror keeps the VCM's RPM reference", (pl[3], pl[4]) == (0x1F, 0x83))
check("DLC is 6", len(pl) == 6)

inh = Inhibitor(SlotSync(), strategy="lead", payload_mode="idle")
out = drive(inh, 400, payload=lambda k: (0x0B, 0x48, 0x81, 0x1F, 0x83, k % 16))
check("idle mode sends the byte-exact engine-off command",
      out[-1][1][:5] == (0x08, 0x00, 0x80, 0xFF, 0x7F), "%s" % (out[-1][1],))

print("\n== Counter pre-emption: does the lead frame really steal the slot? ==")
inh = Inhibitor(SlotSync(), strategy="lead")
delivered = []
for k in range(400):
    tk = k * 0.010
    p = (0x0B, 0x48, 0x81, 0x1F, 0x83, k % 16)
    delivered.append((tk, p, False))           # the VCM's own frame, in its slot
    inh.on_genuine(tk, p)
    u = tk
    while u < tk + 0.010:                      # our lead frame for slot k+1,
        for _id, pl in inh.poll(u):            # placed ahead of it
            delivered.append((u, pl, True))
        u += 0.00005
delivered.sort(key=lambda x: x[0])
cv = M.CounterValidating()
for ts, pl, m in delivered:
    cv.deliver(ts, pl, m)
check("counter-validating receiver rejects nearly every genuine frame",
      cv.rejected_genuine > 350, "rejected %d of %d" %
      (cv.rejected_genuine, cv.rejected_genuine + cv.accepted_genuine))
check("and accepts ours instead", cv.accepted_mine > 350,
      "accepted %d" % cv.accepted_mine)

print("\n== transmit rate ceiling ==")
inh = Inhibitor(SlotSync(), strategy="trail")
drive(inh, 400)
check("normal operation stays under the ceiling",
      not inh.rate_tripped and inh.sent > 300,
      "sent %d tripped=%s" % (inh.sent, inh.rate_tripped))

# Feed the core its own output alongside the VCM's -- the runaway that
# actually happened on a transport that echoes. Each frame we send comes back,
# looks like a fresh VCM frame with a fresh counter, and schedules another.
inh = Inhibitor(SlotSync(), strategy="trail")
t = 0.0
for k in range(300):
    inh.on_genuine(t, (0x08, 0x00, 0x80, 0xFF, 0x7F, k % 16))
    u = t
    while u < t + 0.010:
        for _cid, pl in inh.poll(u):
            inh.on_genuine(u, pl)      # the echo, fed straight back in
        u += 0.0001
    t += 0.010
check("self-feeding trips the ceiling", inh.rate_tripped,
      "sent %d tripped=%s" % (inh.sent, inh.rate_tripped))
check("and latches the transmitter off", inh.state == "off")
check("ceiling held near the configured 300/s", inh.sent < 500,
      "sent %d" % inh.sent)
check("the echo shows up as repeated counters", inh.sync.counter_repeats > 0,
      "repeats=%d" % inh.sync.counter_repeats)

print("\n== engage interlock ==")
loaded = lambda k: (0x0B, 0x30, 0x7B, 0x63, 0x84, k % 16)   # -1232 cts, engine at RPM
idle = lambda k: (0x08, 0x00, 0x80, 0xFF, 0x7F, k % 16)     # 0 cts, engine off

inh = Inhibitor(SlotSync(), strategy="trail", arm_mode="on_demand")
check("refuses to arm before it has seen a frame", inh.arm()[0] is False)

inh = Inhibitor(SlotSync(), strategy="trail", arm_mode="on_demand")
drive(inh, 200, payload=loaded)
check("reads engine-running out of the RPM reference", inh.engine_running() is True)
ok, why = inh.arm()
check("refuses to engage a running generator", ok is False, why)
check("and stays silent", inh.sent == 0 and inh.state == "off")

inh = Inhibitor(SlotSync(), strategy="trail", arm_mode="on_demand")
drive(inh, 200, payload=idle)
check("reads engine-off out of the RPM reference", inh.engine_running() is False)
ok, why = inh.arm()
check("arms with the engine off", ok is True, why)
out = drive(inh, 50, t0=2.0, payload=idle)
check("transmits once armed", len(out) > 30, "sent %d" % len(out))
inh.disarm()
n = inh.sent
drive(inh, 50, t0=2.6, payload=idle)
check("disarm goes silent immediately", inh.sent == n and inh.state == "off")

inh = Inhibitor(SlotSync(), strategy="trail", arm_mode="on_demand")
drive(inh, 200, payload=loaded)
check("the override exists for the bench", inh.arm(allow_running=True)[0] is True)

# the same machinery holding a non-zero target
inh = Inhibitor(SlotSync(), strategy="trail", arm_mode="on_demand",
                target_torque=342)
drive(inh, 200, payload=idle)
inh.arm()
out = drive(inh, 50, t0=2.0, payload=idle)
pl = out[-1][1]
check("holds a positive target just as readily",
      ((pl[2] << 8) | pl[1]) - 32768 == 342,
      "%d" % (((pl[2] << 8) | pl[1]) - 32768))

print("\n== scoring sanity ==")
m = M.PerFrame()
m.deliver(0.0, (0x0B, 0x48, 0x81, 0, 0, 0), False)     # +328
m.deliver(1.0, (0x08, 0x00, 0x80, 0, 0, 1), False)     # 0
sc = M.score(m, 0.0, 2.0)
check("zero_fraction counts the zero half", abs(sc["zero_fraction"] - 0.5) < 1e-6,
      "%.4f" % sc["zero_fraction"])
check("mean_abs is the time average", abs(sc["mean_abs"] - 164.0) < 1e-6,
      "%.4f" % sc["mean_abs"])
check("longest_nonzero is the leak length",
      abs(sc["longest_nonzero_s"] - 1.0) < 1e-6, "%.4f" % sc["longest_nonzero_s"])

print("\n%d passed, %d failed" % (len(PASS), len(FAIL)))
sys.exit(1 if FAIL else 0)
