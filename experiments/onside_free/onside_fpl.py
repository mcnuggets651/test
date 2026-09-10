#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import html
import json
import re
import unicodedata
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterable
from urllib.request import Request, urlopen

ONSIDE_URL = "https://onsidearena.com/fpl/predicted-points"
OFFICIAL_FPL_URL = "https://fantasy.premierleague.com/api/bootstrap-static/"
ONSIDE_GRADED_DATA_URL = "https://onsidearena.com/data/graded-predictions.csv"
ONSIDE_MCP_PACKAGE = "onside-football-mcp"
ONSIDE_MCP_VERSION = "0.2.0"
ONSIDE_MCP_COMMIT = "cf48d1d3374768de5cb6f7716d7e76f06b16e0b6"
SCHEMA = "onside-fpl-evidence-v1"
DEFAULT_MAX_CAPTURE_AGE_HOURS = 240
DEFAULT_MIN_SOURCE_ROWS = 200
DEFAULT_MIN_MATCH_RATIO = 0.85
USER_AGENT = "fpl-test-onside-provider/1.0 (+https://github.com/mcnuggets651/test)"


class ProviderError(RuntimeError):
    pass


@dataclass(frozen=True)
class ProjectionRow:
    name: str
    team: str
    position: str
    price: float | None
    ownership: float | None
    xp: float


class _TableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[list[str]] = []
        self._in_row = False
        self._in_cell = False
        self._row: list[str] = []
        self._cell: list[str] = []
        self.text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag == "tr":
            self._in_row = True
            self._row = []
        elif self._in_row and tag in {"td", "th"}:
            self._in_cell = True
            self._cell = []

    def handle_data(self, data: str) -> None:
        value = data.strip()
        if value:
            self.text.append(value)
            if self._in_cell:
                self._cell.append(value)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if self._in_row and tag in {"td", "th"} and self._in_cell:
            value = " ".join(self._cell).strip()
            self._row.append(value)
            self._cell = []
            self._in_cell = False
        elif tag == "tr" and self._in_row:
            if self._row:
                self.rows.append(self._row)
            self._row = []
            self._in_row = False
            self._in_cell = False


def _normalise(value: str) -> str:
    folded = unicodedata.normalize("NFKD", value)
    ascii_value = "".join(ch for ch in folded if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+", "", ascii_value.casefold())


def _parse_float(value: str) -> float | None:
    cleaned = value.replace("£", "").replace("%", "").replace(",", "").strip()
    if not cleaned or cleaned in {"-", "—", "n/a", "N/A"}:
        return None
    match = re.search(r"-?\d+(?:\.\d+)?", cleaned)
    return float(match.group(0)) if match else None


def _header_index(headers: list[str], variants: Iterable[str]) -> int | None:
    normalised = [_normalise(x) for x in headers]
    wanted = {_normalise(x) for x in variants}
    for i, item in enumerate(normalised):
        if item in wanted:
            return i
    for i, item in enumerate(normalised):
        if any(token and token in item for token in wanted):
            return i
    return None


def parse_projection_page(page_html: str) -> tuple[list[ProjectionRow], str, int, str]:
    parser = _TableParser()
    parser.feed(page_html)
    visible = " ".join(parser.text)

    gw_match = re.search(r"\bGameweek\s+(\d+)\b", visible, flags=re.IGNORECASE)
    if not gw_match:
        gw_match = re.search(r"\bGW\s*(\d+)\b", visible, flags=re.IGNORECASE)
    if not gw_match:
        raise ProviderError("Onside page does not expose a parseable target Gameweek")
    gameweek = int(gw_match.group(1))

    engine_match = re.search(r"\bengine\s+v?([A-Za-z0-9._-]+)", visible, flags=re.IGNORECASE)
    engine = f"v{engine_match.group(1)}" if engine_match else "unknown"

    frozen_match = re.search(
        r"\bFrozen\s+(\d{1,2})\s+([A-Za-z]+),?\s+(\d{1,2}:\d{2})\s+UTC\b",
        visible,
        flags=re.IGNORECASE,
    )
    if not frozen_match:
        raise ProviderError("Onside page does not expose a frozen UTC capture timestamp")
    frozen_fragment = f"{frozen_match.group(1)} {frozen_match.group(2)} {frozen_match.group(3)}"

    rows: list[ProjectionRow] = []
    seen: dict[tuple[str, str, str], float] = {}
    header_found = False
    for i, row in enumerate(parser.rows):
        xp_idx = _header_index(row, ("Onside xP", "xP", "Projected points", "Expected points"))
        player_idx = _header_index(row, ("Player", "Name"))
        team_idx = _header_index(row, ("Club", "Team"))
        pos_idx = _header_index(row, ("Pos", "Position"))
        if xp_idx is None or player_idx is None or team_idx is None or pos_idx is None:
            continue
        price_idx = _header_index(row, ("Price", "Cost"))
        own_idx = _header_index(row, ("Owned", "Ownership", "Own"))
        header_found = True
        for candidate in parser.rows[i + 1 :]:
            needed = max(xp_idx, player_idx, team_idx, pos_idx)
            if len(candidate) <= needed:
                continue
            name = candidate[player_idx].strip().rstrip("!").strip()
            team = candidate[team_idx].strip().upper()
            position = candidate[pos_idx].strip().upper()
            xp = _parse_float(candidate[xp_idx])
            if not name or not re.fullmatch(r"[A-Z]{2,4}", team) or position not in {"GKP", "DEF", "MID", "FWD"} or xp is None:
                continue
            if not (-2.0 <= xp <= 30.0):
                raise ProviderError(f"implausible Onside xP for {name}: {xp}")
            price = _parse_float(candidate[price_idx]) if price_idx is not None and len(candidate) > price_idx else None
            ownership = _parse_float(candidate[own_idx]) if own_idx is not None and len(candidate) > own_idx else None
            key = (_normalise(name), team, position)
            if key in seen:
                if abs(seen[key] - xp) > 1e-9:
                    raise ProviderError(f"conflicting duplicate Onside row for {name}/{team}/{position}")
                continue
            seen[key] = xp
            rows.append(ProjectionRow(name=name, team=team, position=position, price=price, ownership=ownership, xp=xp))
        break

    if not header_found:
        raise ProviderError("Onside projection table header not found")
    return rows, visible, gameweek, engine + "|" + frozen_fragment


def _parse_utc_fragment(fragment: str, deadline: dt.datetime) -> dt.datetime:
    match = re.fullmatch(r"(\d{1,2})\s+([A-Za-z]+)\s+(\d{1,2}:\d{2})", fragment.strip())
    if not match:
        raise ProviderError(f"invalid frozen timestamp fragment: {fragment!r}")
    day = int(match.group(1))
    month_text = match.group(2)
    hhmm = match.group(3)
    month = dt.datetime.strptime(month_text[:3].title(), "%b").month
    hour, minute = (int(x) for x in hhmm.split(":"))
    candidates: list[dt.datetime] = []
    for year in (deadline.year - 1, deadline.year, deadline.year + 1):
        try:
            candidates.append(dt.datetime(year, month, day, hour, minute, tzinfo=dt.timezone.utc))
        except ValueError:
            continue
    before = [x for x in candidates if x <= deadline]
    if before:
        return min(before, key=lambda x: deadline - x)
    if not candidates:
        raise ProviderError("could not materialise Onside frozen timestamp")
    return min(candidates, key=lambda x: abs(deadline - x))


def _iso_utc(value: str) -> dt.datetime:
    parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


def _official_next_event(bootstrap: dict[str, Any]) -> tuple[int, dt.datetime]:
    events = bootstrap.get("events")
    if not isinstance(events, list):
        raise ProviderError("Official FPL bootstrap missing events")
    next_events = [e for e in events if isinstance(e, dict) and e.get("is_next") is True]
    if len(next_events) != 1:
        raise ProviderError(f"Official FPL must expose exactly one is_next event, got {len(next_events)}")
    event = next_events[0]
    try:
        return int(event["id"]), _iso_utc(str(event["deadline_time"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise ProviderError("Official FPL next event is malformed") from exc


def _departed(news: str) -> bool:
    value = news.casefold()
    markers = (
        "has joined ",
        "joined on loan",
        "joined permanently",
        "transferred to ",
        "signed for ",
        "loan for the rest of the season",
    )
    return any(marker in value for marker in markers)


def _official_index(bootstrap: dict[str, Any]) -> dict[tuple[str, str, str], list[dict[str, Any]]]:
    teams = bootstrap.get("teams")
    elements = bootstrap.get("elements")
    element_types = bootstrap.get("element_types")
    if not isinstance(teams, list) or not isinstance(elements, list) or not isinstance(element_types, list):
        raise ProviderError("Official FPL bootstrap missing teams/elements/element_types")
    team_short = {int(t["id"]): str(t["short_name"]).upper() for t in teams if isinstance(t, dict) and "id" in t and "short_name" in t}
    pos_short = {
        int(p["id"]): str(p.get("singular_name_short") or p.get("singular_name") or "").upper()
        for p in element_types
        if isinstance(p, dict) and "id" in p
    }
    index: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for element in elements:
        if not isinstance(element, dict):
            continue
        try:
            team = team_short[int(element["team"])]
            pos = pos_short[int(element["element_type"])]
        except (KeyError, TypeError, ValueError):
            continue
        names = {
            str(element.get("web_name") or ""),
            f"{element.get('first_name') or ''} {element.get('second_name') or ''}".strip(),
            str(element.get("second_name") or ""),
        }
        for name in names:
            norm = _normalise(name)
            if norm:
                index.setdefault((norm, team, pos), []).append(element)
    return index


def match_rows(rows: list[ProjectionRow], bootstrap: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    index = _official_index(bootstrap)
    matched: list[dict[str, Any]] = []
    unmatched: list[dict[str, Any]] = []
    for row in rows:
        candidates = index.get((_normalise(row.name), row.team, row.position), [])
        unique = {int(x.get("id", 0)): x for x in candidates if int(x.get("id", 0)) > 0}
        if len(unique) != 1:
            unmatched.append({"name": row.name, "team": row.team, "position": row.position, "xp": row.xp, "reason": "identity_unmatched_or_ambiguous"})
            continue
        element = next(iter(unique.values()))
        news = str(element.get("news") or "")
        if _departed(news):
            unmatched.append({"name": row.name, "team": row.team, "position": row.position, "xp": row.xp, "reason": "official_news_indicates_departure"})
            continue
        matched.append(
            {
                "element": int(element["id"]),
                "name": str(element.get("web_name") or row.name),
                "onside_name": row.name,
                "team": row.team,
                "position": row.position,
                "onside_xp": row.xp,
                "onside_price": row.price,
                "onside_ownership_pct": row.ownership,
                "official_now_cost": (float(element.get("now_cost", 0)) / 10.0) if element.get("now_cost") is not None else None,
                "official_status": element.get("status"),
                "official_chance_next_round": element.get("chance_of_playing_next_round"),
                "official_news": news,
            }
        )
    matched.sort(key=lambda x: (-float(x["onside_xp"]), int(x["element"])))
    return matched, unmatched


def fetch_bytes(url: str, timeout: int = 20) -> bytes:
    request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/json;q=0.9,*/*;q=0.8"})
    with urlopen(request, timeout=timeout) as response:  # noqa: S310 - fixed allow-listed HTTPS URLs
        return response.read()


def build_bundle(
    *,
    page_bytes: bytes,
    bootstrap_bytes: bytes,
    fetched_at: dt.datetime | None = None,
    max_capture_age_hours: int = DEFAULT_MAX_CAPTURE_AGE_HOURS,
    min_source_rows: int = DEFAULT_MIN_SOURCE_ROWS,
    min_match_ratio: float = DEFAULT_MIN_MATCH_RATIO,
) -> dict[str, Any]:
    fetched_at = (fetched_at or dt.datetime.now(dt.timezone.utc)).astimezone(dt.timezone.utc)
    try:
        page_html = page_bytes.decode("utf-8")
        bootstrap = json.loads(bootstrap_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProviderError("source payload decoding failed") from exc
    if not isinstance(bootstrap, dict):
        raise ProviderError("Official FPL bootstrap must be a JSON object")

    rows, _visible, page_gw, metadata = parse_projection_page(page_html)
    engine, frozen_fragment = metadata.split("|", 1)
    official_gw, deadline = _official_next_event(bootstrap)
    capture = _parse_utc_fragment(frozen_fragment, deadline)

    if page_gw != official_gw:
        raise ProviderError(f"Onside GW{page_gw} does not match Official FPL next GW{official_gw}")
    if capture >= deadline:
        raise ProviderError(f"Onside capture {capture.isoformat()} is not pre-deadline {deadline.isoformat()}")
    age_hours = (fetched_at - capture).total_seconds() / 3600.0
    if age_hours < -1:
        raise ProviderError("Onside capture timestamp is materially in the future")
    if age_hours > max_capture_age_hours:
        raise ProviderError(f"Onside capture is stale: {age_hours:.1f}h > {max_capture_age_hours}h")
    if len(rows) < min_source_rows:
        raise ProviderError(f"Onside projection table too small: {len(rows)} < {min_source_rows}")

    matched, unmatched = match_rows(rows, bootstrap)
    ratio = len(matched) / len(rows) if rows else 0.0
    if ratio < min_match_ratio:
        raise ProviderError(f"Onside/Official FPL identity match ratio too low: {ratio:.3f} < {min_match_ratio:.3f}")

    return {
        "schema": SCHEMA,
        "status": "ready",
        "provider": "Onside",
        "target_gameweek": official_gw,
        "official_deadline_utc": deadline.isoformat(),
        "onside_engine_version": engine,
        "onside_capture_utc": capture.isoformat(),
        "fetched_at_utc": fetched_at.isoformat(),
        "capture_age_hours": round(age_hours, 3),
        "coverage": {
            "source_rows": len(rows),
            "matched_current_fpl_rows": len(matched),
            "excluded_rows": len(unmatched),
            "match_ratio": round(ratio, 6),
        },
        "players": matched,
        "excluded_source_rows": unmatched,
        "provenance": {
            "projection_url": ONSIDE_URL,
            "projection_response_sha256": hashlib.sha256(page_bytes).hexdigest(),
            "official_fpl_url": OFFICIAL_FPL_URL,
            "official_fpl_response_sha256": hashlib.sha256(bootstrap_bytes).hexdigest(),
            "graded_dataset_url": ONSIDE_GRADED_DATA_URL,
            "attribution": "Onside — https://onsidearena.com/",
            "upstream_mcp": {
                "package": ONSIDE_MCP_PACKAGE,
                "version": ONSIDE_MCP_VERSION,
                "commit_sha": ONSIDE_MCP_COMMIT,
                "fpl_tool_exposed_at_pinned_commit": False,
                "note": "Pinned MCP exposes World Cup tools only; FPL evidence is not represented as MCP output.",
            },
        },
        "limitations": [
            "Onside evidence from this adapter is current-Gameweek xP only.",
            "No Onside expected-minutes or start-probability field is exposed by this validated surface.",
            "No Onside multi-Gameweek optimiser edge is inferred or fabricated.",
            "Onside remains independent from Dastan/SmartPlay and AIrsenal; values are not silently averaged.",
            "Cross-model accuracy claims must be recomputed on matched rows before provider ranking.",
        ],
    }


def live_bundle() -> dict[str, Any]:
    return build_bundle(page_bytes=fetch_bytes(ONSIDE_URL), bootstrap_bytes=fetch_bytes(OFFICIAL_FPL_URL))


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch and validate current Onside FPL evidence")
    parser.add_argument("--out", type=Path, help="write canonical JSON to this path; stdout if omitted")
    parser.add_argument("--onside-html", type=Path, help="offline Onside HTML fixture")
    parser.add_argument("--bootstrap-json", type=Path, help="offline Official FPL bootstrap fixture")
    args = parser.parse_args()

    if bool(args.onside_html) != bool(args.bootstrap_json):
        parser.error("--onside-html and --bootstrap-json must be supplied together")
    if args.onside_html:
        bundle = build_bundle(
            page_bytes=args.onside_html.read_bytes(),
            bootstrap_bytes=args.bootstrap_json.read_bytes(),
        )
    else:
        bundle = live_bundle()
    text = json.dumps(bundle, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
    else:
        print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
