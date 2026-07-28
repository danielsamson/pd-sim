"""The command-file wire convention, pinned against a shared fixture.

The command file + `#N`/`[rc#N]` tagging is one convention spoken by three
implementations: bridge.lua (the game-side producer) and its two host drivers,
pd-sim and pd-link. `bridge_wire.json` is that convention as data, byte-identical to
the copy in pd-link — a diff between the two is exactly the drift it exists to catch.

pd-sim writes commands *untagged* (it reads replies off the Simulator console, not a
reply file — see docs/CONSOLE.md); the `#N`/`[rc#N]` half is pd-link's macOS read-back.
So pd-sim conforms to the untagged command encoding and the Data-dir layout — the
parts it actually speaks — and this asserts channel.py matches them.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pd_sim import channel

WIRE = json.loads((Path(__file__).parent / "bridge_wire.json").read_text())


def _pdx(tmp_path: Path, bundle: str = "com.test.game") -> Path:
    pdx = tmp_path / "game.pdx"
    pdx.mkdir()
    (pdx / "pdxinfo").write_text(f"name=Test\nbundleID={bundle}\n")
    return pdx


def test_default_cmd_file_matches_the_convention():
    assert channel.DEFAULT_CMD_FILE == WIRE["default_cmd_file"]


def test_data_dir_formula(tmp_path):
    pdx = _pdx(tmp_path)
    expected = Path(WIRE["data_dir"].format(sdk=tmp_path, bundle_id="com.test.game"))
    assert channel.data_dir(pdx, tmp_path) == expected


@pytest.mark.parametrize(
    "case", [c for c in WIRE["commands"] if c["tag"] is None], ids=lambda c: c["wire"]
)
def test_untagged_command_encoding(tmp_path, case):
    pdx = _pdx(tmp_path)
    path = channel.send(case["command"], pdx, tmp_path)
    assert path.read_text() == case["wire"] + "\n"
