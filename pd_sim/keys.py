"""Buttons -> Simulator keystrokes, injected through XTEST.

The map is measured, not documented: a probe game printed which Playdate button each
keystroke produced. Worth knowing that A and B are on `s` and `a` — the letters are in
the opposite order to the button names, because they mirror the device's physical
layout, where B sits left of A.

Two things about injection, both learned the hard way:

  * `xdotool key --window <id>` uses XSendEvent, and SDL ignores synthetic events by
    design. Keys sent that way vanish with no error anywhere. Injection must go
    through XTEST — plain `xdotool key`, no `--window` — which needs the target window
    to hold input focus, which needs a window manager (see display.py).
  * A keystroke sent immediately after activating the window is dropped. Focus takes a
    moment to settle, and the Simulator is not listening yet. SETTLE below is that
    moment; without it the first press of any run is lost, which looks exactly like a
    flaky test.
"""

from __future__ import annotations

import shutil
import subprocess
import time

# button -> X keysym, measured against a probe game on Simulator 3.1.1
KEYMAP = {
    "up": "Up",
    "down": "Down",
    "left": "Left",
    "right": "Right",
    "a": "s",       # not a typo — see the module docstring
    "b": "a",
}

# Escape opens the system menu, exactly as the Menu button does on the device: the
# game gets playdate.gameWillPause() and STOPS UPDATING until it is dismissed. Driving
# it is useful (menu handling is real behaviour worth testing) and a trap if you forget
# the loop is now paused.
MENU_KEY = "Escape"

# The crank is the mouse WHEEL over the Simulator window — no keystroke drives it.
# Measured at 4 degrees per click on 3.1.1, and the first click also UNDOCKS the crank,
# which is its own observable event (playdate.crankUndocked).
CRANK_DEGREES_PER_CLICK = 4.0
WHEEL_FORWARD, WHEEL_BACK = "4", "5"

# The real window is ~482x706; the decoy is 10x10. Anything above this is the game.
MIN_WINDOW_AREA = 10_000

SETTLE = 0.3        # after focusing, before the first key
HOLD = 0.05         # keydown -> keyup, long enough for one frame at 30fps
GAP = 0.12          # between presses, so buttonJustPressed sees distinct edges


class InputError(RuntimeError):
    """The Simulator window could not be found or focused."""


def _xdotool(*args: str, display: str) -> str:
    if not shutil.which("xdotool"):
        raise InputError("xdotool not installed — apt-get install -y xdotool")
    out = subprocess.run(
        ["xdotool", *args],
        env={"DISPLAY": display},
        capture_output=True, text=True, timeout=15,
    )
    return out.stdout.strip()


def _geometry(window: str, display: str) -> tuple[int, int, int, int]:
    """x, y, width, height."""
    shell = _xdotool("getwindowgeometry", "--shell", window, display=display)
    values = dict(line.split("=", 1) for line in shell.splitlines() if "=" in line)
    return (int(values["X"]), int(values["Y"]),
            int(values["WIDTH"]), int(values["HEIGHT"]))


def find_window(display: str, timeout: float = 15.0) -> str:
    """The Simulator's window id, once it exists.

    It maps TWO windows matching "Playdate": a 10x10 helper and the real one. Two
    traps, both silent:

      * take the first match and you get the helper, so every mouse coordinate you
        compute from its geometry is nonsense — a crank turn lands outside the window
        and does nothing, with no error
      * the helper maps FIRST, so "wait until a window exists, then pick the largest"
        still returns the helper if you happen to look in that gap

    So wait for one big enough to be real, and only fall back to the largest if the
    timeout runs out.
    """
    deadline = time.monotonic() + timeout
    best = None
    while time.monotonic() < deadline:
        found = _xdotool("search", "--name", "Playdate", display=display)
        for w in (w for w in found.splitlines() if w.strip()):
            try:
                _, _, width, height = _geometry(w, display)
            except (KeyError, ValueError, subprocess.SubprocessError):
                continue
            area = width * height
            if best is None or area > best[0]:
                best = (area, w)
            if area >= MIN_WINDOW_AREA:
                return w
        time.sleep(0.2)
    if best:
        return best[1]
    raise InputError(f"no Playdate Simulator window on {display} after {timeout}s")


def focus(window: str, display: str) -> None:
    """Give the Simulator input focus, and let it settle."""
    _xdotool("windowactivate", window, display=display)
    time.sleep(SETTLE)


def press(button: str, display: str, window: str | None = None) -> None:
    """Press one Playdate button. Re-focuses first: an unfocused window silently
    swallows the keystroke, and re-focusing costs milliseconds."""
    key = KEYMAP.get(button.lower())
    if key is None:
        raise InputError(
            f"unknown button {button!r} — one of: {', '.join(sorted(KEYMAP))}"
        )
    if window:
        focus(window, display)
    _xdotool("keydown", "--clearmodifiers", key, display=display)
    time.sleep(HOLD)
    _xdotool("keyup", "--clearmodifiers", key, display=display)
    time.sleep(GAP)


def menu(display: str, window: str | None = None) -> None:
    """Press Menu. The game pauses (playdate.gameWillPause) until this is sent again."""
    if window:
        focus(window, display)
    _xdotool("key", "--clearmodifiers", MENU_KEY, display=display)
    time.sleep(GAP)


def crank(degrees: float, display: str, window: str) -> float:
    """Turn the crank, by mouse wheel over the window. Returns the degrees actually
    turned, which is quantised to whole clicks.

    The first turn also undocks the crank — a game that gates on isCrankDocked() will
    see that transition, not just the rotation.
    """
    x, y, width, height = _geometry(window, display)
    focus(window, display)
    _xdotool("mousemove", str(x + width // 2), str(y + height // 3), display=display)
    time.sleep(0.1)

    clicks = int(round(abs(degrees) / CRANK_DEGREES_PER_CLICK))
    button = WHEEL_FORWARD if degrees >= 0 else WHEEL_BACK
    for _ in range(clicks):
        _xdotool("click", button, display=display)
        time.sleep(0.08)
    turned = clicks * CRANK_DEGREES_PER_CLICK
    return turned if degrees >= 0 else -turned
