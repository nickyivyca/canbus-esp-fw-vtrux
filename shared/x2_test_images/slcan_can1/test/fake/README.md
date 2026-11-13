# slcan_can1/test/fake/

Host fakes of what `../../main/app_main.c` calls, for `../test_app.c`: ESP-IDF's
USB Serial/JTAG driver and task call, the board's TWAI pins, and the shared
`vtrux/can_port` and `vtrux/twai_port`, as far as the app uses them.
Everything is in `fake_idf.h` and `fake_idf.c`; the other headers stand in
for the real ones by name and include `fake_idf.h`. The fake of the port
records whether it is up, the mode of each bring-up, and every frame offered
to it, and counts a frame offered while it is down or listen-only as a T8a
violation.

| File | Stands in for |
|---|---|
| `fake_idf.h`, `fake_idf.c` | all of it, and the fake's state for the tests |
| `esp_err.h` | `esp_err.h` |
| `board.h` | the board's `board.h` (`vtrux/x2`) |
| `can_port.h` | `vtrux/can_port`'s `can_port.h`, with the `driver/twai.h` message type |
| `twai_port.h` | `vtrux/twai_port`'s `twai_port.h` |
| `driver/` | `driver/usb_serial_jtag.h`; see `driver/README.md` |
| `freertos/` | `freertos/FreeRTOS.h`, `freertos/task.h`; see `freertos/README.md` |
