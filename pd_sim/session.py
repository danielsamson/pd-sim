"""The whole thing, as one object: display + Simulator + input.

    with Session("game.pdx") as sim:
        sim.wait_for("ready")
        sim.press("a")
        result = sim.finish()
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

from .channel import DEFAULT_CMD_FILE, data_dir, send
from .display import VirtualDisplay
from .keys import InputError, crank, find_window, focus, menu, press
from .simulator import RunResult, Simulator, sdk_path


class Session:
    """A headless Simulator you can drive.

    The window is found and focused at start, not lazily at the first press: focusing
    costs a moment to settle, and paying it once up front means the first press
    behaves like every other one. A press that silently vanishes because focus had not
    landed yet is the single most confusing failure this thing has.
    """

    def __init__(self, pdx: str | Path, display_number: int | None = None,
                 interactive: bool = True) -> None:
        self.pdx = Path(pdx)
        self.interactive = interactive
        self._display = VirtualDisplay(display_number)
        self._sim: Simulator | None = None
        self._window: str | None = None
        self._artifacts: list[Path] = []

    def __enter__(self) -> Session:
        self._display.__enter__()
        self._sim = Simulator(self.pdx, self._display.name, self._display.env())
        self._sim.start()
        if self.interactive:
            try:
                self._window = find_window(self._display.name)
            except InputError as e:
                # Attach what the Simulator said (or did not say). Without this the
                # error names the display, which is the one thing that is fine.
                raise InputError(f"{e}\n\n{self._sim.diagnosis()}") from None
            focus(self._window, self._display.name)
        return self

    def __exit__(self, *exc) -> None:
        if self._sim:
            self._sim.stop()
        self._display.__exit__(*exc)

    # -- reading ---------------------------------------------------------------

    @property
    def console(self) -> str:
        assert self._sim
        return self._sim.console

    def wait_for(self, pattern: str, timeout: float = 20.0) -> bool:
        assert self._sim
        return self._sim.wait_for(pattern, timeout)

    def run_for(self, seconds: float) -> None:
        time.sleep(seconds)

    # -- driving ---------------------------------------------------------------

    def _require_interactive(self) -> None:
        if not self.interactive:
            raise RuntimeError("this Session was created with interactive=False")

    def press(self, *buttons: str) -> None:
        """Press buttons in order. Names are Playdate's — a, b, up, down, left,
        right — not the keyboard letters they happen to map to."""
        self._require_interactive()
        assert self._window
        for button in buttons:
            press(button, self._display.name, self._window)

    def menu(self) -> None:
        """Press Menu. The game PAUSES until you send it again — gameWillPause fires
        and update() stops. Anything you wait for after this will time out unless the
        game prints from a pause handler."""
        self._require_interactive()
        menu(self._display.name, self._window)

    def crank(self, degrees: float) -> float:
        """Turn the crank. Returns the degrees actually turned (quantised to whole
        mouse-wheel clicks). The first turn also undocks it."""
        self._require_interactive()
        assert self._window
        return crank(degrees, self._display.name, self._window)

    def send(self, command: str, cmd_file: str = DEFAULT_CMD_FILE) -> None:
        """Send a command line to the running game — the way to pass VALUES.

        `press("a")` can only press a button; this can say `set 2.cut 0.4`. It needs
        the game to poll its command file (bridge.lua does), and in exchange it is
        deterministic: no focus, no window, no key map, no timing. Prefer it whenever
        both would work.
        """
        send(command, self.pdx, sdk_path(), cmd_file)

    @property
    def data_dir(self) -> Path:
        """The game's Data directory — where its saves and exports land, and where the
        command file goes. Useful in both directions."""
        return data_dir(self.pdx, sdk_path())

    def screenshot(self, path: str | Path) -> Path:
        """Capture the Simulator WINDOW — works on any .pdx, no cooperation at all.

        This is the zero-cooperation option and it is not the exact framebuffer: you
        get the whole window, chrome included (the device body, the d-pad, the crank
        widget), at whatever zoom the Simulator is using. Good for "did it draw
        anything", for eyeballing a run, and for a CI artifact on failure.

        When you want the game's actual 400x240 output — for comparing against a
        golden, or measuring anything — have the game call
        `playdate.simulator.writeToFile` and use `await_file` instead. That is the
        difference between the game's output and a photograph of a monitor.
        """
        if not shutil.which("import"):
            raise RuntimeError(
                "ImageMagick's `import` not installed — apt-get install -y imagemagick. "
                "(Or have the game call playdate.simulator.writeToFile and use await_file.)"
            )
        window = self._window or find_window(self._display.name)
        target = Path(path)
        subprocess.run(
            ["import", "-window", window, str(target)],
            env={"DISPLAY": self._display.name, "PATH": os.environ.get("PATH", "/usr/bin:/bin")},
            check=True, capture_output=True, timeout=30,
        )
        self._artifacts.append(target)
        return target

    def await_file(self, path: str | Path, timeout: float = 15.0) -> Path:
        """Wait for a file the GAME writes — a screenshot, an exported state.

        Screenshots come from inside the game (`playdate.simulator.writeToFile`), not
        from grabbing the X window, and deliberately so: writeToFile hands over the
        exact 400x240 framebuffer, while a window grab returns whatever zoom the
        Simulator happened to be at, with chrome. One is the game's output; the other
        is a photo of a monitor.

        Beware one frame of lag: `getDisplayImage()` returns the last COMPLETED frame,
        so a capture taken during `update()` shows the previous one. Write on the frame
        after the state you mean to capture.
        """
        target = Path(path)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if target.exists() and target.stat().st_size > 0:
                self._artifacts.append(target)
                return target
            time.sleep(0.1)
        raise TimeoutError(
            f"{target} was not written within {timeout}s — does the game call "
            f"playdate.simulator.writeToFile({str(target)!r})?"
        )

    def finish(self) -> RunResult:
        assert self._sim
        self._sim.stop()
        return self._sim.result(self._artifacts)
