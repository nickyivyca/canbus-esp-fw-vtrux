// c12_bench: the MCP2515 conformance sequence on the X2's real chip.
//
// A minimal standalone ESP-IDF image -- no Arduino, no PlatformIO, no
// product code -- that implements mcpconf::Target over spi_master and
// nothing else (reviewer, 2026-10-13). The sequence itself is the host
// suite's conformance.cpp, compiled in unchanged, so the transcript this
// prints diffs line for line against mcp2515_conformance_fake.txt. It
// judges the chip against its datasheet, not our driver, so the same image
// serves before and after the ESP-IDF port.
//
// What the board cannot do itself -- put a frame on the charger segment,
// see what reached the wire, stop acknowledging -- it asks the host for,
// over the USB Serial/JTAG console, one request line at a time:
//
//   board -> host                 host -> board
//   @REQ CANINJECT                @YES | @NO      (a sender is attached)
//   @REQ INJECT <id> <ext> <tag> <dlc>
//                                 @OK             (the frame was sent AND
//                                                  its echo came back, so
//                                                  it is on the wire and
//                                                  the chip has it)
//   @REQ CLEAR                    @OK             (start a wire capture)
//   @REQ TAGS                     @TAGS 1,0,2 | @TAGS -   (data[0] of every
//                                                  frame received since
//                                                  CLEAR, oldest first)
//   @REQ CANWITHHOLD              @YES | @NO      (nothing else on the
//                                                  segment, confirmed)
//   @REQ ACK <0|1>                @OK             (the host's adapter in
//                                                  listen-only, or normal)
//
// and the host drives the board with `HELLO?` (answered with the image's
// identity) and `RUN <C1..C13|all>` (answered with the transcript between
// `@BEGIN` and `@END`). Every C12 action is sequenced by the script, so no
// timing precision is needed of the host (conformance.h, injectFrame).
//
// The X2's MCP2515: 16 MHz crystal on SPI2 (FSPI's IO_MUX pins), CS 10,
// SCK 12, MISO 13, MOSI 11 (bringup notes, "The board"). CS is driven as a
// plain GPIO because the sequence frames its own transactions with
// csLow()/csHigh(). The TWAI port is never started, so the vehicle
// segment sees nothing from this image.
//
// Tester, 2026-10-13. firmware/test/c12_bench/README.md has the build.

#include <stdio.h>
#include <string.h>

#include <string>
#include <vector>

#include "driver/gpio.h"
#include "driver/spi_master.h"
#include "driver/usb_serial_jtag.h"
#include "driver/usb_serial_jtag_vfs.h"
#include "esp_app_desc.h"
#include "esp_idf_version.h"
#include "esp_rom_sys.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#include "../../host_bridge/conformance.h"

namespace {

const gpio_num_t PIN_CS = GPIO_NUM_10;
const gpio_num_t PIN_SCK = GPIO_NUM_12;
const gpio_num_t PIN_MISO = GPIO_NUM_13;
const gpio_num_t PIN_MOSI = GPIO_NUM_11;
// 1 MHz: well inside the chip's 10 MHz, and slow enough that wiring is
// never the question. Nothing in the sequence is timed against SPI.
const int SPI_HZ = 1000000;

char line_buf[256];

// One line from the host, without its newline. Blank lines are skipped.
const char* readLine() {
  for (;;) {
    if (!fgets(line_buf, sizeof(line_buf), stdin)) {
      vTaskDelay(1);
      continue;
    }
    size_t n = strlen(line_buf);
    while (n && (line_buf[n - 1] == '\n' || line_buf[n - 1] == '\r'))
      line_buf[--n] = 0;
    if (n) return line_buf;
  }
}

// Send one request and return the host's reply (the first line that
// starts with '@'; anything else the host prints is ignored).
std::string request(const char* req) {
  printf("@REQ %s\n", req);
  fflush(stdout);
  for (;;) {
    const char* r = readLine();
    if (r[0] == '@') return std::string(r);
  }
}

bool yes(const char* req) { return request(req) == "@YES"; }

struct BenchTarget : mcpconf::Target {
  spi_device_handle_t dev = nullptr;

  bool begin() {
    gpio_config_t cs = {};
    cs.pin_bit_mask = 1ULL << PIN_CS;
    cs.mode = GPIO_MODE_OUTPUT;
    if (gpio_config(&cs) != ESP_OK) return false;
    gpio_set_level(PIN_CS, 1);
    spi_bus_config_t bus = {};
    bus.mosi_io_num = PIN_MOSI;
    bus.miso_io_num = PIN_MISO;
    bus.sclk_io_num = PIN_SCK;
    bus.quadwp_io_num = -1;
    bus.quadhd_io_num = -1;
    if (spi_bus_initialize(SPI2_HOST, &bus, SPI_DMA_DISABLED) != ESP_OK)
      return false;
    spi_device_interface_config_t d = {};
    d.mode = 0;
    d.clock_speed_hz = SPI_HZ;
    d.spics_io_num = -1;
    d.queue_size = 1;
    return spi_bus_add_device(SPI2_HOST, &d, &dev) == ESP_OK;
  }

  void csLow() override { gpio_set_level(PIN_CS, 0); }
  void csHigh() override { gpio_set_level(PIN_CS, 1); }
  uint8_t transfer(uint8_t out) override {
    spi_transaction_t t = {};
    t.length = 8;
    t.flags = SPI_TRANS_USE_TXDATA | SPI_TRANS_USE_RXDATA;
    t.tx_data[0] = out;
    spi_device_polling_transmit(dev, &t);
    return t.rx_data[0];
  }

  // "At least this long": a deadline on the 1 us esp_timer, yielding a
  // tick only while more than two ticks remain.
  void advanceUs(uint64_t us) override {
    const int64_t end = esp_timer_get_time() + (int64_t)us;
    for (;;) {
      const int64_t left = end - esp_timer_get_time();
      if (left <= 0) return;
      if (left > 2000 * portTICK_PERIOD_MS) vTaskDelay(1);
      else esp_rom_delay_us(left > 1000 ? 1000 : (uint32_t)left);
    }
  }
  uint64_t nowUs() override { return (uint64_t)esp_timer_get_time(); }

  std::vector<uint8_t> wireTags() override {
    std::vector<uint8_t> out;
    const std::string r = request("TAGS");
    if (r.compare(0, 6, "@TAGS ") != 0) return out;
    const std::string body = r.substr(6);
    if (body == "-") return out;
    size_t i = 0;
    while (i < body.size()) {
      size_t j = body.find(',', i);
      if (j == std::string::npos) j = body.size();
      out.push_back((uint8_t)strtoul(body.substr(i, j - i).c_str(),
                                     nullptr, 10));
      i = j + 1;
    }
    return out;
  }
  void clearWire() override { request("CLEAR"); }

  bool canWithholdAck() override { return yes("CANWITHHOLD"); }
  void setAcknowledged(bool on) override {
    request(on ? "ACK 1" : "ACK 0");
  }

  bool canInject() override { return yes("CANINJECT"); }
  void injectFrame(uint32_t id, bool ext, uint8_t tag,
                   uint8_t dlc) override {
    char req[64];
    snprintf(req, sizeof(req), "INJECT %lX %d %02X %u", (unsigned long)id,
             ext ? 1 : 0, tag, dlc);
    request(req);
  }

  const char* subjectName() override {
    return "silicon (X2 MCP2515, c12_bench, single-adapter)";
  }
};

void hello() {
  const esp_app_desc_t* d = esp_app_get_description();
  char sha[65];
  for (int i = 0; i < 32; i++) sprintf(sha + 2 * i, "%02x",
                                       d->app_elf_sha256[i]);
  printf("@HELLO c12_bench version=%s elf_sha256=%s idf=%s\n", d->version,
         sha, esp_get_idf_version());
  fflush(stdout);
}

}  // namespace

extern "C" void app_main(void) {
  usb_serial_jtag_driver_config_t cfg =
      USB_SERIAL_JTAG_DRIVER_CONFIG_DEFAULT();
  usb_serial_jtag_driver_install(&cfg);
  usb_serial_jtag_vfs_use_driver();
  setvbuf(stdin, nullptr, _IONBF, 0);

  static BenchTarget t;
  const bool spi_ok = t.begin();
  hello();
  printf("@READY spi=%s\n", spi_ok ? "ok" : "FAILED");
  fflush(stdout);

  for (;;) {
    const char* cmd = readLine();
    if (strcmp(cmd, "HELLO?") == 0) {
      hello();
    } else if (strncmp(cmd, "RUN ", 4) == 0) {
      const std::string name(cmd + 4);
      if (!spi_ok) {
        printf("@END %s spi-failed\n", name.c_str());
      } else {
        mcpconf::Recorder rec;
        printf("@BEGIN %s\n", name.c_str());
        const bool known = mcpconf::runNamed(t, rec, name.c_str());
        if (known) rec.print(t.subjectName());
        printf("@END %s %s\n", name.c_str(), known ? "ok" : "unknown-step");
      }
      fflush(stdout);
    } else {
      printf("@ERR unknown command: %s\n", cmd);
      fflush(stdout);
    }
  }
}
