# jammer/main/ -- TEST EQUIPMENT, NOT FOR VEHICLE

The jammer's own ESP-IDF component: the app around `../components/jam_pulse`.

| File | What |
|---|---|
| `CMakeLists.txt` | the component; no TWAI driver among its requirements |
| `idf_component.yml` | the dependencies: the board `vtrux/x2` at a shared/main tag, from git, and ESP-IDF 5.4.2 exactly (shared/tools R33) |
| `jammer_main.c` | `app_main`: CAN1's TXD recessive first, the MCP2515 reset, the console, the pulse engine and the line task, the banner; the line handling (`ID`, then `jam_line`, else `ERR unknown_command`) |
| `jammer_main.h` | the line handling, declared for the host tests |
| `console_usj.c`, `.h` | the console on the USB Serial/JTAG port: every line `JAMMER <text>\r\n`, written whole under a lock with a 20 ms timeout. The functions are `jcon_*`: ESP-IDF's `esp_vfs_console` already has a `console_read` |
| `mcp2515_reset.c`, `.h` | the MCP2515's SPI RESET and the CANSTAT and CANCTRL reads, through the board layer's SPI CAN port; only RESET (0xC0) and READ (0x03) are ever sent |
