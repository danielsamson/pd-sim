"""A virtual X display for the Simulator to draw into.

The Playdate Simulator is a GTK + SDL2 desktop app: it needs a DISPLAY, and it needs
an audio device. Neither has to be real. `SDL_AUDIODRIVER=dummy` settles the audio,
and Xvfb settles the display — which is what makes the Simulator runnable on a CI
runner, in a container, over SSH.

A window MANAGER is needed too, and that is the part that is easy to miss. Xvfb alone
gives you a display with no focus model: `XSetInputFocus` fails with `BadMatch`, and
SDL only reads the keyboard of a focused window, so every injected keystroke is
silently dropped. The Simulator still runs, still renders, still prints — it simply
never sees input, which reads like input injection being impossible rather than like
a missing WM.
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import time

# Openbox is the smallest thing that provides a focus model. Anything implementing
# _NET_ACTIVE_WINDOW would do.
WM = "openbox"
GEOMETRY = "1024x768x24"


class DisplayError(RuntimeError):
    """Xvfb or the window manager could not be started."""


def _free_display(start: int = 90, end: int = 200) -> int:
    """A display number nobody is on.

    X uses an abstract unix socket per display; binding one is the cheapest reliable
    probe. Racy in principle — two callers could pick the same number between the
    probe and Xvfb starting — so the caller retries rather than trusting this alone.
    """
    for n in range(start, end):
        if os.path.exists(f"/tmp/.X11-unix/X{n}"):
            continue
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            s.bind("\0" + f"/tmp/.X11-unix/X{n}")
            return n
        except OSError:
            continue
        finally:
            s.close()
    raise DisplayError(f"no free X display in :{start}-:{end}")


class VirtualDisplay:
    """Xvfb + a window manager, as a context manager.

    Use it around anything that needs to *interact* with the Simulator. If you only
    need it to run and print, the WM is optional — but it costs a few MB and removes a
    whole class of "why did nothing happen", so it is not optional here.
    """

    def __init__(self, number: int | None = None, geometry: str = GEOMETRY) -> None:
        self.number = number
        self.geometry = geometry
        self._xvfb: subprocess.Popen | None = None
        self._wm: subprocess.Popen | None = None

    @property
    def name(self) -> str:
        return f":{self.number}"

    def __enter__(self) -> VirtualDisplay:
        for tool in ("Xvfb", WM):
            if not shutil.which(tool):
                raise DisplayError(
                    f"{tool} not installed — pd-sim needs a virtual display. "
                    f"On Debian/Ubuntu: apt-get install -y xvfb {WM} xdotool"
                )

        if self.number is None:
            self.number = _free_display()

        self._xvfb = subprocess.Popen(
            ["Xvfb", self.name, "-screen", "0", self.geometry],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        self._await_socket()

        env = {**os.environ, "DISPLAY": self.name}
        self._wm = subprocess.Popen(
            [WM], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        # The WM has to be up BEFORE the Simulator maps its window, or the window is
        # never managed and never focusable. Give it a moment, then check it survived:
        # openbox exiting immediately (a second WM already running, say) is otherwise
        # invisible until keystrokes mysteriously stop arriving.
        time.sleep(0.5)
        if self._wm.poll() is not None:
            self.__exit__(None, None, None)
            raise DisplayError(f"{WM} exited immediately on {self.name}")
        return self

    def _await_socket(self, timeout: float = 10.0) -> None:
        deadline = time.monotonic() + timeout
        path = f"/tmp/.X11-unix/X{self.number}"
        while time.monotonic() < deadline:
            if os.path.exists(path):
                return
            if self._xvfb and self._xvfb.poll() is not None:
                raise DisplayError(f"Xvfb exited before {self.name} was ready")
            time.sleep(0.05)
        raise DisplayError(f"Xvfb did not come up on {self.name} within {timeout}s")

    def __exit__(self, *exc) -> None:
        for proc in (self._wm, self._xvfb):
            if proc and proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
        self._wm = self._xvfb = None

    def env(self) -> dict[str, str]:
        """Environment for a child that should draw here, with audio stubbed."""
        return {
            **os.environ,
            "DISPLAY": self.name,
            "SDL_AUDIODRIVER": "dummy",
            # No accessibility bus on a CI runner, and GTK complains loudly about it
            # on every launch. Not an error, but it lands in the console we parse.
            "NO_AT_BRIDGE": "1",
        }
