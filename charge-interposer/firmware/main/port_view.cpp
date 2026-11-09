#include "port_view.h"

#include <string.h>

namespace interposer {

static const uint32_t ONLY_BITRATE = 500000UL;

bool PortView::begin(uint32_t bitrate) {
  if (bitrate != ONLY_BITRATE) return false;
  return can_port_up(index_) == ESP_OK;
}

bool PortView::receive(RxFrame& out) {
  twai_message_t m;
  if (can_port_receive(index_, &m, 0) != ESP_OK) return false;  // none waiting
  out.id = m.identifier;
  out.ext = m.extd != 0;
  out.len = m.data_length_code > 8 ? 8 : m.data_length_code;
  for (uint8_t i = 0; i < out.len; i++) out.data[i] = m.data[i];
  out.t_ms = last_ms_;
  return true;
}

bool PortView::send(uint32_t id, bool ext, const uint8_t* data, uint8_t len) {
  twai_message_t m;
  memset(&m, 0, sizeof(m));
  m.identifier = id;
  m.extd = ext ? 1 : 0;
  m.data_length_code = len > 8 ? 8 : len;
  for (uint8_t i = 0; i < m.data_length_code; i++) m.data[i] = data[i];
  // A wait of 0: enqueue or fail, never block. In no-ACK self-test the
  // backend sets the self-reception flag itself (twai_port T2).
  return can_port_send(index_, &m, 0) == ESP_OK;
}

void PortView::service(uint32_t t_ms) {
  last_ms_ = t_ms;
  can_port_service(index_);
}

can_port_status_t PortView::status() const {
  can_port_status_t s;
  memset(&s, 0, sizeof(s));
  if (can_port_status(index_, &s) != ESP_OK) memset(&s, 0, sizeof(s));
  return s;
}

}  // namespace interposer
