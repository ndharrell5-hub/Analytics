"""Basketball Reference (basketball-reference.com) connector.

Pages are server-rendered HTML. Every cell carries a stable `data-stat` attribute, which we use
as the column name. Some secondary tables are shipped inside HTML comments and revealed by
JavaScript, so we un-comment the page before parsing.

Sports Reference enforces roughly 20 requests/minute per IP and temporarily blocks clients that
exceed it, so this client waits >= 3.5s between requests and caches every page. Their terms
allow light personal use; don't bulk-download the site.
"""

from __future__ import annotations

from typing import Any

import pandas as pd
from bs4 import BeautifulSoup

from hub.cache import ResponseCache
from hub.http import CachedFetcher
from hub.sources.nba_stats import current_season
from hub.teams import Team, find_team

BASE_URL = "https://www.basketball-reference.com"

# Tables on /teams/<ABBR>/<YEAR>.html, by id.
TEAM_PAGE_TABLES = {
    "roster": "Roster", "team_and_opponent": "Team & opponent totals",
    "team_misc": "Misc (ORtg/DRtg/pace/four factors)", "per_game_stats": "Player per game",
    "totals_stats": "Player totals", "per_minute_stats": "Player per 36",
    "per_poss": "Player per 100 poss", "advanced": "Player advanced (PER, BPM, WS...)",
    "adj_shooting": "Adjusted shooting", "shooting": "Shooting by distance",
    "pbp_stats": "Play-by-play (on/off, positions)", "salaries2": "Salaries",
    "injuries": "Injury report",
}

# League-wide pages: /leagues/NBA_<YEAR>_<suffix>.html  ->  table id
LEAGUE_PAGES = {
    "per_game": ("per_game", "per_game_stats"),
    "totals": ("totals", "totals_stats"),
    "per_minute": ("per_minute", "per_minute_stats"),
    "per_poss": ("per_poss", "per_poss_stats"),
    "advanced": ("advanced", "advanced"),
    "play_by_play": ("play-by-play", "pbp_stats"),
    "shooting": ("shooting", "shooting_stats"),
    "adj_shooting": ("adj_shooting", "adj-shooting"),
}


def season_end_year(season: str | None) -> int:
    """'2026-27' -> 2027 (Basketball Reference names seasons by the year they end)."""
    season = season or current_season()
    return int(season[:4]) + 1


def _cell_value(text: str) -> Any:
    text = text.strip()
    if text == "":
        return None
    try:
        return int(text)
    except ValueError:
        pass
    try:
        return float(text)
    except ValueError:
        return text


def parse_tables(html: str) -> dict[str, pd.DataFrame]:
    """Every <table id=...> on a page (including commented-out ones), keyed by id.

    Columns use each cell's data-stat. Repeated header rows and spacer rows are dropped.
    Player/team links are kept as extra `<col>_id` columns (e.g. name_display_id = 'millebr02').
    """
    html = html.replace("<!--", "").replace("-->", "")
    soup = BeautifulSoup(html, "lxml")
    tables: dict[str, pd.DataFrame] = {}
    for table in soup.find_all("table"):
        tid = table.get("id")
        body = table.find("tbody")
        if not tid or body is None:
            continue
        records = []
        for tr in body.find_all("tr"):
            classes = tr.get("class") or []
            if "thead" in classes or "spacer" in classes or "over_header" in classes:
                continue
            rec: dict[str, Any] = {}
            for cell in tr.find_all(["th", "td"]):
                stat = cell.get("data-stat")
                if not stat:
                    continue
                rec[stat] = _cell_value(cell.get_text())
                link = cell.find("a")
                href = link.get("href", "") if link else ""
                if href.startswith("/teams/"):  # /teams/PHO/2027.html -> PHO
                    rec[f"{stat}_id"] = href.split("/")[2]
                elif href.count("/") >= 2:  # /players/m/millebr02.html -> millebr02
                    rec[f"{stat}_id"] = href.rstrip("/").rsplit("/", 1)[-1].removesuffix(".html")
            if any(v is not None for k, v in rec.items() if k != "ranker"):
                records.append(rec)
        tables[tid] = pd.DataFrame(records)
    return tables


class BBRefClient:
    def __init__(self, cache: ResponseCache | None = None, session=None,
                 min_interval: float = 3.5, timeout: float = 30.0, retries: int = 3):
        self.http = CachedFetcher(
            "basketball-reference", BASE_URL, {"Accept": "text/html"}, cache, session,
            min_interval, timeout, retries,
            unavailable_hint=("Basketball Reference rate-limits to ~20 requests/minute; "
                              "if you were blocked, wait an hour and retry."),
        )

    def page(self, path: str, ttl: float | None = 6 * 3600) -> dict[str, pd.DataFrame]:
        return parse_tables(self.http.get(path, ttl=ttl, as_json=False))

    def table(self, path: str, table_id: str, ttl: float | None = 6 * 3600) -> pd.DataFrame:
        tables = self.page(path, ttl)
        if table_id not in tables:
            raise KeyError(f"No table {table_id!r} on {path}. Found: {sorted(tables)}")
        return tables[table_id]

    @staticmethod
    def _ttl(season: str | None) -> float | None:
        # Past seasons are final; cache forever.
        return None if season and season < current_season() else 6 * 3600

    # ------------------------------------------------------------------ team

    def team_game_log(self, team: Team | str, season: str | None = None,
                      advanced: bool = False, playoffs: bool = False) -> pd.DataFrame:
        """Box-score game log (or advanced: ORtg/DRtg/pace/four factors) for every game."""
        team = team if isinstance(team, Team) else find_team(team)
        kind = "gamelog-advanced" if advanced else "gamelog"
        tables = self.page(f"/teams/{team.bbref_abbr}/{season_end_year(season)}/{kind}/",
                           self._ttl(season))
        prefix = "team_game_log_adv" if advanced else "team_game_log"
        want = f"{prefix}_{'post' if playoffs else 'reg'}"
        if want not in tables:
            if playoffs:
                return pd.DataFrame()
            want = next((k for k in tables if k.startswith(prefix)), None)
            if want is None:
                raise KeyError(f"No game log table found. Tables: {sorted(tables)}")
        df = tables[want]
        if "date" in df.columns:
            df = df[df["date"].notna()].copy()
            df["date"] = pd.to_datetime(df["date"]).dt.date
        if "game_location" in df.columns:
            df["game_location"] = df["game_location"].fillna("vs")
        return df.reset_index(drop=True)

    def team_page_table(self, team: Team | str, table_id: str,
                        season: str | None = None) -> pd.DataFrame:
        """Any table from a team's season page; see TEAM_PAGE_TABLES."""
        team = team if isinstance(team, Team) else find_team(team)
        return self.table(f"/teams/{team.bbref_abbr}/{season_end_year(season)}.html",
                          table_id, self._ttl(season))

    # ------------------------------------------------------------------ league / players

    def league_players(self, kind: str = "per_game", season: str | None = None) -> pd.DataFrame:
        """League-wide player table: per_game, totals, per_minute, per_poss, advanced, ..."""
        if kind not in LEAGUE_PAGES:
            raise ValueError(f"kind must be one of {sorted(LEAGUE_PAGES)}")
        suffix, table_id = LEAGUE_PAGES[kind]
        return self.table(f"/leagues/NBA_{season_end_year(season)}_{suffix}.html",
                          table_id, self._ttl(season))

    def player_game_log(self, player_id: str, season: str | None = None) -> pd.DataFrame:
        """Game log for a player by Basketball Reference id (e.g. 'millebr02').

        Find ids with find_player() or from the *_id columns of any player table.
        """
        path = f"/players/{player_id[0]}/{player_id}/gamelog/{season_end_year(season)}"
        tables = self.page(path, self._ttl(season))
        tid = next((k for k in ("player_game_log_reg", "pgl_basic") if k in tables), None)
        if tid is None:
            raise KeyError(f"No game log on {path}. Tables: {sorted(tables)}")
        df = tables[tid]
        if "date" in df.columns:
            df = df[df["date"].notna()].copy()
            df["date"] = pd.to_datetime(df["date"]).dt.date
        if "game_location" in df.columns:
            df["game_location"] = df["game_location"].fillna("vs")
        return df.reset_index(drop=True)

    def find_player(self, name: str, season: str | None = None) -> pd.DataFrame:
        """Search this season's league per-game table for a player name (case-insensitive)."""
        df = self.league_players("per_game", season)
        name_col = "name_display" if "name_display" in df.columns else "player"
        hits = df[df[name_col].str.contains(name, case=False, na=False)]
        id_col = f"{name_col}_id"
        cols = [c for c in (name_col, id_col, "team_name_abbr", "team_id", "pos") if c in hits]
        return hits[cols].drop_duplicates(subset=[id_col]).reset_index(drop=True)

