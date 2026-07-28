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
        assert sim.wait_for("READY", timeout=90), sim.console
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
        # POLL, do not sleep. A fixed sleep bakes in an assumption about how long
        # startup takes, and a CI runner needs ~26s just to load the .pdx — so a
        # 20-second sleep passed here for weeks and failed there, reporting an empty
        # console and booted=False, which reads like the Simulator being broken rather
        # than the test finishing before it started.
        assert sim.wait_for_failure(timeout=90), (
            f"no Lua error within 90s:\n{sim._sim.diagnosis()}"
        )
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
        assert sim.wait_for("READY", timeout=90), sim.console
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
        assert sim.wait_for("READY", timeout=90), sim.console
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
        written = sim.await_file(shot, timeout=90)
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
        assert sim.wait_for("TICK 30", timeout=90), "nothing streamed: " + sim.console
        sim.finish()


def test_values_reach_a_running_game(tmp_path, monkeypatch):
    """The command channel, end to end. This is what keystrokes cannot do: a value.

    Modelled on GrainShift's `remote.sh sim "set 2.cut 0.4"`, which is where this
    convention came from — the host appends, the game polls, no focus involved.
    """
    monkeypatch.setenv("PLAYDATE_SDK_PATH", str(sdk_path()))
    pdx = build(tmp_path, '''
        import "CoreLibs/graphics"
        local cut = 0.0
        local frame = 0
        print("READY")
        local function poll()
            local f = playdate.file.open("mcp_cmd.txt", playdate.file.kFileRead)
            if not f then return end
            local line = f:readline()
            while line do
                local k, v = line:match("^set%s+(%S+)%s+(%S+)$")
                if k then cut = tonumber(v) or cut; print("SET " .. k .. "=" .. tostring(cut)) end
                line = f:readline()
            end
            f:close()
            playdate.file.delete("mcp_cmd.txt")
        end
        function playdate.update()
            frame = frame + 1
            if frame % 15 == 0 then poll() end
        end
    ''', name="chan")

    data = sdk_path() / "Disk" / "Data" / "com.test.chan"
    shutil.rmtree(data, ignore_errors=True)

    with Session(pdx, interactive=False) as sim:
        assert sim.wait_for("READY", timeout=90), sim.console
        sim.send("set 2.cut 0.4")
        assert sim.wait_for(r"SET 2\.cut=0\.4", timeout=15), sim.console
        sim.send("set 2.cut 0.9")
        assert sim.wait_for(r"SET 2\.cut=0\.9", timeout=15), sim.console
        sim.finish()


def test_the_device_can_be_tilted(tmp_path):
    """The accelerometer, driven by its dial and verified by the game's own reading.

    Asserting on readAccelerometer() rather than on what tilt() returns is the point:
    the dial is a UI widget, so the only honest confirmation is the number the game
    sees. Loose tolerances — this is a mouse drag on a dial, not an API.
    """
    pdx = build(tmp_path, '''
        import "CoreLibs/graphics"
        playdate.startAccelerometer()
        print("READY")
        function playdate.update()
            local x, y, z = playdate.readAccelerometer()
            if x then print(string.format("ACCEL x=%.3f y=%.3f", x, y)) end
        end
    ''', name="tilt")

    def latest(console):
        rows = [l for l in console.splitlines() if l.startswith("ACCEL")]
        assert rows, f"the game never read the accelerometer:\n{console}"
        pairs = dict(kv.split("=") for kv in rows[-1].replace("ACCEL ", "").split())
        return float(pairs["x"]), float(pairs["y"])

    with Session(pdx) as sim:
        assert sim.wait_for("READY", timeout=90), sim.console

        sim.tilt(0.5, 0)
        sim.run_for(1.5)
        x_right, _ = latest(sim.console)

        sim.tilt(-0.5, 0)
        sim.run_for(1.5)
        x_left, _ = latest(sim.console)

        sim.tilt(0, 0.5)
        sim.run_for(1.5)
        _, y_down = latest(sim.console)
        sim.finish()

    # Direction is what matters, and that the two ends are distinct and signed.
    assert x_right > 0.25, f"tilting right gave x={x_right}"
    assert x_left < -0.25, f"tilting left gave x={x_left}"
    assert x_right - x_left > 0.6, "the two extremes are not far enough apart"
    assert y_down > 0.25, f"tilting down gave y={y_down}"


WIDGET_PROBE = '''
    import "CoreLibs/graphics"
    print("READY")
    function playdate.deviceWillLock() print("EVENT deviceWillLock") end
    function playdate.deviceDidUnlock() print("EVENT deviceDidUnlock") end
    function playdate.crankDocked() print("EVENT crankDocked") end
    function playdate.crankUndocked() print("EVENT crankUndocked") end
    local frame = 0
    function playdate.update()
        frame = frame + 1
        if frame %% 30 == 0 then
            print(string.format("STATE crank=%%.1f docked=%%s",
                playdate.getCrankPosition(), tostring(playdate.isCrankDocked())))
        end
    end
'''


def test_the_lock_button_locks_and_unlocks(tmp_path):
    """Lock is a UI button, not a keystroke — there is no key for it."""
    pdx = build(tmp_path, WIDGET_PROBE % (), name="lock")
    with Session(pdx) as sim:
        assert sim.wait_for("READY", timeout=90), sim.console
        sim.lock()
        assert sim.wait_for("deviceWillLock", timeout=20), sim.console
        sim.lock()
        assert sim.wait_for("deviceDidUnlock", timeout=20), sim.console
        sim.finish()


def test_the_crank_can_be_docked_and_set_to_an_exact_angle(tmp_path):
    """Typing the angle beats turning it: the wheel quantises to 4-degree clicks and
    moves relative to wherever the crank already was."""
    pdx = build(tmp_path, WIDGET_PROBE % (), name="crankset")
    with Session(pdx) as sim:
        assert sim.wait_for("READY", timeout=90), sim.console
        sim.crank_dock()
        assert sim.wait_for("crankUndocked", timeout=20), sim.console

        sim.set_crank(90)
        assert sim.wait_for(r"crank=90\.0", timeout=20), sim.console
        sim.set_crank(270)
        assert sim.wait_for(r"crank=270\.0", timeout=20), sim.console
        sim.finish()


def test_the_widget_offset_is_still_right(tmp_path):
    """Pins CAPTURE_Y_OFFSET.

    Widget coordinates are read off a screenshot, but capture space is offset from
    root space, so a click computed naively lands ~22px low. A 38px accelerometer dial
    absorbs that and merely reads wrong; a 14px checkbox misses entirely and reports
    nothing at all. If a window manager theme changes the offset, this fails here
    rather than every widget silently missing.
    """
    pdx = build(tmp_path, WIDGET_PROBE % (), name="offset")
    with Session(pdx) as sim:
        assert sim.wait_for("READY", timeout=90), sim.console
        sim.crank_dock()
        assert sim.wait_for("crankUndocked", timeout=20), (
            "the Docked checkbox was not hit — CAPTURE_Y_OFFSET is probably wrong "
            f"for this window manager:\n{sim.console}"
        )
        sim.finish()


def test_it_works_on_a_machine_that_has_never_run_the_simulator(tmp_path, monkeypatch):
    """The first-run gate, which only a clean machine has.

    Point the Simulator's config at an empty directory and it behaves like a fresh
    install: it wants to show a newsletter dialog, and while that is pending it emits
    almost nothing on stdout — no game print(), not even its own SDK/Release header.
    Errors still come through, so a CRASHED game reports a traceback while a healthy
    one looks silent.

    Every developer machine passes this by accident, because the first run wrote the
    setting. Only CI is ever genuinely clean, which is exactly where it bit.
    """
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "fresh-config"))

    pdx = build(tmp_path, '''
        import "CoreLibs/graphics"
        print("READY")
        function playdate.update() end
    ''', name="firstrun")

    with Session(pdx, interactive=False) as sim:
        assert sim.wait_for("READY", timeout=90), (
            "no console on a first-run profile — suppress_first_run_dialogs is not "
            f"working:\n{sim._sim.diagnosis()}"
        )
        sim.finish()
