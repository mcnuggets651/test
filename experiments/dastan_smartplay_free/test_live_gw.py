from __future__ import annotations

import importlib.util
from pathlib import Path

MODULE_PATH = Path(__file__).with_name("live_gw.py")
SPEC = importlib.util.spec_from_file_location("live_gw", MODULE_PATH)
assert SPEC and SPEC.loader
live_gw = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(live_gw)


def test_season_label_2026_27():
    assert live_gw.season_label([{"deadline_time": "2026-08-14T17:30:00Z"}]) == "2026-27"


def test_synthetic_target_row_is_zero_target_not_fake_history():
    player = {
        "id": 9,
        "team": 1,
        "element_type": 4,
        "web_name": "Example",
        "now_cost": 75,
        "transfers_in_event": 20,
        "transfers_out_event": 5,
        "selected_by_percent": "10.0",
    }
    fixture = {
        "id": 100,
        "event": 4,
        "team_h": 1,
        "team_a": 2,
        "kickoff_time": "2026-09-12T14:00:00Z",
    }
    row = live_gw.synthetic_target_row(
        player,
        fixture,
        {1: "Home", 2: "Away"},
        {"total_players": 1_000_000},
        None,
    )
    assert row["GW"] == 4
    assert row["team"] == "Home"
    assert row["opponent_team"] == 2
    assert row["minutes"] == 0
    assert row["total_points"] == 0
    assert row["value"] == 75.0
    assert row["selected"] == 100_000.0
    assert row["transfers_balance"] == 15.0


def test_live_context_matches_dastan_public_signal_contract():
    bootstrap = {
        "elements": [
            {
                "code": 123,
                "ep_next": "5.5",
                "status": "d",
                "chance_of_playing_next_round": 75,
                "news": "Knock",
            }
        ]
    }
    context = live_gw.live_context_by_code(bootstrap)[123]
    assert context == {
        "ar_ep_next": 5.5,
        "sig_status_risk": 1.0,
        "sig_chance_playing": 75.0,
        "sig_has_news": 1.0,
    }


def test_normalize_name_handles_accents():
    assert live_gw.normalize_name("João Pedro") == "joaopedro"
