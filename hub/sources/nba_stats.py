"""Client for the (undocumented) NBA.com stats API at stats.nba.com/stats/.

Every table on nba.com/stats is backed by one of these JSON endpoints. Responses look like:

    {"resultSets": [{"name": "...", "headers": [...], "rowSet": [[...], ...]}], ...}

Notes on access:
  * Requests need browser-like headers (User-Agent, Referer, Origin) or they hang.
  * NBA.com's CDN silently drops connections from most cloud/data-center IPs. Run this from a
    home or office connection. A 30s timeout followed by "empty reply" means you're blocked.
  * Be polite: we rate-limit and cache everything.
"""

from __future__ import annotations

import time
from datetime import date, datetime
from typing import Any

import pandas as pd
import requests

from hub.cache import ResponseCache
from hub.teams import Team, find_team

BASE_URL = "https://stats.nba.com/stats"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nba.com/",
    "Origin": "https://www.nba.com",
    "Connection": "keep-alive",
}

# PtMeasureType values for leaguedashptstats (the "Tracking" section of nba.com/stats).
TRACKING_MEASURES = {
    "SpeedDistance": "Speed & distance",
    "Possessions": "Touches, time of possession, sec/dribbles per touch",
    "Drives": "Drives",
    "Passing": "Passes, potential assists, assist points",
    "CatchShoot": "Catch & shoot",
    "PullUpShot": "Pull-up shooting",
    "Rebounding": "Rebounding chances / contested boards",
    "Defense": "Defensive impact (opp FG% at rim etc.)",
    "Efficiency": "Scoring efficiency by type",
    "ElbowTouch": "Elbow touches",
    "PostTouch": "Post touches",
    "PaintTouch": "Paint touches",
}

SEASON_TYPES = ("Regular Season", "Playoffs", "PlayIn", "Pre Season")

# Tracking data for completed games doesn't change, so cache it ~forever. Season-to-date
# tables change nightly.
TTL_FOREVER = None
TTL_SEASON = 6 * 3600


class NBAStatsBlocked(RuntimeError):
    """Raised when NBA.com drops or refuses the connection (usually an IP block)."""


def current_season(today: date | None = None) -> str:
    """'2026-27' style season string. The NBA season rolls over in October."""
    today = today or date.today()
    start = today.year if today.month >= 10 else today.year - 1
    return f"{start}-{str(start + 1)[-2:]}"


def result_set(payload: dict[str, Any], index: int = 0) -> pd.DataFrame:
    """Turn one NBA.com resultSet into a DataFrame."""
    sets = payload.get("resultSets") or payload.get("resultSet")
    if isinstance(sets, dict):  # a few endpoints return a single object
        sets = [sets]
    rs = sets[index]
    return pd.DataFrame(rs["rowSet"], columns=rs["headers"])


def parse_game_date(value: str) -> date:
    """NBA.com uses several formats: '2026-11-16T00:00:00', 'NOV 16, 2026', '11/16/2026'."""
    value = value.strip()
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d", "%b %d, %Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(value.title() if "," in value else value, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"Unrecognised game date: {value!r}")


class NBAStatsClient:
    def __init__(
        self,
        cache: ResponseCache | None = None,
        session: requests.Session | None = None,
        min_interval: float = 0.8,
        timeout: float = 30.0,
        retries: int = 3,
    ):
        self.cache = cache or ResponseCache()
        self.session = session or requests.Session()
        self.session.headers.update(HEADERS)
        self.min_interval = min_interval
        self.timeout = timeout
        self.retries = retries
        self._last_request = 0.0

    # ------------------------------------------------------------------ transport

    def get(self, endpoint: str, params: dict[str, Any], ttl: float | None = TTL_SEASON) -> dict:
        key = ResponseCache.make_key("nba_stats", endpoint, params)
        cached = self.cache.get(key, ttl)
        if cached is not None:
            return cached

        url = f"{BASE_URL}/{endpoint}"
        last_err: Exception | None = None
        for attempt in range(self.retries):
            wait = self.min_interval - (time.monotonic() - self._last_request)
            if wait > 0:
                time.sleep(wait)
            self._last_request = time.monotonic()
            try:
                resp = self.session.get(url, params=params, timeout=self.timeout)
            except (requests.ConnectionError, requests.Timeout) as err:
                last_err = err
                time.sleep(2 ** attempt)
                continue
            if resp.status_code in (429, 500, 502, 503, 504):
                last_err = RuntimeError(f"HTTP {resp.status_code} from {endpoint}")
                time.sleep(2 ** (attempt + 1))
                continue
            resp.raise_for_status()
            body = resp.json()
            self.cache.set(key, body)
            return body

        raise NBAStatsBlocked(
            f"Could not reach stats.nba.com/{endpoint} after {self.retries} tries ({last_err}). "
            "NBA.com drops connections from most cloud servers and many VPNs; "
            "run this from a home/office network."
        )

    # ------------------------------------------------------------------ endpoints

    def team_game_log(
        self, team: Team | str, season: str | None = None, season_type: str = "Regular Season"
    ) -> pd.DataFrame:
        """One row per game played. Columns include GAME_ID, GAME_DATE, MATCHUP, WL, PTS."""
        team = team if isinstance(team, Team) else find_team(team)
        payload = self.get(
            "teamgamelogs",
            {
                "TeamID": team.id,
                "Season": season or current_season(),
                "SeasonType": season_type,
                "LeagueID": "00",
            },
        )
        df = result_set(payload)
        df["GAME_DATE"] = df["GAME_DATE"].map(parse_game_date)
        return df.sort_values("GAME_DATE").reset_index(drop=True)

    def tracking(
        self,
        measure: str = "Possessions",
        season: str | None = None,
        season_type: str = "Regular Season",
        player_or_team: str = "Team",
        per_mode: str = "PerGame",
        date_from: date | None = None,
        date_to: date | None = None,
        team: Team | str | None = None,
        opponent: Team | str | None = None,
        last_n_games: int = 0,
        location: str = "",
        outcome: str = "",
    ) -> pd.DataFrame:
        """leaguedashptstats: the nba.com/stats 'Tracking' tables, with every filter exposed."""
        if measure not in TRACKING_MEASURES:
            raise ValueError(f"measure must be one of {sorted(TRACKING_MEASURES)}")
        team_id = (team if isinstance(team, Team) else find_team(team)).id if team else 0
        opp_id = (opponent if isinstance(opponent, Team) else find_team(opponent)).id if opponent else 0
        fmt = lambda d: d.strftime("%m/%d/%Y") if d else ""  # noqa: E731
        params = {
            "College": "", "Conference": "", "Country": "", "Division": "", "DraftPick": "",
            "DraftYear": "", "GameScope": "", "Height": "", "PlayerExperience": "",
            "PlayerPosition": "", "SeasonSegment": "", "StarterBench": "", "VsConference": "",
            "VsDivision": "", "Weight": "", "Month": 0, "PORound": 0, "LeagueID": "00",
            "DateFrom": fmt(date_from), "DateTo": fmt(date_to),
            "LastNGames": last_n_games, "Location": location, "Outcome": outcome,
            "OpponentTeamID": opp_id, "TeamID": team_id,
            "PerMode": per_mode, "PlayerOrTeam": player_or_team, "PtMeasureType": measure,
            "Season": season or current_season(), "SeasonType": season_type,
        }
        # A window that ended before today is frozen; cache it permanently.
        ttl = TTL_FOREVER if date_to and date_to < date.today() else TTL_SEASON
        df = result_set(self.get("leaguedashptstats", params, ttl=ttl))
        if team_id and "TEAM_ID" in df.columns:
            df = df[df["TEAM_ID"] == team_id]
        return df.reset_index(drop=True)

    # ------------------------------------------------------------------ composite queries

    def team_tracking_by_game(
        self,
        team: Team | str,
        measure: str = "Possessions",
        season: str | None = None,
        season_type: str = "Regular Season",
    ) -> pd.DataFrame:
        """Tracking stats for every game a team has played.

        NBA.com has no per-game tracking log, so we pull the team's game log and then query
        leaguedashptstats once per game with DateFrom == DateTo == game date.
        """
        team = team if isinstance(team, Team) else find_team(team)
        games = self.team_game_log(team, season, season_type)
        rows = []
        for g in games.itertuples(index=False):
            day = self.tracking(
                measure=measure, season=season, season_type=season_type,
                date_from=g.GAME_DATE, date_to=g.GAME_DATE, team=team,
            )
            if day.empty:  # tracking not published yet (e.g. game finished minutes ago)
                continue
            row = day.iloc[0].to_dict()
            row.update(GAME_ID=g.GAME_ID, GAME_DATE=g.GAME_DATE, MATCHUP=g.MATCHUP, WL=g.WL)
            rows.append(row)
        if not rows:
            return pd.DataFrame()
        df = pd.DataFrame(rows)
        lead = ["GAME_DATE", "MATCHUP", "WL"]
        return df[lead + [c for c in df.columns if c not in lead]]
