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
    r"http_transport|libGL|dbus|dbind|AT-SPI|org\.a11y|Soapbox|SDL2|GTK|gtk"
)

# A Lua error surfaces as these. "simulator paused" is the decisive one: the Simulator
# stops the game rather than exiting, so a run that failed still looks alive.
FAILURE = re.compile(r"Update error:|stack traceback:|Update failed, simulator paused")


def settings_file() -> Path:
    """The Simulator's own preferences file (Linux)."""
    config = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(config) / "Playdate Simulator" / "Playdate Simulator.ini"


def suppress_first_run_dialogs() -> Path:
    """Write the two settings that stop the Simulator holding back its console.

    On a machine that has never run the Simulator there is no preferences file, and
    the first launch wants to show its newsletter sign-up and a performance warning.
    While either is pending the Simulator emits almost NOTHING on stdout: no `print()`
    from the game, not even its own `SDK:` / `Release:` / `CMD:` header. Errors still
    come through — they are wired up unconditionally — so a crashed game reports its
    traceback while a healthy one appears silent.

    That combination is genuinely confusing. The game runs: it draws, it responds to
    input, `writeToFile` produces a screenshot. It simply cannot be heard. Every
    wait_for() times out against a game working perfectly, and the one test that
    asserts on a FILE rather than the console passes, which makes it look like the
    console is broken rather than gated.

    This is the same first-run dialog GrainShift's tools/shoot.sh dismisses with a
    hardcoded `xdotool mousemove 663 490 click 1`. Writing the setting is better than
    clicking at a coordinate: no window has to exist, nothing depends on the theme or
    the window size, and it works before the Simulator has drawn anything.

    Existing settings are preserved — only these two keys are set.
    """
    path = settings_file()
    wanted = {"ShowElist": "0", "ShowPerfWarning": "0"}

    lines = path.read_text().splitlines() if path.exists() else []
    seen = set()
    out = []
    for line in lines:
        key = line.split("=", 1)[0].strip()
        if key in wanted:
            out.append(f"{key}={wanted[key]}")
            seen.add(key)
        else:
            out.append(line)

    # New keys go at the TOP: the file has [LastUsed] and other sections, and a bare
    # key appended after a section header belongs to that section, not the root.
    missing = [f"{k}={v}" for k, v in wanted.items() if k not in seen]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(missing + out) + "\n")
    return path


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

        # Before anything launches: a first-run Simulator withholds its console until
        # its sign-up dialog is dealt with, and a silent game is indistinguishable from
        # a broken one. See suppress_first_run_dialogs.
        suppress_first_run_dialogs()

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

    def diagnosis(self) -> str:
        """Why nothing is happening, when nothing is happening.

        A Simulator that cannot start — a missing shared library is the usual reason,
        libwebkit2gtk being the one that catches people — produces no output, maps no
        window, and does not exit. Every wait then fails with an empty console and no
        hint, which reads like "headless does not work" rather than "install a package".
        """
        raw = self.raw_console.strip()
        if raw:
            return raw[-1500:]
        alive = self._proc is not None and self._proc.poll() is None
        state = "still running" if alive else f"exited ({self._proc.returncode if self._proc else '?'})"
        return (
            f"the Simulator printed NOTHING and is {state}.\n"
            "Usually a missing shared library — it links GTK/WebKit, and without them "
            "it neither prints nor exits. Check with:\n"
            f"    ldd {simulator_binary()} | grep 'not found'\n"
            "On Debian/Ubuntu the one most often missing is libwebkit2gtk-4.1-0."
        )

    def wait_for(self, pattern: str, timeout: float = 60.0) -> bool:
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

    def wait_for_failure(self, timeout: float = 60.0) -> bool:
        """Block until the game raises a Lua error, or the timeout expires.

        Testing an error path needs this rather than `wait_for`: the two overlap
        awkwardly, since `wait_for` treats a failure as a reason to give up early. It
        is also the only honest way to wait for a crash — sleeping a fixed number of
        seconds and then checking assumes you know how long startup takes, and startup
        is ~10x slower on a CI runner than on a developer machine.
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if FAILURE.search(self.console):
                return True
            if self._proc and self._proc.poll() is not None:
                return bool(FAILURE.search(self.console))
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
