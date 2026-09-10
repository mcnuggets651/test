from __future__ import annotations

import datetime as dt
import json
import unittest

import onside_fpl
import onside_live


def bootstrap() -> bytes:
    return json.dumps(
        {
            "events": [{"id": 4, "is_next": True, "deadline_time": "2026-09-12T11:30:00Z"}],
            "teams": [{"id": 1, "short_name": "LIV"}],
            "element_types": [{"id": 3, "singular_name_short": "MID"}],
            "elements": [
                {
                    "id": 367,
                    "web_name": "Gakpo",
                    "first_name": "Cody",
                    "second_name": "Gakpo",
                    "team": 1,
                    "element_type": 3,
                    "now_cost": 72,
                    "status": "d",
                    "chance_of_playing_next_round": 75,
                    "news": "Thigh injury - 75% chance of playing",
                }
            ],
        }
    ).encode()


def player_page(*, element: int = 367, gw: int = 4) -> bytes:
    return f"""
    <html><body>
      <h1>Cody Gakpo</h1>
      <p>Midfielder · FPL ID {element}</p>
      <p>Next fixture GW{gw}: vs FUL — projected 7.0 xP.</p>
      <p>Across the next 6 fixtures, Onside projects 41.0 total xP.</p>
      <p>Onside gives Gakpo a 70% chance of starting Gameweek {gw}.</p>
      <p>ENGINE V2 · GW{gw} · AS OF 10 SEPT · ONSIDEARENA.COM</p>
    </body></html>
    """.encode()


class OnsideLiveTests(unittest.TestCase):
    def test_slug_and_url_are_deterministic(self) -> None:
        self.assertEqual(onside_live.player_slug("B.Fernandes"), "b-fernandes")
        self.assertEqual(onside_live.player_slug("João Pedro"), "joao-pedro")
        self.assertEqual(onside_live.player_url(367, "Gakpo"), "https://onsidearena.com/player/gakpo-367")

    def test_parse_live_player_page(self) -> None:
        result = onside_live.parse_player_page(player_page(), element=367, web_name="Gakpo", target_gameweek=4)
        self.assertEqual(result["element"], 367)
        self.assertEqual(result["onside_xp"], 7.0)
        self.assertEqual(result["onside_next_6_gw_xp"], 41.0)
        self.assertEqual(result["onside_start_probability_pct"], 70.0)
        self.assertEqual(result["onside_engine"], "v2")
        self.assertEqual(result["onside_as_of"].upper(), "10 SEPT")

    def test_identity_mismatch_fails_closed(self) -> None:
        with self.assertRaisesRegex(onside_fpl.ProviderError, "identity mismatch"):
            onside_live.parse_player_page(player_page(element=999), element=367, web_name="Gakpo", target_gameweek=4)

    def test_wrong_gameweek_fails_closed(self) -> None:
        with self.assertRaisesRegex(onside_fpl.ProviderError, "target GW4"):
            onside_live.parse_player_page(player_page(gw=5), element=367, web_name="Gakpo", target_gameweek=4)

    def test_live_bundle_keeps_official_status_separate(self) -> None:
        result = onside_live.build_live_bundle(
            bootstrap_bytes=bootstrap(),
            elements=[367],
            page_fetcher=lambda _url: player_page(),
            fetched_at=dt.datetime(2026, 9, 10, 15, 0, tzinfo=dt.timezone.utc),
        )
        self.assertEqual(result["schema"], "onside-fpl-live-player-evidence-v1")
        self.assertEqual(result["players"][0]["onside_xp"], 7.0)
        self.assertEqual(result["players"][0]["official_status"], "d")
        self.assertEqual(result["players"][0]["official_chance_next_round"], 75)
        self.assertFalse(result["provenance"]["mcp"]["fpl_tool_exposed_at_pinned_commit"])


if __name__ == "__main__":
    unittest.main()
