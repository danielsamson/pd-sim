"""Which command file a game is actually driven through.

The channel was renamed: mcp_cmd.txt -> bridge_cmd.txt. The name came from
playdate-mcp, where the convention was invented, and stayed after bridge.lua was
extracted into pd-link and this and pd-link's sim ops adopted it — so the one control
path every Simulator-hosted game uses read as an integration most projects never run.

Renaming a filename both ends agree on is the kind of change that breaks everything
silently, so resolution is evidence-driven: the game's reply file names the command
file it polls, and failing that its mere existence pairs it by convention. These
tests pin that, because getting it wrong is a command nobody reads and a timeout
against a perfectly healthy game.
"""

from pathlib import Path

import pytest

from pd_sim.channel import (
    DEFAULT_CMD_FILE,
    DEFAULT_OUT_FILE,
    LEGACY_CMD_FILE,
    LEGACY_OUT_FILE,
    resolve_channel,
    send,
)


@pytest.fixture
def game(tmp_path):
    """A built .pdx plus the SDK layout its Data dir lives under."""
    pdx = tmp_path / "Game.pdx"
    pdx.mkdir()
    (pdx / "pdxinfo").write_text("name=Test\nbundleID=com.test.game\n")
    sdk = tmp_path / "sdk"
    data = sdk / "Disk" / "Data" / "com.test.game"
    data.mkdir(parents=True)
    return pdx, sdk, data


def test_a_game_that_has_written_nothing_gets_the_new_default(game):
    pdx, sdk, _ = game
    assert resolve_channel(pdx, sdk)[:2] == (DEFAULT_CMD_FILE, DEFAULT_OUT_FILE)


def test_the_announcement_wins_over_every_convention(game):
    pdx, sdk, data = game
    (data / DEFAULT_OUT_FILE).write_text("[rc] bridge ready cmd=gs_cmd.txt\n")
    cmd, out, how = resolve_channel(pdx, sdk)
    assert (cmd, out, how) == ("gs_cmd.txt", DEFAULT_OUT_FILE, "announced")


def test_a_legacy_reply_file_means_a_legacy_command_file(game):
    """An older pinned bridge.lua: writes mcp_out.txt, polls only mcp_cmd.txt."""
    pdx, sdk, data = game
    (data / LEGACY_OUT_FILE).write_text("[rc] pong\n")
    assert resolve_channel(pdx, sdk)[:2] == (LEGACY_CMD_FILE, LEGACY_OUT_FILE)


def test_the_new_reply_file_wins_when_both_exist(game):
    """A game that updated mid-session leaves the old file behind. Newer is truer."""
    pdx, sdk, data = game
    (data / LEGACY_OUT_FILE).write_text("[rc] pong\n")
    (data / DEFAULT_OUT_FILE).write_text("[rc] bridge ready cmd=bridge_cmd.txt\n")
    assert resolve_channel(pdx, sdk)[:2] == (DEFAULT_CMD_FILE, DEFAULT_OUT_FILE)


def test_an_empty_reply_file_is_not_evidence(game):
    """bridge.lua truncates its reply file at startup, so zero bytes says nothing."""
    pdx, sdk, data = game
    (data / LEGACY_OUT_FILE).write_text("")
    assert resolve_channel(pdx, sdk)[:2] == (DEFAULT_CMD_FILE, DEFAULT_OUT_FILE)


def test_send_writes_to_the_resolved_file(game):
    pdx, sdk, data = game
    (data / LEGACY_OUT_FILE).write_text("[rc] pong\n")
    path = send("set 2.cut 0.4", pdx, sdk)
    assert path == data / LEGACY_CMD_FILE
    assert path.read_text() == "set 2.cut 0.4\n"


def test_an_explicit_name_overrides_resolution(game):
    pdx, sdk, data = game
    path = send("ping", pdx, sdk, cmd_file="gs_cmd.txt")
    assert path == data / "gs_cmd.txt"


def test_send_still_appends_rather_than_overwrites(game):
    """The game consumes at its own polling rate; a second command must queue behind
    the first, not erase it unread."""
    pdx, sdk, data = game
    send("first", pdx, sdk)
    send("second", pdx, sdk)
    assert (data / DEFAULT_CMD_FILE).read_text() == "first\nsecond\n"
