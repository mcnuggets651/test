#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
import unicodedata
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable

import onside_fpl

SCHEMA = "onside-fpl-live-player-evidence-v1"
SERVER_RENDERED_BOARD_MIN_ROWS = 50
SERVER_RENDERED_BOARD_MIN_MATCH_RATIO = 0.75


class _TextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        value = data.strip()
        if value:
            self.parts.append(value)


def visible_text(page_html: str) -> str:
    parser = _TextParser()
    parser.feed(page_html)
    return " ".join(parser.parts)


def player_slug(web_name: str) -> str:
    folded = unicodedata.normalize("NFKD", web_name)
    ascii_value = "".join(ch for ch in folded if not unicodedata.combining(ch))
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_value.casefold()).strip("-")
    if not slug:
        raise onside_fpl.ProviderError(f"cannot construct Onside player slug from {web_name!r}")
    return slug


def player_url(element: int, web_name: str) -> str:
    return f"https://onsidearena.com/player/{player_slug(web_name)}-{element}"


def _float_match(text: str, patterns: list[str], field: str) -> float:
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return float(match.group(1))
    raise onside_fpl.ProviderError(f"Onside player page missing {field}")


def parse_player_page(page_bytes: bytes, *, element: int, web_name: str, target_gameweek: int) -> dict[str, Any]:
    try:
        page_html = page_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise onside_fpl.ProviderError("Onside player page is not UTF-8") from exc
    text = visible_text(page_html)

    id_match = re.search(r"\bFPL\s+ID\s+(\d+)\b", text, flags=re.IGNORECASE)
    if not id_match or int(id_match.group(1)) != element:
        raise onside_fpl.ProviderError(
            f"Onside player identity mismatch for element {element}: page FPL ID {id_match.group(1) if id_match else 'missing'}"
        )

    gw_tokens = {int(value) for value in re.findall(r"\bGW\s*(\d+)\b", text, flags=re.IGNORECASE)}
    if target_gameweek not in gw_tokens and not re.search(
        rf"\bGameweek\s+{target_gameweek}\b", text, flags=re.IGNORECASE
    ):
        raise onside_fpl.ProviderError(f"Onside player page does not reference target GW{target_gameweek}")

    gw_xp = _float_match(
        text,
        [
            rf"\bGW\s*{target_gameweek}\s*:\s*.{{0,180}}?projected\s+([0-9]+(?:\.[0-9]+)?)\s*xP\b",
            r"\b([0-9]+(?:\.[0-9]+)?)\s*xP\s+next\s+(?:match|gameweek)\b",
        ],
        "current-Gameweek xP",
    )
    six_gw_xp = _float_match(
        text,
        [r"Across\s+the\s+next\s+6\s+fixtures,?\s+Onside\s+projects\s+([0-9]+(?:\.[0-9]+)?)\s+total\s+xP"],
        "next-six-Gameweek xP",
    )
    start_probability = _float_match(
        text,
        [rf"Onside\s+gives\s+.{{0,100}}?\s+a\s+([0-9]+(?:\.[0-9]+)?)%\s+chance\s+of\s+starting\s+Gameweek\s+{target_gameweek}\b"],
        "start probability",
    )

    if not (0.0 <= gw_xp <= 30.0 and 0.0 <= six_gw_xp <= 180.0 and 0.0 <= start_probability <= 100.0):
        raise onside_fpl.ProviderError(
            f"implausible Onside live values for {web_name}: gw_xp={gw_xp}, six_gw_xp={six_gw_xp}, start={start_probability}"
        )

    engine_match = re.search(r"\bENGINE\s+V([A-Za-z0-9._-]+)\b", text, flags=re.IGNORECASE)
    as_of_match = re.search(r"\bAS\s+OF\s+(\d{1,2}\s+[A-Za-z]+)\b", text, flags=re.IGNORECASE)
    return {
        "element": element,
        "name": web_name,
        "target_gameweek": target_gameweek,
        "onside_xp": gw_xp,
        "onside_next_6_gw_xp": six_gw_xp,
        "onside_start_probability_pct": start_probability,
        "onside_engine": f"v{engine_match.group(1)}" if engine_match else "unknown",
        "onside_as_of": as_of_match.group(1) if as_of_match else None,
        "source_url": player_url(element, web_name),
        "source_sha256": hashlib.sha256(page_bytes).hexdigest(),
    }


def _official_elements(bootstrap: dict[str, Any]) -> dict[int, dict[str, Any]]:
    elements = bootstrap.get("elements")
    if not isinstance(elements, list):
        raise onside_fpl.ProviderError("Official FPL bootstrap missing elements")
    result: dict[int, dict[str, Any]] = {}
    for value in elements:
        if not isinstance(value, dict):
            continue
        try:
            element = int(value["id"])
        except (KeyError, TypeError, ValueError):
            continue
        result[element] = value
    return result


def _looks_departed(news: str) -> bool:
    value = news.casefold()
    return any(
        marker in value
        for marker in (
            "has joined ",
            "joined on loan",
            "joined permanently",
            "transferred to ",
            "signed for ",
            "loan for the rest of the season",
        )
    )


def discover_top_elements(page_bytes: bytes, bootstrap_bytes: bytes, *, limit: int) -> tuple[list[int], dict[str, Any]]:
    board = onside_fpl.build_bundle(
        page_bytes=page_bytes,
        bootstrap_bytes=bootstrap_bytes,
        min_source_rows=SERVER_RENDERED_BOARD_MIN_ROWS,
        min_match_ratio=SERVER_RENDERED_BOARD_MIN_MATCH_RATIO,
    )
    elements = [int(row["element"]) for row in board["players"][:limit]]
    if not elements:
        raise onside_fpl.ProviderError("Onside server-rendered board produced no current FPL identities")
    return elements, board


def build_live_bundle(
    *,
    bootstrap_bytes: bytes,
    elements: list[int],
    page_fetcher: Callable[[str], bytes] = onside_fpl.fetch_bytes,
    fetched_at: dt.datetime | None = None,
) -> dict[str, Any]:
    try:
        bootstrap = json.loads(bootstrap_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise onside_fpl.ProviderError("Official FPL bootstrap decoding failed") from exc
    if not isinstance(bootstrap, dict):
        raise onside_fpl.ProviderError("Official FPL bootstrap must be a JSON object")
    target_gw, deadline = onside_fpl._official_next_event(bootstrap)
    official = _official_elements(bootstrap)

    requested = list(dict.fromkeys(int(x) for x in elements))
    if not requested:
        raise onside_fpl.ProviderError("at least one Official FPL element id is required")

    players: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    for element in requested:
        row = official.get(element)
        if row is None:
            excluded.append({"element": element, "reason": "not_current_official_fpl_element"})
            continue
        name = str(row.get("web_name") or "").strip()
        news = str(row.get("news") or "")
        if not name or _looks_departed(news):
            excluded.append({"element": element, "name": name, "reason": "current_player_sanity_gate"})
            continue
        url = player_url(element, name)
        page = page_fetcher(url)
        evidence = parse_player_page(page, element=element, web_name=name, target_gameweek=target_gw)
        evidence.update(
            {
                "official_status": row.get("status"),
                "official_chance_next_round": row.get("chance_of_playing_next_round"),
                "official_news": news,
                "official_now_cost": (float(row.get("now_cost", 0)) / 10.0) if row.get("now_cost") is not None else None,
            }
        )
        players.append(evidence)

    if not players:
        raise onside_fpl.ProviderError("no requested Onside player evidence passed validation")

    fetched_at = (fetched_at or dt.datetime.now(dt.timezone.utc)).astimezone(dt.timezone.utc)
    return {
        "schema": SCHEMA,
        "status": "ready",
        "provider": "Onside",
        "target_gameweek": target_gw,
        "official_deadline_utc": deadline.isoformat(),
        "fetched_at_utc": fetched_at.isoformat(),
        "requested_elements": requested,
        "players": players,
        "excluded": excluded,
        "provenance": {
            "official_fpl_url": onside_fpl.OFFICIAL_FPL_URL,
            "official_fpl_response_sha256": hashlib.sha256(bootstrap_bytes).hexdigest(),
            "attribution": "Onside — https://onsidearena.com/",
            "transport": "public Onside player pages",
            "mcp": {
                "package": onside_fpl.ONSIDE_MCP_PACKAGE,
                "version": onside_fpl.ONSIDE_MCP_VERSION,
                "commit_sha": onside_fpl.ONSIDE_MCP_COMMIT,
                "fpl_tool_exposed_at_pinned_commit": False,
            },
        },
        "semantics": {
            "live_player_pages": "current Onside model evidence; may update between matches/deadlines",
            "frozen_board": "separate pre-deadline audit ledger; do not conflate with current live values",
            "independence": "Onside evidence is not averaged with or substituted for Dastan/SmartPlay or AIrsenal",
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch current live Onside evidence for selected Official FPL players")
    parser.add_argument("--elements", help="comma-separated Official FPL element ids")
    parser.add_argument("--top", type=int, default=5, help="if --elements is omitted, discover this many from server-rendered board")
    parser.add_argument("--out", type=Path, help="write canonical JSON here; stdout if omitted")
    args = parser.parse_args()

    bootstrap_bytes = onside_fpl.fetch_bytes(onside_fpl.OFFICIAL_FPL_URL)
    if args.elements:
        try:
            elements = [int(x.strip()) for x in args.elements.split(",") if x.strip()]
        except ValueError as exc:
            raise SystemExit("--elements must be comma-separated integers") from exc
    else:
        board_bytes = onside_fpl.fetch_bytes(onside_fpl.ONSIDE_URL)
        elements, _board = discover_top_elements(board_bytes, bootstrap_bytes, limit=max(1, args.top))

    bundle = build_live_bundle(bootstrap_bytes=bootstrap_bytes, elements=elements)
    text = json.dumps(bundle, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
    else:
        print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
