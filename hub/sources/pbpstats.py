"""PBP Stats (pbpstats.com) connector: possession-level stats built from NBA play-by-play.

Unique value vs. the other sources: shot quality, seconds per possession, lineup and on/off
data, putbacks/second-chance splits, penalty (bonus) splits, and stats by shot zone with
blocked/assisted breakdowns. Uses the public JSON API at api.pbpstats.com.

IDs are NBA.com ids (team 1610612766, player 1641706, lineups 'id-id-id-id-id').
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pandas as pd

from hub.cache import ResponseCache
from hub.http import CachedFetcher
from hub.sources.nba_stats import current_season
from hub.teams import Team, find_team

BASE_URL = "https://api.pbpstats.com"
ENTITY_TYPES = ("Team", "Player", "Lineup", "LineupOpponent", "Opponent")

# On/off values for these stats are raw totals, so we also express them per 48 minutes.
COUNTING_STATS = {"PlusMinus", "Points", "OpponentPoints", "Rebounds", "OffRebounds",
                  "DefRebounds", "Assists", "Turnovers", "Steals", "Blocks", "FG3M", "FG3A",
                  "FG2M", "FG2A", "FTA", "Fouls", "FoulsDrawn"}


def _team_id(team: Team | str | int | None) -> int | None:
    if team is None:
        return None
    return team.id if isinstance(team, Team) else find_team(team).id


def _numeric(df: pd.DataFrame) -> pd.DataFrame:
    """The on/off endpoint returns numbers as strings ('-2.000'); convert where possible."""
    for col in df.columns:
        if not pd.api.types.is_numeric_dtype(df[col]):
            converted = pd.to_numeric(df[col], errors="coerce")
            if converted.notna().sum() == df[col].notna().sum():
                df[col] = converted
    return df


class PBPStatsClient:
    def __init__(self, cache: ResponseCache | None = None, session=None,
                 min_interval: float = 2.0, timeout: float = 45.0, retries: int = 4):
        self.http = CachedFetcher(
            "pbpstats", BASE_URL, {"Accept": "application/json"}, cache, session,
            min_interval, timeout, retries,
            unavailable_hint="pbpstats.com is a one-person project and sometimes busy; retry later.",
        )

    def _get(self, path: str, params: dict[str, Any], date_to: date | None = None) -> dict:
        params = {k: v for k, v in params.items() if v not in (None, "")}
        frozen = (date_to is not None and date_to < date.today()) or \
            params.get("Season", current_season()) < current_season()
        return self.http.get(path, params, ttl=None if frozen else 6 * 3600)

    def totals(
        self,
        entity: str = "Team",
        season: str | None = None,
        season_type: str = "Regular Season",
        team: Team | str | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
        **filters: Any,
    ) -> pd.DataFrame:
        """Season (or date-window) totals for every team, player, or lineup.

        Extra filters pass straight through to the API, e.g. StartType='OffDeadball'.
        """
        if entity not in ENTITY_TYPES:
            raise ValueError(f"entity must be one of {ENTITY_TYPES}")
        body = self._get("get-totals/nba", {
            "Season": season or current_season(), "SeasonType": season_type, "Type": entity,
            "TeamId": _team_id(team),
            "FromDate": date_from.isoformat() if date_from else None,
            "ToDate": date_to.isoformat() if date_to else None,
            **filters,
        }, date_to)
        return pd.DataFrame(body.get("multi_row_table_data", []))

    def game_logs(
        self,
        team: Team | str | None = None,
        player_id: str | int | None = None,
        lineup_id: str | None = None,
        season: str | None = None,
        season_type: str = "Regular Season",
    ) -> pd.DataFrame:
        """Per-game possession stats for one team, player (NBA.com id), or lineup."""
        if sum(x is not None for x in (team, player_id, lineup_id)) != 1:
            raise ValueError("pass exactly one of team, player_id, lineup_id")
        if team is not None:
            entity, entity_id = "Team", _team_id(team)
        elif player_id is not None:
            entity, entity_id = "Player", player_id
        else:
            entity, entity_id = "Lineup", lineup_id
        body = self._get("get-game-logs/nba", {
            "Season": season or current_season(), "SeasonType": season_type,
            "EntityType": entity, "EntityId": entity_id,
        })
        df = pd.DataFrame(body.get("multi_row_table_data", []))
        if "Date" in df.columns:
            df["Date"] = pd.to_datetime(df["Date"]).dt.date
            df = df.sort_values("Date").reset_index(drop=True)
        return df

    def on_off(self, team: Team | str, stat: str = "PlusMinus", season: str | None = None,
               season_type: str = "Regular Season") -> pd.DataFrame:
        """Each player's team `stat` with them on vs. off the floor."""
        body = self._get("get-on-off/nba/stat", {
            "Season": season or current_season(), "SeasonType": season_type,
            "TeamId": _team_id(team), "Stat": stat,
        })
        df = _numeric(pd.DataFrame(body.get("results", [])))
        if df.empty:
            return df
        sort_col = "On-Off"
        if stat in COUNTING_STATS:
            df["On/48"] = (df["On"] / df["MinutesOn"] * 48).round(1)
            df["Off/48"] = (df["Off"] / df["MinutesOff"] * 48).round(1)
            df["Net/48"] = (df["On/48"] - df["Off/48"]).round(1)
            sort_col = "Net/48"
        return df.sort_values(sort_col, ascending=False).reset_index(drop=True)

    def find_player(self, name: str, team: Team | str | None = None,
                    season: str | None = None) -> pd.DataFrame:
        """Look up NBA.com player ids by (partial) name."""
        df = self.totals("Player", season, team=team)
        hits = df[df["Name"].str.contains(name, case=False, na=False)]
        return hits[["Name", "EntityId", "TeamAbbreviation"]].reset_index(drop=True)
