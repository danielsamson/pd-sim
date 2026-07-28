"""Running a .pdx in a headless Simulator, and reading what it says.

The Simulator's stdout is the whole reason this is worth doing: the game's `print()`
lands there, and so does a Lua runtime error, with a stack traceback:

    Update error: main.lua:7: deliberate runtime failure
    stack traceback:
        [C]: in function 'error'
        main.lua:7: in function <main.lua:4>
    Update failed, simulator paused.

That is a real test signal, and nothing else in a Playdate toolchain produces it
without a device. `pdc` proves a file compiles; a headless run proves it *runs*.
"""

from __future__ import annotations

import os
import pty
import queue
import re
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

# The Simulator's own chatter, plus its crash reporter's, plus whatever the container's
# audio stack complains about. None of it is the game, and all of it drowns the game.
NOISE = re.compile(
    r"pw\.conf|pipewire|\[sentry\]|crashpad|file_io_posix|process_reader|"
    r"http_transport|libGL|dbus|Soapbox|SDL2|GTK|gtk"
)

# A Lua error surfaces as these. "simulator paused" is the decisive one: the Simulator
# stops the game rather than exiting, so a run that failed still looks alive.
FAILURE = re.compile(r"Update error:|stack traceback:|Update failed, simulator paused")


def sdk_path() -> Path:
    return Path(os.environ.get("PLAYDATE_SDK_PATH", "~/Developer/PlaydateSDK")).expanduser()


def simulator_binary() -> Path:
    """The Simulator executable. On Linux it ships in the SDK's bin/ — the same place
    as pdc — which is what makes headless CI possible at all."""
    found = shutil.which("PlaydateSimulator")
    if found:
        return Path(found)
    candidate = sdk_path() / "bin" / "PlaydateSimulator"
    if candidate.exists():
        return candidate
    raise FileNotFoundError(
        "PlaydateSimulator not found — set PLAYDATE_SDK_PATH, or put its bin/ on PATH"
    )


@dataclass
class RunResult:
    """What a headless run saw. `failed` is judged from the CONSOLE, never from the
    exit code: the Simulator pauses a crashed game instead of exiting, and it also
    segfaults inside `playdate.simulator.exit()` on a perfectly good run. The exit
    status is noise here; the traceback is the truth."""

    console: str
    failed: bool
    booted: bool
    artifacts: list[Path] = field(default_factory=list)

    @property
    def traceback(self) -> str:
        """Just the failure, for an error message that fits on a screen."""
        lines = self.console.splitlines()
        for i, line in enumerate(lines):
            if FAILURE.search(line):
                return "\n".join(lines[i:i + 12])
        return ""


class Simulator:
    """A running Simulator process, with its console drained in the background.

    Draining matters: the console is a pipe, and a pipe that nobody reads fills and
    blocks the writer. A Simulator blocked on stdout stops rendering, which looks like
    a hung game.
    """

    def __init__(self, pdx: Path, display: str, env: dict[str, str]) -> None:
        self.pdx = Path(pdx)
        self.display = display
        self._env = env
        self._proc: subprocess.Popen | None = None
        self._pty_main: int = -1
        self._lines: queue.Queue[str] = queue.Queue()
        self._seen: list[str] = []
        self._reader: threading.Thread | None = None

    def start(self) -> None:
        if not self.pdx.exists():
            raise FileNotFoundError(f"no such .pdx: {self.pdx}")

        # A PTY, not a pipe. The Simulator uses stdio's default buffering, which is
        # FULLY buffered when stdout is not a terminal — so through a plain pipe it
        # emits nothing at all until it exits, and it does not exit. Every wait_for()
        # times out against a game that is printing perfectly well. A pty makes it
        # line-buffer, and unlike `stdbuf` it needs no coreutils on the host.
        self._pty_main, secondary = pty.openpty()
        self._proc = subprocess.Popen(
            [str(simulator_binary()), str(self.pdx)],
            env=self._env, stdout=secondary, stderr=secondary, close_fds=True,
        )
        os.close(secondary)
        self._reader = threading.Thread(target=self._drain, daemon=True)
        self._reader.start()

    def _drain(self) -> None:
        buffer = b""
        while True:
            try:
                chunk = os.read(self._pty_main, 4096)
            except OSError:
                break                       # the child exited and closed the pty
            if not chunk:
                break
            buffer += chunk
            *lines, buffer = buffer.split(b"\n")
            for line in lines:
                self._lines.put(line.decode("utf-8", "replace").rstrip("\r"))
        if buffer:
            self._lines.put(buffer.decode("utf-8", "replace").rstrip("\r"))

    def _pump(self) -> None:
        while True:
            try:
                self._seen.append(self._lines.get_nowait())
            except queue.Empty:
                return

    @property
    def console(self) -> str:
        """Everything the game said, with the Simulator's own chatter removed."""
        self._pump()
        return "\n".join(ln for ln in self._seen if not NOISE.search(ln))

    @property
    def raw_console(self) -> str:
        self._pump()
        return "\n".join(self._seen)

    def wait_for(self, pattern: str, timeout: float = 20.0) -> bool:
        """Block until the console matches, or a Lua error makes waiting pointless.

        Returning early on failure is the difference between a 2-second red build and
        a 20-second one: a crashed game will never print what you are waiting for.
        """
        rx = re.compile(pattern)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            text = self.console
            if rx.search(text):
                return True
            if FAILURE.search(text):
                return False
            if self._proc and self._proc.poll() is not None:
                return bool(rx.search(self.console))
            time.sleep(0.1)
        return False

    def stop(self) -> None:
        """Terminate. Note there is no clean in-game exit worth using: calling
        `playdate.simulator.exit()` segfaults the Linux Simulator (after flushing, so
        artifacts survive), which would make an exit code meaningless even if we
        wanted one."""
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._proc.kill()
                self._proc.wait(timeout=5)
        if self._pty_main >= 0:
            try:
                os.close(self._pty_main)    # unblocks the reader's os.read
            except OSError:
                pass
            self._pty_main = -1
        if self._reader:
            self._reader.join(timeout=2)
        self._pump()

    def result(self, artifacts: list[Path] | None = None) -> RunResult:
        console = self.console
        return RunResult(
            console=console,
            failed=bool(FAILURE.search(console)),
            booted="Loading:" in self.raw_console,
            artifacts=artifacts or [],
        )
