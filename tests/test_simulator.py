"""Parsing and verdicts — no SDK, no display, no Simulator.

The console strings here are verbatim from a real headless run on 3.1.1. That matters:
every one of these was a guess at some point, and the guesses were wrong in ways only
running it revealed.
"""

from __future__ import annotations

from pd_sim.simulator import FAILURE, NOISE, RunResult

CRASH = """22:04:17: Loading: /tmp/probe.pdx/
22:04:17: SDK: /tmp/PlaydateSDK-3.1.1
BOOT-OK
FRAME-5-REACHED
Update error: main.lua:7: deliberate runtime failure
stack traceback:
\t[C]: in function 'error'
\tmain.lua:7: in function <main.lua:4>
22:04:18: Update failed, simulator paused.
22:04:18: Soapbox: error: 7, http code: 0"""

CLEAN = """22:15:02: Loading: /tmp/e2e.pdx/
READY
GOT A
22:15:11: writeToFile() OK: /tmp/e2e/shot.png"""


def test_a_lua_error_is_a_failure():
    r = RunResult(console=CRASH, failed=bool(FAILURE.search(CRASH)), booted=True)
    assert r.failed
    assert "deliberate runtime failure" in r.traceback
    assert "stack traceback:" in r.traceback


def test_a_clean_run_is_not_a_failure():
    r = RunResult(console=CLEAN, failed=bool(FAILURE.search(CLEAN)), booted=True)
    assert not r.failed
    assert r.traceback == ""


def test_the_paused_line_alone_is_enough():
    """A crashed game does not exit — the Simulator PAUSES it. Miss this line and a
    run that failed looks like a run that is still going, until the timeout."""
    assert FAILURE.search("22:04:18: Update failed, simulator paused.")


def test_noise_is_dropped_but_the_game_is_not():
    for chatter in ("[sentry] DEBUG crash-safe logs flush",
                    "[E] pw.conf | can't load config client.conf",
                    "22:04:18: Soapbox: error: 7, http code: 0"):
        assert NOISE.search(chatter), chatter
    for real in ("READY", "GOT A", "Update error: main.lua:7: boom",
                 "hp: 40  score: 12"):
        assert not NOISE.search(real), real


def test_noise_does_not_eat_a_traceback():
    """The filter runs over the same text the verdict reads. If it ever swallowed a
    traceback line, every crash would silently pass."""
    kept = [ln for ln in CRASH.splitlines() if not NOISE.search(ln)]
    assert any(FAILURE.search(ln) for ln in kept)
