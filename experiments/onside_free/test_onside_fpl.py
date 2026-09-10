from __future__ import annotations

import datetime as dt
import json
import unittest

import onside_fpl


def bootstrap(*, gw: int = 4, deadline: str = "2026-09-12T11:30:00Z") -> bytes:
    payload = {
        "events": [
            {"id": 3, "is_next": False, "deadline_time": "2026-09-05T11:30:00Z"},
            {"id": gw, "is_next": True, "deadline_time": deadline},
        ],
        "teams": [
            {"id": 1, "short_name": "LIV"},
            {"id": 2, "short_name": "MCI"},
            {"id": 3, "short_name": "TOT"},
        ],
        "element_types": [
            {"id": 1, "singular_name_short": "GKP"},
            {"id": 2, "singular_name_short": "DEF"},
            {"id": 3, "singular_name_short": "MID"},
            {"id": 4, "singular_name_short": "FWD"},
        ],
        "elements": [
            {
                "id": 10,
                "web_name": "Isak",
                "first_name": "Alexander",
                "second_name": "Isak",
                "team": 1,
                "element_type": 4,
                "now_cost": 91,
                "status": "a",
                "chance_of_playing_next_round": 100,
                "news": "",
            },
            {
                "id": 20,
                "web_name": "Haaland",
                "first_name": "Erling",
                "second_name": "Haaland",
                "team": 2,
                "element_type": 4,
                "now_cost": 155,
                "status": "a",
                "chance_of_playing_next_round": 100,
                "news": "",
            },
            {
                "id": 30,
                "web_name": "Departed",
                "first_name": "Old",
                "second_name": "Departed",
                "team": 3,
                "element_type": 3,
                "now_cost": 50,
                "status": "u",
                "chance_of_playing_next_round": 0,
                "news": "Has joined Lille on loan for the rest of the season",
            },
        ],
    }
    return json.dumps(payload).encode()


def page(*, gw: int = 4, frozen: str = "4 Sept, 22:19 UTC", duplicate: bool = False) -> bytes:
    extra = "<tr><td>Isak</td><td>LIV</td><td>FWD</td><td>£9.1</td><td>21.0%</td><td>6.9</td></tr>" if duplicate else ""
    return f"""
    <html><body>
      <p>Gameweek {gw} · engine v5</p>
      <p>Our expected points. Frozen {frozen} before the deadline.</p>
      <table>
        <thead><tr><th>Player</th><th>Club</th><th>Pos</th><th>Price</th><th>Owned</th><th>Onside xP ↓</th></tr></thead>
        <tbody>
          <tr><td>Isak</td><td>LIV</td><td>FWD</td><td>£9.1</td><td>21.0%</td><td>7.1</td></tr>
          <tr><td>Haaland</td><td>MCI</td><td>FWD</td><td>£15.5</td><td>71.3%</td><td>3.6</td></tr>
          <tr><td>Departed</td><td>TOT</td><td>MID</td><td>£5.0</td><td>0.1%</td><td>2.0</td></tr>
          {extra}
        </tbody>
      </table>
    </body></html>
    """.encode()


NOW = dt.datetime(2026, 9, 10, 15, 0, tzinfo=dt.timezone.utc)


class OnsideProviderTests(unittest.TestCase):
    def test_build_bundle_matches_current_assets_and_excludes_departure(self) -> None:
        result = onside_fpl.build_bundle(
            page_bytes=page(),
            bootstrap_bytes=bootstrap(),
            fetched_at=NOW,
            min_source_rows=3,
            min_match_ratio=0.60,
        )
        self.assertEqual(result["schema"], "onside-fpl-evidence-v1")
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["target_gameweek"], 4)
        self.assertEqual(result["onside_engine_version"], "v5")
        self.assertEqual([p["element"] for p in result["players"]], [10, 20])
        self.assertAlmostEqual(result["players"][0]["onside_xp"], 7.1)
        self.assertEqual(result["excluded_source_rows"][0]["reason"], "official_news_indicates_departure")
        self.assertFalse(result["provenance"]["upstream_mcp"]["fpl_tool_exposed_at_pinned_commit"])
        self.assertEqual(result["provenance"]["upstream_mcp"]["version"], "0.2.0")

    def test_parser_accepts_realistic_header(self) -> None:
        rows, _visible, gw, metadata = onside_fpl.parse_projection_page(page().decode())
        self.assertEqual(gw, 4)
        self.assertTrue(metadata.startswith("v5|"))
        self.assertEqual(rows[0].name, "Isak")
        self.assertEqual(rows[0].team, "LIV")
        self.assertEqual(rows[0].position, "FWD")
        self.assertEqual(rows[0].ownership, 21.0)

    def test_wrong_gameweek_fails_closed(self) -> None:
        with self.assertRaisesRegex(onside_fpl.ProviderError, "does not match"):
            onside_fpl.build_bundle(
                page_bytes=page(gw=5),
                bootstrap_bytes=bootstrap(gw=4),
                fetched_at=NOW,
                min_source_rows=1,
                min_match_ratio=0.0,
            )

    def test_post_deadline_capture_fails_closed(self) -> None:
        with self.assertRaisesRegex(onside_fpl.ProviderError, "not pre-deadline"):
            onside_fpl.build_bundle(
                page_bytes=page(frozen="12 Sept, 12:00 UTC"),
                bootstrap_bytes=bootstrap(deadline="2026-09-12T11:30:00Z"),
                fetched_at=dt.datetime(2026, 9, 12, 12, 5, tzinfo=dt.timezone.utc),
                min_source_rows=1,
                min_match_ratio=0.0,
            )

    def test_stale_capture_fails_closed(self) -> None:
        with self.assertRaisesRegex(onside_fpl.ProviderError, "stale"):
            onside_fpl.build_bundle(
                page_bytes=page(),
                bootstrap_bytes=bootstrap(),
                fetched_at=NOW,
                max_capture_age_hours=24,
                min_source_rows=1,
                min_match_ratio=0.0,
            )

    def test_conflicting_duplicate_fails_closed(self) -> None:
        with self.assertRaisesRegex(onside_fpl.ProviderError, "conflicting duplicate"):
            onside_fpl.build_bundle(
                page_bytes=page(duplicate=True),
                bootstrap_bytes=bootstrap(),
                fetched_at=NOW,
                min_source_rows=1,
                min_match_ratio=0.0,
            )

    def test_low_identity_coverage_fails_closed(self) -> None:
        with self.assertRaisesRegex(onside_fpl.ProviderError, "match ratio too low"):
            onside_fpl.build_bundle(
                page_bytes=page(),
                bootstrap_bytes=bootstrap(),
                fetched_at=NOW,
                min_source_rows=1,
                min_match_ratio=0.90,
            )

    def test_tiny_table_fails_closed(self) -> None:
        with self.assertRaisesRegex(onside_fpl.ProviderError, "too small"):
            onside_fpl.build_bundle(
                page_bytes=page(),
                bootstrap_bytes=bootstrap(),
                fetched_at=NOW,
                min_source_rows=4,
                min_match_ratio=0.0,
            )


if __name__ == "__main__":
    unittest.main()
