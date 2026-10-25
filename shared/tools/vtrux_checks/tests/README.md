# shared/tools/vtrux_checks/tests

**Owner:** tester (gen-inhibit).

The build checks' own tests, one file per check, each runnable alone:

| File | What |
|---|---|
| `test_scope.py` | R17 (`../scope.py`): each rule failing on a planted input and passing on a faithful one, and its readers on small files under `scratch/vtrux-checks-tests/` |
| `test_options.py` | R18 (`../options.py`): each rule failing on a planted input and passing on a faithful one, the profile's reader, and the command line on a small build tree under `scratch/vtrux-checks-tests/` |
