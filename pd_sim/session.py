"""The whole thing, as one object: display + Simulator + input.

    with Session("game.pdx") as sim:
        sim.wait_for("ready")
        sim.press("a")
        result = sim.finish()
"""

from __future__ import annotations

import time
from pathlib import Path

from .display import VirtualDisplay
from .keys import crank, find_window, focus, menu, press
from .simulator import RunResult, Simulator


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
            self._window = find_window(self._display.name)
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
