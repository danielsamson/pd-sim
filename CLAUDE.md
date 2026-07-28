# pd-sim — agent notes

Drives a Playdate Simulator headlessly. Everything below is measured against Simulator
3.1.1, not taken from documentation — re-measure rather than assume if a version changes.

## Where this sits

- **pd-canvas** — a screen's pixels, pure Lua, milliseconds, no Simulator.
- **pd-sim** (here) — the *running game*: input, transitions, state over time.
- **pd-link** — real hardware, which is the only place timing, audio, the frame budget
  and an ARM binary exist.

Do not describe pd-sim as replacing either neighbour. It is x86 running an interpreter.

## Facts that cost time to learn

- **A window manager is required**, not just Xvfb. Without a focus model
  `XSetInputFocus` fails with `BadMatch` and SDL reads no keyboard — the Simulator runs,
  renders and prints perfectly while ignoring every keystroke.
- **Injection must use XTEST** (`xdotool key`, no `--window`). SDL ignores synthetic
  XSendEvent keys by design; they vanish with no error.
- **stdout must be a PTY.** The Simulator's stdio is fully buffered when stdout is not a
  terminal, so over a plain pipe nothing arrives until it exits — and it does not exit.
- **Two windows match "Playdate".** A 10×10 decoy maps FIRST, then the real ~482×706
  one. Pick by area, and wait for one big enough to be real.
- **Judge a run by its console, never its exit code.** A crashed game is *paused*, not
  exited, and `playdate.simulator.exit()` segfaults on a healthy run (after flushing,
  so artifacts survive).
- **The first key after focusing is dropped** unless focus has settled (`SETTLE`).
- **`getDisplayImage()` lags one frame** — it returns the last completed frame, so a
  capture during `update()` shows the previous one.

## Input map (measured)

| control | how |
|---|---|
| D-pad | arrow keys |
| A / B | `s` / `a` — backwards-looking, because they mirror the device where B is left of A |
| Menu | `Escape` — fires `gameWillPause` and **stops the update loop** until sent again |
| crank | mouse wheel over the window, 4°/click; the first turn undocks |
| Lock | no known input |
| accelerometer | unsolved |

`tests/test_sdk.py` re-measures this against a real Simulator, so a rebinding fails a
test rather than silently breaking downstream suites. Keep it that way.

## Tests

`pytest` runs the parsing tests only. The ones that matter need the SDK and a display:

    pytest -m sdk            # needs PLAYDATE_SDK_PATH, xvfb, openbox, xdotool

CI runs both. A change to input, buffering or window selection that is not covered by
an `sdk`-marked test is not covered at all — the unit tests cannot see any of it.
