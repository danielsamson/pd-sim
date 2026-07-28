# Getting the Simulator's console

Search for this and you will find that it cannot be done. That is true on macOS and
false on Linux, and the difference is one fact about buffering — which is worth writing
down, because the same question cost two separate investigations here and they reached
opposite conclusions while both being right.

## The short version

| where | console read-back | how |
|---|---|---|
| Linux | **works** | run the SDK's `bin/PlaydateSimulator` with stdout on a **pty** |
| macOS | not programmatically | the `.app` relaunches detached; Window ▸ Console, or use the device |
| device | works | USB serial, natively — see [pd-link](https://github.com/danielsamson/pd-link) |

pd-sim does the Linux one. It is why `pd-sim run game.pdx` can fail a build on a Lua
error with nobody at a keyboard.

## Why the received wisdom says it is impossible

The [feature request for stdout output](https://devforum.play.date/t/option-to-have-playdatesimulator-send-console-output-to-stdout/8155)
is open, and the surrounding threads establish, correctly, that:

- on macOS `print()` reaches stdout only when launched from an interactive Terminal
- there is no CLI flag and no environment variable to force it
- Windows builds as a GUI subsystem, so redirection detaches
- only `logToConsole()` and `error()` were ever explicitly wired to stdout

The macOS mechanism is worth understanding, because it is genuinely unfixable from
outside: launching the `.app`'s inner binary makes macOS relaunch it as a detached
application. Whatever you attached — a pipe, a pty via `script` — is attached to a
process that is no longer the one running the game. Tools that appear to manage it
(the VS Code extension, `playdate-simulator-utils`) run the Simulator in an attached
interactive context and inherit stdout the ordinary way.

## Why Linux is different

**The Linux SDK ships a plain ELF binary at `bin/PlaydateSimulator`, alongside `pdc`.**
No `.app` bundle, no relaunch. It writes the game's `print()` to its own stdout like
any other Unix process, and that stdout is inheritable, redirectable and capturable.

The catch is not permissions or platform support. It is **buffering**:

> C stdio is line-buffered when stdout is a terminal and **fully buffered** when it is
> anything else.

Through a pipe, the Simulator's output sits in a 4KB buffer that flushes when the
process exits — and a Simulator does not exit. It runs the game forever. So a pipe
gives you an empty capture, indefinitely, from a program that is printing perfectly
well. That is the exact symptom people report, and it looks identical to "the platform
does not support this".

**A pty is a terminal**, so the same binary line-buffers and every `print()` arrives
immediately. `pty.openpty()` in the standard library; no `stdbuf`, no coreutils
dependency, and it does not care how the process was launched or whether anything is
in the foreground.

```python
import pty, subprocess
main, secondary = pty.openpty()
proc = subprocess.Popen([simulator, "game.pdx"], stdout=secondary, stderr=secondary)
os.close(secondary)
# read from `main` — output arrives line by line, live
```

`pd_sim/simulator.py` is this, plus a drain thread so the pty never fills and blocks
the writer.

## What actually comes out

From a fully backgrounded Simulator, no terminal in sight, on a CI runner:

```
PLAIN-PRINT works
MID-RUN print at frame 20
Update error: main.lua:8: global 'printTable' is not callable (a nil value)
stack traceback:
	main.lua:8: in function <main.lua:5>
Update failed, simulator paused.
```

Both halves matter. `print()` gives a game the ability to report its own state. The
**traceback** is the test signal: `pdc` proves a file compiles, and this proves it
runs. A runtime error on frame 200, or behind a button press, or from an asset that
did not ship, is invisible to every other automated check in a Playdate toolchain.

Two behaviours to know:

- **A crashed game is PAUSED, not exited.** The process stays alive with a zero exit
  status. Judge a run by its console, never its exit code — `RunResult.failed` reads
  the traceback. `playdate.simulator.exit()` also segfaults the Linux Simulator on a
  perfectly healthy run, after flushing, which would make the exit code meaningless
  even if you wanted it.
- **Noise.** sentry, crashpad, pipewire, GTK and (on a runner with no accessibility
  bus) AT-SPI all write to the same stream. `NOISE` in `simulator.py` filters them, and
  a test asserts the filter never swallows a traceback line — because if it ever did,
  every crash would silently pass.

## What this does not give you

Console output is a **stream**, not a protocol. It cannot tell you which line answered
which command: send two things quickly and you get four lines with no correlation.

For request/response, use the bridge — a command file in, tagged `[rc]` replies out,
which is what pd-link does over serial and what `Session.send()` writes into. If you
are adding read-back to the Simulator route, mirror `bridge.lua`'s existing tagging
rather than inventing a second scheme; the wire format has to agree across both halves
of the protocol, and a third convention is how these drift apart.

That is also the honest fix for macOS: it sidesteps the console entirely, and it is a
better answer than the console for anything that needs an answer rather than a log.

**This now exists**, built to exactly that rule — the same `#N` / `[rc#N]` tagging, no
second scheme. `bridge.lua` gained an `outFile` option: every `[rc]` reply is mirrored
to a file in the game's Data dir (cleared once per session), and `pd-link sim.exchange`
(≥ v0.4.6) sends a tagged command and reads its tagged reply back from there. On Linux
pd-sim still reads the console directly; on macOS, where the console cannot be captured,
that reply file is the read-back. Both routes share the one convention on purpose, so
keep them in step — a change to the tag format is a change to both.

## If it stops working

Verified on SDK **3.1.1**, Linux. If a future Linux build wraps the binary the way
macOS does, the pty stops helping. `Simulator.diagnosis()` is the thing that will say
so — it reports what the Simulator actually printed, and when that is nothing, names
the likely cause instead of leaving you with an empty string.

Quick check, no Python involved:

```sh
script -qc "$PLAYDATE_SDK_PATH/bin/PlaydateSimulator game.pdx" /dev/null
```

If your game's `print()` appears there, the pty route is fine and anything failing is
above this layer.
