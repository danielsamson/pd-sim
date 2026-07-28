"""pd-sim — a Playdate Simulator you can drive from a script.

The Simulator is a GTK/SDL2 desktop app, but nothing about it needs a desk: with a
virtual display it runs on a CI runner, in a container, over SSH. That turns the one
check this toolchain could never automate — does the game actually RUN — into
something a merge gate can do.

    from pd_sim import Session

    with Session("game.pdx") as sim:
        sim.wait_for("ready")
        sim.press("a", "right")
        sim.crank(90)
        result = sim.finish()
        assert not result.failed, result.traceback

What it is NOT: a substitute for hardware. It is x86 running an interpreter, not a
168MHz Cortex-M7 — timing, frame budget, audio and a native extension's ARM binary
exist only on the device. See pd-link for that half.
"""

from .channel import ChannelError, data_dir, send
from .display import DisplayError, VirtualDisplay
from .keys import KEYMAP, InputError
from .session import Session
from .simulator import RunResult, Simulator

__version__ = "0.1.0"
__all__ = ["Session", "Simulator", "RunResult", "VirtualDisplay",
           "KEYMAP", "DisplayError", "InputError", "ChannelError", "send", "data_dir"]
