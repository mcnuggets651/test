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


def snapshot(gw: int = 4):
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
            "published_gw": gw,
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
        self.assertEqual(len(result["squad"]), 15)
        self.assertTrue(
            all(
                "purchase_price_tenths" in p and "selling_price_tenths" in p
                for p in result["squad"]
            )
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

    def test_official_price_drift_rejected(self):
        official = bootstrap()
        official["elements"][0]["now_cost"] += 1
        with self.assertRaises(owner_state.OwnerStateError):
            owner_state.validate_and_whitelist(
                json.dumps(snapshot()).encode(), official
            )


if __name__ == "__main__":
    unittest.main()
