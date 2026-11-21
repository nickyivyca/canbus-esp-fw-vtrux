"""The bench supply (DEVICES-SPEC K1-K10): a Korad KA3005P behind a
protection service that alone opens it.

    ka3005p.py   the supply's serial protocol (the driver)
    service.py   the protection service: limits, read-back, the output
                 watch, the latched fault, holder-only writes, off when the
                 holder that turned the output on no longer holds the bench
    client.py    the client library every other caller uses, with the K8
                 sequence calls

PROTOCOL is the version of the request protocol between the client library
and the service. A request carrying any other version is refused (K1).
"""

PROTOCOL = 1
