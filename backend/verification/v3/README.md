# verification/v3 — evidence index (generated during the final build, before packaging)

| File | What it is |
|---|---|
| `pytest_v3_verbose.log` | Full suite, 61 passed, 0 warnings (clean venv, exact pins, Python 3.12.3) |
| `compileall_v3.log` | `python -m compileall` (empty = success) |
| `requirements.linux-py312.freeze.txt` | `pip freeze` of the environment used for training, testing and screenshots |
| `requirements.windows-cp314.resolved.txt` | Full dependency tree resolved for **Windows + CPython 3.14, wheels only** (uv). Evidence that pip can install the pins there; not an execution on Windows |
| `browser_capture_a/b/c.log` | Real-Chrome capture runs (default / test-fixture OSM path / dark). Each reports `JS PAGE ERRORS: 0` |
| `browser_click_test.log` | Click map → origin → destination → routes, in real Chrome |

Not in this folder by design: the **post-ZIP clean-extract verification** (fresh venv install from `requirements.txt`, full test run, startup commands,
manifest verification of every file). Those logs verify the final archive itself, so they cannot live inside it; they are delivered next to the ZIP as
`final_clean_room_verification.log`.
