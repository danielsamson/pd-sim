"""The real thing: a Simulator, a virtual display, a compiled .pdx.

Marked `sdk` and deselected by default, because it needs the Playdate SDK plus xvfb,
openbox and xdotool. CI runs it — that is the entire point of this package, so a suite
that skipped it would be testing everything except the claim.

The key map in particular is MEASURED here rather than asserted from documentation. If
a Simulator release moves a binding, this fails, instead of every downstream suite
quietly pressing nothing.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

from pd_sim import Session
from pd_sim.simulator import sdk_path

pytestmark = pytest.mark.sdk


def _pdc() -> str:
    found = shutil.which("pdc") or str(sdk_path() / "bin" / "pdc")
    if not Path(found).exists():
        pytest.skip("no pdc — set PLAYDATE_SDK_PATH")
    return found


def build(tmp_path: Path, lua: str, name: str = "t") -> Path:
    src = tmp_path / "src"
    src.mkdir(exist_ok=True)
    (src / "main.lua").write_text(textwrap.dedent(lua))
    (src / "pdxinfo").write_text(
        f"name={name}\nauthor=test\ndescription=d\n"
        f"bundleID=com.test.{name}\nversion=1.0\nbuildNumber=1\n"
    )
    out = tmp_path / f"{name}.pdx"
    subprocess.run([_pdc(), str(src), str(out)], check=True, capture_output=True, timeout=60)
    return out


def test_a_clean_game_runs_and_prints(tmp_path):
    pdx = build(tmp_path, '''
        import "CoreLibs/graphics"
        print("READY")
        function playdate.update() end
    ''')
    with Session(pdx, interactive=False) as sim:
        assert sim.wait_for("READY", timeout=30), sim.console
        result = sim.finish()
    assert result.booted
    assert not result.failed


def test_a_runtime_error_fails_the_run(tmp_path):
    """The whole justification for this package: pdc compiles this file happily, and
    it is broken. Only running it says so."""
    pdx = build(tmp_path, '''
        import "CoreLibs/graphics"
        local frame = 0
        function playdate.update()
            frame = frame + 1
            if frame == 5 then
                local t = nil
                print(t.missing)
            end
        end
    ''')
    with Session(pdx, interactive=False) as sim:
        sim.run_for(12)
        result = sim.finish()
    assert result.failed, result.console
    assert "stack traceback" in result.traceback


def test_every_button_arrives(tmp_path):
    """Measures the key map. A Simulator release that rebinds a key fails HERE."""
    pdx = build(tmp_path, '''
        import "CoreLibs/graphics"
        print("READY")
        function playdate.update()
            for _, n in ipairs({"A", "B", "Up", "Down", "Left", "Right"}) do
                if playdate.buttonJustPressed(playdate["kButton" .. n]) then
                    print("GOT " .. n)
                end
            end
        end
    ''')
    with Session(pdx) as sim:
        assert sim.wait_for("READY", timeout=30), sim.console
        sim.press("a", "b", "up", "down", "left", "right")
        sim.run_for(2)
        console = sim.console
        sim.finish()

    for expected in ("GOT A", "GOT B", "GOT Up", "GOT Down", "GOT Left", "GOT Right"):
        assert expected in console, f"{expected} never arrived:\n{console}"


def test_the_crank_turns_and_undocks(tmp_path):
    pdx = build(tmp_path, '''
        import "CoreLibs/graphics"
        print("READY")
        function playdate.crankUndocked() print("UNDOCKED") end
        function playdate.update()
            if math.abs(playdate.getCrankChange()) > 0.01 then
                print("CRANK " .. math.floor(playdate.getCrankPosition()))
            end
        end
    ''')
    with Session(pdx) as sim:
        assert sim.wait_for("READY", timeout=30), sim.console
        turned = sim.crank(40)
        sim.run_for(2)
        console = sim.console
        sim.finish()

    assert turned == pytest.approx(40, abs=4)
    assert "UNDOCKED" in console, f"the first turn must undock:\n{console}"
    assert "CRANK " in console, console


def test_a_screenshot_comes_back(tmp_path):
    shot = tmp_path / "shot.png"
    pdx = build(tmp_path, f'''
        import "CoreLibs/graphics"
        local gfx = playdate.graphics
        local frame = 0
        function playdate.update()
            frame = frame + 1
            gfx.clear(gfx.kColorWhite)
            gfx.fillCircleAtPoint(200, 120, 40)
            if frame == 30 then
                playdate.simulator.writeToFile(gfx.getDisplayImage(), "{shot}")
            end
        end
    ''')
    with Session(pdx, interactive=False) as sim:
        written = sim.await_file(shot, timeout=30)
        sim.finish()

    assert written.exists()
    header = written.read_bytes()[:8]
    assert header == b"\x89PNG\r\n\x1a\n", "not a PNG"


def test_the_window_found_is_the_real_one(tmp_path):
    """The Simulator maps a 10x10 decoy alongside the real window, and the decoy maps
    FIRST. Picking it breaks every mouse coordinate — silently, since a click outside
    the window is not an error."""
    from pd_sim.keys import MIN_WINDOW_AREA, _geometry

    pdx = build(tmp_path, '''
        import "CoreLibs/graphics"
        function playdate.update() end
    ''')
    with Session(pdx) as sim:
        _, _, width, height = _geometry(sim._window, sim._display.name)
        sim.finish()

    assert width * height >= MIN_WINDOW_AREA, f"picked the decoy: {width}x{height}"


@pytest.mark.skipif(os.environ.get("CI") == "true", reason="timing-sensitive")
def test_console_streams_while_running(tmp_path):
    """Output must arrive DURING the run, not at exit.

    The Simulator's stdio is fully buffered when stdout is not a terminal, so through
    an ordinary pipe nothing appears until it exits — and it never exits. Every
    wait_for() would time out against a game printing perfectly well. The pty is what
    makes this pass.
    """
    pdx = build(tmp_path, '''
        import "CoreLibs/graphics"
        local frame = 0
        function playdate.update()
            frame = frame + 1
            if frame % 30 == 0 then print("TICK " .. frame) end
        end
    ''')
    with Session(pdx, interactive=False) as sim:
        assert sim.wait_for("TICK 30", timeout=20), "nothing streamed: " + sim.console
        sim.finish()
