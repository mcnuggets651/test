from __future__ import annotations

import json
import unittest

import owner_state


def bootstrap(gw: int = 4):
    elements = []
    counts = [(1, 2), (2, 5), (3, 5), (4, 3)]
    pid = 1
    team = 1
    for element_type, count in counts:
        for _ in range(count):
            elements.append(
                {
                    "id": pid,
                    "web_name": f"P{pid}",
                    "element_type": element_type,
                    "team": team,
                    "now_cost": 50 + pid,
                    "status": "a",
                }
            )
            pid += 1
            team = (team % 5) + 1
    return {"events": [{"id": gw, "is_next": True}], "elements": elements}


def snapshot(gw: int = 4, published_gw: int | None = None):
    official = bootstrap(gw)
    positions = {1: "GK", 2: "DEF", 3: "MID", 4: "FWD"}
    rows = []
    for element in official["elements"]:
        rows.append(
            {
                "element_id": element["id"],
                "web_name": element["web_name"],
                "position": positions[element["element_type"]],
                "team_id": element["team"],
                "current_price_tenths_at_snapshot": element["now_cost"],
                "status_at_snapshot": "a",
                "can_transact_at_snapshot": True,
                "purchase_price_tenths": element["now_cost"],
                "selling_price_tenths": element["now_cost"],
            }
        )
    return {
        "run": {
            "attestation_scope": "PRIVATE_MANAGER",
            "immutable": True,
            "target_gameweek": gw,
            "run_id": "r1",
            "release_tag": "tag",
            "published_at": "2026-09-07T12:00:00Z",
            "season": "2026-2027",
        },
        "team_state": {
            "state_complete_for_transfers": True,
            "published_gw": gw if published_gw is None else published_gw,
            "entry_id": 63984,
            "bank_tenths": 7,
            "free_transfers": 2,
            "active_chip": None,
            "squad": rows,
        },
        "refresh_token": "SHOULD_NOT_LEAK",
        "nested": {"secret": "NO"},
    }


class OwnerStateTests(unittest.TestCase):
    def test_valid_exact_state_and_prices(self):
        raw = json.dumps(snapshot()).encode()
        result = owner_state.validate_and_whitelist(raw, bootstrap())
        self.assertEqual(result["entry_id"], 63984)
        self.assertEqual(result["bank_tenths"], 7)
        self.assertEqual(result["free_transfers"], 2)
        self.assertEqual(result["published_gameweek_lag"], 0)
        self.assertEqual(len(result["squad"]), 15)
        self.assertTrue(
            all(
                "purchase_price_tenths" in p and "selling_price_tenths" in p
                for p in result["squad"]
            )
        )
        self.assertEqual(result["private_authority"]["official_rebased_element_count"], 0)

    def test_previous_published_gameweek_is_valid_for_live_next_target(self):
        result = owner_state.validate_and_whitelist(
            json.dumps(snapshot(gw=4, published_gw=3)).encode(), bootstrap(4)
        )
        self.assertEqual(result["published_gameweek"], 3)
        self.assertEqual(result["target_gameweek"], 4)
        self.assertEqual(result["published_gameweek_lag"], 1)

    def test_team_state_more_than_one_gameweek_old_is_rejected(self):
        with self.assertRaises(owner_state.OwnerStateError):
            owner_state.validate_and_whitelist(
                json.dumps(snapshot(gw=4, published_gw=2)).encode(), bootstrap(4)
            )

    def test_future_published_gameweek_is_rejected(self):
        with self.assertRaises(owner_state.OwnerStateError):
            owner_state.validate_and_whitelist(
                json.dumps(snapshot(gw=4, published_gw=5)).encode(), bootstrap(4)
            )

    def test_secret_fields_are_not_propagated(self):
        result = owner_state.validate_and_whitelist(
            json.dumps(snapshot()).encode(), bootstrap()
        )
        text = json.dumps(result)
        self.assertNotIn("refresh_token", text)
        self.assertNotIn("SHOULD_NOT_LEAK", text)
        self.assertNotIn('"secret"', text)

    def test_duplicate_player_rejected(self):
        state = snapshot()
        state["team_state"]["squad"][1]["element_id"] = state["team_state"]["squad"][0]["element_id"]
        with self.assertRaises(owner_state.OwnerStateError):
            owner_state.validate_and_whitelist(json.dumps(state).encode(), bootstrap())

    def test_stale_target_gameweek_rejected(self):
        with self.assertRaises(owner_state.OwnerStateError):
            owner_state.validate_and_whitelist(
                json.dumps(snapshot(3)).encode(), bootstrap(4)
            )

    def test_wrong_free_transfers_rejected(self):
        state = snapshot()
        state["team_state"]["free_transfers"] = 6
        with self.assertRaises(owner_state.OwnerStateError):
            owner_state.validate_and_whitelist(json.dumps(state).encode(), bootstrap())

    def test_missing_price_rejected(self):
        state = snapshot()
        del state["team_state"]["squad"][0]["selling_price_tenths"]
        with self.assertRaises(owner_state.OwnerStateError):
            owner_state.validate_and_whitelist(json.dumps(state).encode(), bootstrap())

    def test_malformed_snapshot_selling_price_rejected(self):
        state = snapshot()
        state["team_state"]["squad"][0]["selling_price_tenths"] += 1
        with self.assertRaises(owner_state.OwnerStateError):
            owner_state.validate_and_whitelist(json.dumps(state).encode(), bootstrap())

    def test_official_price_rise_rebases_current_and_selling_price(self):
        official = bootstrap()
        official["elements"][0]["now_cost"] += 2
        result = owner_state.validate_and_whitelist(
            json.dumps(snapshot()).encode(), official
        )
        player = next(p for p in result["squad"] if p["element_id"] == 1)
        self.assertEqual(player["snapshot_current_price_tenths"], 51)
        self.assertEqual(player["current_price_tenths"], 53)
        self.assertEqual(player["purchase_price_tenths"], 51)
        self.assertEqual(player["snapshot_selling_price_tenths"], 51)
        self.assertEqual(player["selling_price_tenths"], 52)
        self.assertTrue(player["official_rebased"])
        self.assertEqual(result["private_authority"]["official_price_rebased_element_count"], 1)

    def test_official_price_drop_rebases_selling_price_in_full(self):
        official = bootstrap()
        official["elements"][0]["now_cost"] -= 1
        result = owner_state.validate_and_whitelist(
            json.dumps(snapshot()).encode(), official
        )
        player = next(p for p in result["squad"] if p["element_id"] == 1)
        self.assertEqual(player["current_price_tenths"], 50)
        self.assertEqual(player["selling_price_tenths"], 50)

    def test_live_official_status_replaces_snapshot_status(self):
        official = bootstrap()
        official["elements"][0]["status"] = "d"
        result = owner_state.validate_and_whitelist(
            json.dumps(snapshot()).encode(), official
        )
        player = next(p for p in result["squad"] if p["element_id"] == 1)
        self.assertEqual(player["status"], "d")
        self.assertTrue(player["official_rebased"])
        self.assertEqual(result["private_authority"]["official_status_rebased_element_count"], 1)


if __name__ == "__main__":
    unittest.main()
