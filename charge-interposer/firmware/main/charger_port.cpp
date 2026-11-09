#include "charger_port.h"

#include <string.h>

namespace interposer {

namespace {

struct Charger {
  Mcp2515Port* port;
  uint32_t bitrate;
  uint32_t now_ms;
  can_port_mode_t mode;
  // Carried across a teardown, which clears the driver's rings and with
  // them its ring's drop count (Mcp2515Port::end()).
  uint32_t rx_lost_carried;
  uint32_t rx_taken;      // frames handed to a receiver
  uint32_t tx_queued;     // sends the driver queued
  uint32_t tx_down;       // sends with the port down (in the driver's txDropped too)
  uint32_t tx_rejected;   // remote frames and no frame, refused before the driver
  uint32_t rx_flushed;
  uint32_t bring_ups;
};

Charger s_chg;

esp_err_t opUp(void* ctx) {
  Charger* c = static_cast<Charger*>(ctx);
  if (c->port->isUp()) return ESP_OK;
  if (!c->port->begin(c->bitrate)) return ESP_FAIL;
  c->bring_ups++;
  return ESP_OK;
}

esp_err_t opDown(void* ctx) {
  Charger* c = static_cast<Charger*>(ctx);
  if (!c->port->isUp()) return ESP_OK;
  const uint32_t before = c->port->rxDropped();
  c->port->end();
  c->rx_lost_carried += before - c->port->rxDropped();
  return ESP_OK;
}

bool opIsUp(void* ctx) { return static_cast<Charger*>(ctx)->port->isUp(); }

// NORMAL and LISTEN-ONLY only: the chip's internal loopback is not
// can_port's no-ACK self-test (no frame reaches the wire), so that is
// refused, and the diag variant sets the loopback on the driver before
// bringing it up. Up, a mode change is a teardown and a bring-up, since the
// driver sets the chip's mode in begin().
esp_err_t opSetMode(void* ctx, can_port_mode_t mode) {
  Charger* c = static_cast<Charger*>(ctx);
  if (mode != CAN_PORT_MODE_NORMAL && mode != CAN_PORT_MODE_LISTEN_ONLY)
    return ESP_ERR_NOT_SUPPORTED;
  if (mode == c->mode) return ESP_OK;
  const bool was_up = c->port->isUp();
  if (was_up) (void)opDown(ctx);
  c->port->setListenOnly(mode == CAN_PORT_MODE_LISTEN_ONLY);
  c->mode = mode;
  return was_up ? opUp(ctx) : ESP_OK;
}

// The stock settings are port 0's (can_port C3), never this port's.
esp_err_t opConfigure(void*, const can_port_settings_t*) {
  return ESP_ERR_NOT_SUPPORTED;
}

// Only what is waiting: the driver's receive never waits, and returning at
// once is within any bound the caller gives (can_port C2).
esp_err_t opReceive(void* ctx, twai_message_t* out, TickType_t) {
  Charger* c = static_cast<Charger*>(ctx);
  if (out == nullptr) return ESP_ERR_INVALID_ARG;
  if (!c->port->isUp()) return ESP_ERR_INVALID_STATE;
  RxFrame f;
  if (!c->port->receive(f)) return ESP_ERR_TIMEOUT;
  memset(out, 0, sizeof(*out));
  out->identifier = f.id;
  out->extd = f.ext ? 1 : 0;
  out->data_length_code = f.len;
  for (uint8_t i = 0; i < f.len && i < 8; i++) out->data[i] = f.data[i];
  c->rx_taken++;
  return ESP_OK;
}

// Enqueue only. Every send but a remote frame reaches the driver, the port
// down included, so the driver's own txDropped() -- a 2.2 tally -- counts
// what it counted before the port was behind can_port.
esp_err_t opSend(void* ctx, const twai_message_t* m, TickType_t) {
  Charger* c = static_cast<Charger*>(ctx);
  if (m == nullptr || m->rtr) {
    c->tx_rejected++;
    return ESP_ERR_INVALID_ARG;
  }
  const bool up = c->port->isUp();
  const uint8_t len = m->data_length_code > 8 ? 8 : m->data_length_code;
  if (c->port->send(m->identifier, m->extd != 0, m->data, len)) {
    c->tx_queued++;
    return ESP_OK;
  }
  if (!up) {
    c->tx_down++;
    return ESP_ERR_INVALID_STATE;
  }
  return ESP_ERR_TIMEOUT;
}

void opService(void* ctx) {
  Charger* c = static_cast<Charger*>(ctx);
  c->port->service(c->now_ms);
}

esp_err_t opStatus(void* ctx, can_port_status_t* out) {
  Charger* c = static_cast<Charger*>(ctx);
  if (out == nullptr) return ESP_ERR_INVALID_ARG;
  const Mcp2515Port& p = *c->port;
  memset(out, 0, sizeof(*out));
  out->state = !p.isUp() ? CAN_PORT_STATE_DOWN
               : p.busOff() ? CAN_PORT_STATE_BUS_OFF
                            : CAN_PORT_STATE_RUNNING;
  out->tx_pending = p.txRingUsed();
  out->rx_waiting = p.rxRingUsed();
  out->rx_queue_len = 63;
  out->bus_off = p.busOff();
  out->rx_frames = c->rx_taken;
  out->tx_frames = c->tx_queued;
  out->rx_lost_overrun = p.rxOverruns();
  out->rx_lost = p.rxDropped() + c->rx_lost_carried;
  out->rx_lost_queue_full = out->rx_lost - out->rx_lost_overrun;
  out->rx_lost_is_bound = true;
  out->tx_dropped = p.txDropped() - c->tx_down;
  out->tx_refused = c->tx_down + c->tx_rejected;
  out->tx_failed = p.txFailed();
  out->bus_errors = p.busErrors();
  out->bus_off_entries = p.busOffEvents();
  out->rx_flushed = c->rx_flushed;
  out->bring_ups = c->bring_ups;
  return ESP_OK;
}

void opFlushRx(void* ctx) {
  Charger* c = static_cast<Charger*>(ctx);
  RxFrame f;
  while (c->port->receive(f)) c->rx_flushed++;
}

const can_port_ops_t CHARGER_OPS = {
    "MCP2515", opUp,      opDown,    opIsUp,   opSetMode, opConfigure,
    opReceive, opSend,    opService, opStatus, opFlushRx,
};

}  // namespace

bool chargerRegister(Mcp2515Port& port, uint32_t bitrate) {
  s_chg.port = &port;
  s_chg.bitrate = bitrate;
  s_chg.now_ms = 0;
  s_chg.mode = CAN_PORT_MODE_NORMAL;
  return can_port_register(CHARGER_PORT, &CHARGER_OPS, &s_chg) == ESP_OK;
}

void chargerClock(uint32_t t_ms) { s_chg.now_ms = t_ms; }

}  // namespace interposer
