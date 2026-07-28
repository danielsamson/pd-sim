"""pd-sim on the command line.

    pd-sim run game.pdx                       # does it boot and stay up?
    pd-sim run game.pdx --seconds 20 --press a,right,a
    pd-sim run game.pdx --await shot.png      # wait for a game-written screenshot
    pd-sim keys                               # the button -> keystroke map

`run` exits non-zero on a Lua error, so it is a test as it stands — which is the point.
A `pdc` build proves a file parses. This proves it runs.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .display import DisplayError
from .keys import KEYMAP, InputError
from .session import Session


def _run(args: argparse.Namespace) -> int:
    buttons = [b.strip() for b in args.press.split(",") if b.strip()] if args.press else []

    with Session(args.pdx, interactive=bool(buttons)) as sim:
        if args.wait:
            if not sim.wait_for(args.wait, timeout=args.seconds):
                print(f"pd-sim: never saw {args.wait!r}", file=sys.stderr)
                print(sim.console, file=sys.stderr)
                return 1
        for button in buttons:
            sim.press(button)

        for command in (args.send or []):
            sim.send(command, cmd_file=args.cmd_file)

        if args.shot:
            sim.run_for(1)
            print(f"wrote {sim.screenshot(args.shot)}", file=sys.stderr)

        if args.await_file:
            try:
                written = sim.await_file(args.await_file, timeout=args.seconds)
                print(f"wrote {written}", file=sys.stderr)
            except TimeoutError as e:
                print(f"pd-sim: {e}", file=sys.stderr)
                return 1
        elif not args.wait:
            sim.run_for(args.seconds)

        result = sim.finish()

    if args.console:
        print(result.console)

    if not result.booted:
        print("pd-sim: the Simulator never loaded the .pdx", file=sys.stderr)
        return 1
    if result.failed:
        print("pd-sim: the game raised a Lua error\n", file=sys.stderr)
        print(result.traceback, file=sys.stderr)
        return 1
    return 0


def _keys(_: argparse.Namespace) -> int:
    print("Playdate button -> Simulator keystroke (measured, SDK 3.1.1):\n")
    for button, key in KEYMAP.items():
        print(f"  {button:<6} {key}")
    print("\nA and B are on 's' and 'a' — the letters read backwards because they")
    print("mirror the device, where B sits to the LEFT of A.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="pd-sim", description="Run a Playdate game in a headless Simulator."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="run a .pdx headless; non-zero on a Lua error")
    run.add_argument("pdx", type=Path)
    run.add_argument("--seconds", type=float, default=10.0,
                     help="how long to run, or how long to wait (default: 10)")
    run.add_argument("--press", help="buttons to press, comma-separated (a,b,up,down,left,right)")
    run.add_argument("--wait", metavar="REGEX",
                     help="finish as soon as the console matches this")
    run.add_argument("--await", dest="await_file", metavar="PATH",
                     help="wait for a file the game writes (e.g. a screenshot)")
    run.add_argument("--send", action="append", metavar="COMMAND",
                     help="a command line for the game to poll (repeatable). Carries "
                          "VALUES, unlike --press; needs the game to poll its command file")
    run.add_argument("--cmd-file", default="mcp_cmd.txt",
                     help="the file the game polls (bridge.lua default: mcp_cmd.txt)")
    run.add_argument("--shot", metavar="PATH",
                     help="capture the Simulator window (any .pdx, no cooperation; "
                          "includes chrome). For the exact framebuffer use --await")
    run.add_argument("--console", action="store_true", help="print the game's output")
    run.set_defaults(func=_run)

    sub.add_parser("keys", help="print the button -> keystroke map").set_defaults(func=_keys)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (DisplayError, InputError, FileNotFoundError) as e:
        print(f"pd-sim: {type(e).__name__}: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
