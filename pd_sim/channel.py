"""The command file: sending VALUES to a running game.

Keystrokes press buttons. They cannot say `set 2.cut 0.4`. For anything with a value
in it, the Simulator has a far better channel than input injection — the game's Data
directory, which the host can write to and the game can read while it runs:

    SDK/Disk/Data/<bundleID>/<cmdfile>

The host appends a line; the game polls the file each frame and executes it. That is
the convention `bridge.lua` standardizes, and it is what `pd-link serve` uses to
drive a Simulator.

Prefer it over `press()` wherever both would work. It is deterministic — no focus, no
window, no timing, no key map — and it carries arguments, so one call sets a parameter
to an exact value instead of pressing a button eleven times and hoping.

It is the same trade as pd-link's own two tiers: keystrokes work on ANY .pdx with no
cooperation; this needs the game to poll, and in exchange it can say anything.
"""

from __future__ import annotations

import re
from pathlib import Path

# bridge.lua's default. A game may choose another (GrainShift polls "gs_cmd.txt"), so
# this is a default, never an assumption.
DEFAULT_CMD_FILE = "bridge_cmd.txt"
DEFAULT_OUT_FILE = "bridge_out.txt"

# The same channel under the name it was born with. It was called mcp_* because the
# convention was invented inside playdate-mcp, before bridge.lua was extracted into
# pd-link and this and pd-link's sim ops adopted it -- so a control path every
# Simulator-hosted game uses looked like an integration most projects never run. A game
# pinned to an older bridge.lua still polls the old name, so resolve_channel picks the
# pair per game instead of assuming one.
LEGACY_CMD_FILE = "mcp_cmd.txt"
LEGACY_OUT_FILE = "mcp_out.txt"

# bridge.lua opens its reply file with this, naming the command file it polls, so a
# host can read the answer rather than guess it.
_READY = re.compile(r"^\[rc\]\s+bridge ready cmd=(\S+)\s*$", re.MULTILINE)

_BUNDLE_ID = re.compile(r"^bundleID\s*=\s*(\S+)\s*$", re.MULTILINE | re.IGNORECASE)


class ChannelError(RuntimeError):
    """The game's Data directory could not be located."""


def bundle_id(pdx: Path) -> str:
    """Read the bundle ID out of a built .pdx.

    It names the Data directory, so it has to come from the bundle rather than be
    passed in and guessed at — a wrong id writes a command file nobody reads, and
    nothing anywhere reports an error.
    """
    info = Path(pdx) / "pdxinfo"
    if not info.exists():
        raise ChannelError(f"{pdx} has no pdxinfo — is it a built .pdx?")
    match = _BUNDLE_ID.search(info.read_text(errors="replace"))
    if not match:
        raise ChannelError(f"{info} declares no bundleID")
    return match.group(1)


def data_dir(pdx: Path, sdk: Path) -> Path:
    """Where the Simulator keeps this game's persistent files — both directions: the
    host's command file goes in, and anything the game saves comes out here."""
    return Path(sdk) / "Disk" / "Data" / bundle_id(pdx)


def resolve_channel(pdx: Path, sdk: Path) -> tuple[str, str, str]:
    """Which (command, reply) filenames this game actually uses, and how we know.

    Preference order is evidence, not guesswork: a reply file that NAMES its command
    file wins; then a reply file that merely exists, which pairs by convention; then
    the new default, for a game that has written nothing yet.
    """
    home = data_dir(pdx, sdk)
    for cmd, out in ((DEFAULT_CMD_FILE, DEFAULT_OUT_FILE), (LEGACY_CMD_FILE, LEGACY_OUT_FILE)):
        path = home / out
        if path.exists() and path.stat().st_size:
            match = _READY.search(path.read_text(errors="replace"))
            return (match.group(1) if match else cmd, out,
                    "announced" if match else f"{out} exists")
    return DEFAULT_CMD_FILE, DEFAULT_OUT_FILE, "default (game has written nothing yet)"


def send(command: str, pdx: Path, sdk: Path, cmd_file: str | None = None) -> Path:
    """Append one command line for the running game to pick up.

    Appends rather than overwrites: the game consumes the file at its own polling rate
    (bridge.lua defaults to every 15 frames), so a second command written a moment
    later must queue behind the first rather than erase it unread.
    """
    if cmd_file is None:
        cmd_file, _, _ = resolve_channel(pdx, sdk)
    target = data_dir(pdx, sdk)
    target.mkdir(parents=True, exist_ok=True)
    path = target / cmd_file
    with open(path, "a") as f:
        f.write(command.rstrip("\n") + "\n")
    return path
