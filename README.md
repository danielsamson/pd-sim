# pd-sim

**Drive a Playdate Simulator from a script, with no display.** Run a `.pdx`, press
buttons, turn the crank, capture the screen, and assert on what the game printed —
on a CI runner, in a container, over SSH.

```sh
pd-sim run game.pdx                                  # does it boot and stay up?
pd-sim run game.pdx --press a,right,a --seconds 20
pd-sim run game.pdx --await shot.png                 # wait for a game-written screenshot
pd-sim keys                                          # the button -> keystroke map
```

`run` exits non-zero when the game raises a Lua error, so it is a test as it stands.

## Does it need a cooperating game?

**Almost none of it.** On any `.pdx`, with zero cooperation:

- run it, and tell whether it booted and stayed up
- catch a Lua error, with the traceback
- capture the game's `print()` output
- press every button, turn the crank, open the menu
- screenshot the Simulator window

Two things need the game's help, and both have a no-cooperation alternative:

| want | zero cooperation | with cooperation |
|---|---|---|
| a picture | `--shot` — the window, chrome and all | `--await` — the exact 400×240 framebuffer, via one `writeToFile` line |
| send a value | — (buttons only) | `--send "set 2.cut 0.4"` — the game polls a command file |

So a random `.pdx` you did not write is testable for boot, crashes, input handling and
appearance. A game you *do* control additionally gets exact frames and scripted values.

## Why

`pdc` proves a file **compiles**. Nothing short of hardware proved it **runs** — so a
runtime error in code that only executes on frame 200, or under a button press, or
when a dependency's asset is missing, survived every automated check and waited for a
human with a device.

The Simulator is a GTK + SDL2 desktop app, but nothing about it needs a desk. Given a
virtual display it runs anywhere, and its stdout carries the game's `print()` **and a
Lua stack traceback**:

```
Update error: main.lua:7: attempt to index a nil value (field 'icons')
stack traceback:
    main.lua:7: in function <main.lua:4>
Update failed, simulator paused.
```

That is a real test signal, and it is available on every merge instead of once a week
when someone plugs in a cable.

## What it is not

**Not a substitute for the device.** This is x86 running an interpreter, not a 168MHz
Cortex-M7 with 16MB of RAM. Frame budget, audio, real timing, accelerometer and a
native extension's ARM binary exist only on hardware — see
[pd-link](https://github.com/danielsamson/pd-link) for that half.

**Not a pixel oracle.** For exact 1-bit rendering assertions without any Simulator at
all, [pd-canvas](https://github.com/danielsamson/pd-canvas) rasterizes in pure Lua in
milliseconds. pd-sim is for the thing pd-canvas cannot do: the *running game* — input,
transitions, state over time.

The split, end to end: **pd-canvas** checks a screen's pixels, **pd-sim** checks the
game runs and responds, **pd-link** checks it on real hardware.

## Install

```sh
pip install "pd-sim @ git+https://github.com/danielsamson/pd-sim@v0.1.0"
sudo apt-get install -y xvfb openbox xdotool          # Debian/Ubuntu
```

Needs the Playdate SDK, with `PLAYDATE_SDK_PATH` set or its `bin/` on `PATH`. The
Linux SDK ships `PlaydateSimulator` alongside `pdc` — that is what makes this possible.

## As a library

```python
from pd_sim import Session

with Session("game.pdx") as sim:
    assert sim.wait_for("ready"), sim.console
    sim.press("a", "right")
    sim.crank(90)
    sim.await_file("shot.png")
    result = sim.finish()
    assert not result.failed, result.traceback
```

## Input

| control | how | notes |
|---|---|---|
| D-pad | arrow keys | |
| A / B | `s` / `a` | the letters read backwards — they mirror the device, where B is left of A |
| Menu | `Escape` | fires `gameWillPause`; **the game stops updating** until sent again |
| crank | mouse wheel | 4° per click; the first turn also undocks it |
| Lock | — | no known input; not simulated |
| Lock | not wired yet | the Simulator has a LOCK button in its UI — reachable, not implemented |
| values | the command file | `set 2.cut 0.4` — **needs the game to poll** |
| accelerometer | not wired yet | **reads** fine headless; the Simulator has a tilt widget — reachable, not implemented |

All of it is measured against Simulator 3.1.1, not documented — `pd-sim keys` prints
the map, and `tests/test_hardware.py` re-measures it, so a Simulator update that
changes a binding fails a test instead of silently breaking every suite downstream.

### Sending values, not just buttons

A keystroke can press A. It cannot say `set 2.cut 0.4`. For that the Simulator has a
better channel than input injection — the game's Data directory:

```sh
pd-sim run game.pdx --send "preset eno" --send "set 2.cut 0.4"
```

The host appends a line to `SDK/Disk/Data/<bundleID>/<cmdfile>`; the game polls it each
frame and runs it. That is the convention [bridge.lua](https://github.com/danielsamson/pd-link)
standardizes, and what `pd-link serve` already uses to drive a Simulator.

**Prefer it over `press()` wherever both would work.** It is deterministic — no focus,
no window, no key map, no timing — and it carries arguments, so one call sets a value
exactly instead of pressing a button eleven times and hoping. The trade is pd-link's
own: keystrokes work on any `.pdx` with no cooperation; this needs the game to poll,
and in exchange it can say anything.

The Data directory works in both directions: `sim.data_dir` is also where the game's
saves and exports land.

### The accelerometer, precisely

`playdate.startAccelerometer()` works in a headless Simulator and
`readAccelerometer()` returns a real upright reading, so a game that *reads* tilt runs
fine and can be tested for everything except its response to motion.

Changing the value is unfinished, and the honest state is: it happened once. A
ctrl-drag across the device body moved it from `x=0.000 y=1.000` to `x=0.906
y=-0.424` — unmistakably a tilt. Repeating that same gesture in isolation does
nothing, twice over, and neither do plain, shift or alt drags. The one success came
after three other drags, so something stateful is likely involved that has not been
identified.

Until there is a gesture that works every time, there is no `tilt()` here. An input
method that works one attempt in five is worse than none: it turns every failure into
a question about the harness rather than the game.

## Two things that cost real time

- **A window manager is required** — this is the one that matters. Xvfb alone has no
  focus model, `XSetInputFocus` fails with `BadMatch`, and SDL only reads a focused
  window's keyboard. Without one the Simulator runs, renders and prints perfectly while
  ignoring every keystroke, which reads like input injection being impossible rather
  than a missing package. (Once focused, both `xdotool key` and `key --window` work —
  an earlier version of this file blamed the event type, which was wrong.)
- **The Simulator maps two windows named "Playdate".** One is a 10×10 helper. Take the
  first match and every mouse coordinate you compute is nonsense; pick by area.

## Exit codes and screenshots

Judge a run by its **console**, never its exit status. The Simulator *pauses* a crashed
game rather than exiting, and it segfaults inside `playdate.simulator.exit()` on a
perfectly healthy run — after flushing, so artifacts survive. `RunResult.failed` reads
the traceback, which is the honest signal.

Screenshots come from inside the game:

```lua
playdate.simulator.writeToFile(playdate.graphics.getDisplayImage(), "shot.png")
```

That is the exact 400×240 framebuffer. Grabbing the X window instead would return
whatever zoom the Simulator happened to be at, with chrome — a photo of a monitor
rather than the game's output. Note `getDisplayImage()` returns the last **completed**
frame, so a capture taken during `update()` shows the previous one.
