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

# The accelerometer dial, as fractions of the window. MEASURED, by dragging and
# reading the values back out of a probe game — which is the only way to get these
# right, and the reason they are trustworthy: a drag lands where the game says it
# lands, not where a screenshot looked like it should.
#
#   deflection  ->  reading        (both axes, linear)
#     0.25r          0.118
#     0.50r          0.265
#     0.75r          0.397
#     1.00r          0.544
#
# The centre is 0.8435 down, not 0.871: dragging to the latter reads y=+0.279, i.e.
# half a radius low. That discrepancy is exactly why this is calibrated against the
# game's own numbers instead of eyeballed off an image.
ACCEL_CENTRE_X = 0.207          # of window width
ACCEL_CENTRE_Y = 0.871          # of window height, in CAPTURE space
ACCEL_RADIUS = 0.055            # of window height
ACCEL_GAIN = 0.55               # g per full-radius deflection

# WIDGET POSITIONS, as fractions of the window, read off a `sim.screenshot()` capture.
# Read them that way too if they ever need adjusting — the capture is the map.
LOCK_BUTTON = (0.944, 0.068)
CRANK_DOCKED = (0.542, 0.725)
CRANK_FIELD = (0.747, 0.725)

# Capture space is not root space: a point seen at capture y appears CAPTURE_Y_OFFSET
# pixels lower than where the pointer must go. Measured by sweeping clicks across the
# Docked checkbox until the game reported the dock changing.
#
# It went unnoticed while only the accelerometer was wired, because a 38px dial
# tolerates a 22px error — it just reads half a radius off, which is exactly the
# "resting y=+0.279" that got explained away as the dial's centre being elsewhere. It
# was not; it was this. A 14px checkbox has no such tolerance and misses entirely,
# clicking empty background with nothing reporting anything.
#
# test_the_widget_offset_is_still_right pins it: if a window manager theme changes it,
# that fails rather than every widget quietly missing.
CAPTURE_Y_OFFSET = 22

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


# Startup is far slower on a CI runner than on a developer machine: measured at ~26s
# from launch to "Loading:" on a GitHub runner, against ~3s locally. Timeouts here are
# generous on purpose — they cost nothing when things work, and when they are too tight
# the failure is not "timed out" but something much more confusing. A short window
# timeout in particular means falling back to the 10x10 decoy, and then every mouse
# coordinate is silently wrong.
WINDOW_TIMEOUT = 90.0


def find_window(display: str, timeout: float = WINDOW_TIMEOUT) -> str:
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
    raise InputError(
        f"no Playdate Simulator window on {display} after {timeout}s — the Simulator "
        "is not drawing. If its console is also empty, it is almost certainly a "
        "missing shared library rather than anything about the display; see "
        "Simulator.diagnosis()."
    )


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


def _click_widget(fx: float, fy: float, display: str, window: str) -> None:
    """Click a widget given its position in CAPTURE coordinates."""
    wx, wy, width, height = _geometry(window, display)
    focus(window, display)
    _xdotool("mousemove",
             str(wx + int(width * fx)),
             str(wy + int(height * fy) - CAPTURE_Y_OFFSET), display=display)
    time.sleep(0.15)
    _xdotool("click", "1", display=display)
    time.sleep(GAP)


def lock(display: str, window: str) -> None:
    """Press the Simulator's LOCK button — the device lock, not the menu.

    A game observes it through playdate.deviceWillLock / deviceDidUnlock. It toggles,
    so send it twice to lock and unlock again.
    """
    _click_widget(*LOCK_BUTTON, display=display, window=window)


def crank_dock_toggle(display: str, window: str) -> None:
    """Click the Docked checkbox. Toggles; the game sees crankDocked/crankUndocked."""
    _click_widget(*CRANK_DOCKED, display=display, window=window)


def set_crank(degrees: float, display: str, window: str) -> float:
    """Set the crank to an EXACT angle, by typing into the Simulator's number field.

    Strictly better than turning it with the mouse wheel: that quantises to 4-degree
    clicks and only moves relative to wherever the crank already was, so putting it at
    a known angle means tracking state and hoping. This types the number.
    """
    degrees = float(degrees) % 360.0
    wx, wy, width, height = _geometry(window, display)
    focus(window, display)
    fx, fy = CRANK_FIELD
    _xdotool("mousemove",
             str(wx + int(width * fx)),
             str(wy + int(height * fy) - CAPTURE_Y_OFFSET), display=display)
    time.sleep(0.15)
    # Triple-click selects the field's contents; typing then replaces rather than
    # appending to whatever is already there.
    for _ in range(3):
        _xdotool("click", "1", display=display)
        time.sleep(0.05)
    time.sleep(0.2)
    _xdotool("key", "--clearmodifiers", "ctrl+a", display=display)
    time.sleep(0.1)
    _xdotool("type", f"{degrees:.0f}", display=display)
    time.sleep(0.2)
    _xdotool("key", "--clearmodifiers", "Return", display=display)
    time.sleep(GAP)
    return degrees


def tilt(x: float, y: float, display: str, window: str) -> tuple[float, float]:
    """Tilt the device by dragging the Simulator's accelerometer dial.

    `x` and `y` are accelerometer readings in g, roughly -1..1: `tilt(0, 1)` is
    upright, `tilt(1, 0)` is on its right edge. Returns what was requested clamped to
    what the dial can actually reach.

    This is a UI widget, so it is approximate — the game's own `readAccelerometer()`
    is the truth, and a test should assert on that rather than on this return value.
    The mapping is linear at ACCEL_GAIN per radius; see the constants above for the
    measurements.

    There is no keyboard route to this, and no gesture: an earlier attempt found a
    ctrl-drag that moved the values once and never again. The dial was there the whole
    time. Capture the window before hunting for a shortcut.
    """
    wx, wy, width, height = _geometry(window, display)
    radius = height * ACCEL_RADIUS
    centre_x = wx + int(width * ACCEL_CENTRE_X)
    centre_y = wy + int(height * ACCEL_CENTRE_Y) - CAPTURE_Y_OFFSET

    # Clamp to the dial: past a full radius it stops tracking, so asking for more
    # silently gives less. Report what was actually asked for.
    limit = ACCEL_GAIN
    x = max(-limit, min(limit, x))
    y = max(-limit, min(limit, y))

    target_x = centre_x + int(x / ACCEL_GAIN * radius)
    target_y = centre_y + int(y / ACCEL_GAIN * radius)

    focus(window, display)
    _xdotool("mousemove", str(centre_x), str(centre_y), display=display)
    time.sleep(0.15)
    _xdotool("mousedown", "1", display=display)
    time.sleep(0.15)
    steps = 6
    for i in range(1, steps + 1):
        _xdotool("mousemove",
                 str(centre_x + (target_x - centre_x) * i // steps),
                 str(centre_y + (target_y - centre_y) * i // steps), display=display)
        time.sleep(0.08)
    _xdotool("mouseup", "1", display=display)
    time.sleep(GAP)
    return (x, y)
