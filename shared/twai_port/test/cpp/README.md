# test/cpp/

The charge interposer's C++ interface to the fake: `twai_fake.h` (namespace
`twaifake`, class `TwaiFake`, `fake()`) and `twai_fake.cpp`, with the names
and meanings of the interposer's own `host_bridge/twai_fake.h` at
`charge-interposer/main` `5426202`, so its tests build on the merged fake as
they are written. Each member calls into `../twai_fake.c`, the model.
`../README.md` has the wiring and what changed from that fake.
