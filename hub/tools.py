"""Data tools the question layer (hub/ask.py) lets Claude call.

Each tool is a plain function returning compact text (a CSV table plus a summary), so tables stay
small in Claude's context and the functions can be tested without the API. Errors the model can
fix (bad column, unknown team, blocked source) raise ToolError, which goes back to Claude as an
error result it can react to.
"""

from __future__ import annotations

import functools
from functools import lru_cache
from typing import Literal

import pandas as pd
from anthropic.lib.tools import ToolError

from hub.http import SourceUnavailable
from hub.sources.bbref import LEAGUE_PAGES, TEAM_PAGE_TABLES, BBRefClient
from hub.sources.nba_stats import TRACKING_MEASURES, NBAStatsClient
from hub.sources.pbpstats import PBPStatsClient
from hub.teams import find_team

MAX_ROWS = 60

Measure = Literal["SpeedDistance", "Possessions", "Drives", "Passing", "CatchShoot",
                  "PullUpShot", "Rebounding", "Defense", "Efficiency", "ElbowTouch",
                  "PostTouch", "PaintTouch"]
TeamTable = Literal["roster", "team_and_opponent", "team_misc", "per_game_stats", "totals_stats",
                    "per_minute_stats", "per_poss", "advanced", "adj_shooting", "shooting",
                    "pbp_stats", "salaries2", "injuries"]
LeagueKind = Literal["per_game", "totals", "per_minute", "per_poss", "advanced", "play_by_play",
                     "shooting", "adj_shooting"]
Entity = Literal["Team", "Player", "Lineup"]

assert set(Measure.__args__) == set(TRACKING_MEASURES)
assert set(TeamTable.__args__) == set(TEAM_PAGE_TABLES)
assert set(LeagueKind.__args__) == set(LEAGUE_PAGES)


@lru_cache(maxsize=1)
def nba() -> NBAStatsClient:
    return NBAStatsClient()


@lru_cache(maxsize=1)
def bbref() -> BBRefClient:
    return BBRefClient()


@lru_cache(maxsize=1)
def pbp() -> PBPStatsClient:
    return PBPStatsClient()


# ---------------------------------------------------------------------- formatting


def _columns_help(df: pd.DataFrame) -> str:
    numeric = [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c])]
    other = [c for c in df.columns if c not in numeric]
    return f"Numeric columns: {', '.join(numeric)}\nOther columns: {', '.join(other)}"


def _resolve(df: pd.DataFrame, names: list[str] | None) -> list[str]:
    lookup = {c.lower(): c for c in df.columns}
    out, missing = [], []
    for n in names or []:
        (out if n.lower() in lookup else missing).append(lookup.get(n.lower(), n))
    if missing:
        raise ToolError(f"Unknown column(s) {missing}.\n{_columns_help(df)}")
    return out


def render(
    df: pd.DataFrame,
    source: str,
    id_cols: list[str],
    stats: list[str] | None,
    sort_by: str | None = None,
    ascending: bool = False,
    limit: int | None = None,
    summarize: bool = False,
) -> str:
    """Select columns, sort, cap rows, and format as CSV text with an optional summary."""
    if df.empty:
        return f"[{source}] No rows returned."
    if not stats:
        return (f"[{source}] {len(df)} rows. Call again with `stats` set to the columns "
                f"you need.\n{_columns_help(df)}")
    stats = _resolve(df, stats)
    sort_col = _resolve(df, [sort_by])[0] if sort_by else None
    ids = [c for c in id_cols if c in df.columns and c not in stats]
    out = df[ids + stats + ([sort_col] if sort_col and sort_col not in ids + stats else [])]
    if sort_col:
        out = out.sort_values(sort_col, ascending=ascending, na_position="last")
    total = len(out)
    out = out.head(min(limit or MAX_ROWS, MAX_ROWS))

    text = [f"[{source}] {total} rows" + (f", showing {len(out)}" if len(out) < total else "")]
    text.append(out.to_csv(index=False, float_format="%.4g").strip())
    if summarize:
        lines = []
        for s in stats:
            col = pd.to_numeric(df[s], errors="coerce")
            if col.notna().any():
                lines.append(f"{s}: mean {col.mean():.4g}, min {col.min():.4g}, "
                             f"max {col.max():.4g}, n={int(col.notna().sum())}")
        if lines:
            text.append("Summary (simple average across rows):\n" + "\n".join(lines))
    return "\n\n".join(text)


def _guard(fn):
    """Turn source/lookup failures into ToolErrors Claude can read and recover from."""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except ToolError:
            raise
        except SourceUnavailable as err:
            raise ToolError(f"SOURCE UNAVAILABLE: {err}") from err
        except (ValueError, KeyError) as err:
            raise ToolError(str(err)) from err
    return wrapper


def _min_filter(df: pd.DataFrame, col_options: tuple[str, ...], minimum: float) -> pd.DataFrame:
    if not minimum:
        return df
    col = next((c for c in col_options if c in df.columns), None)
    return df[pd.to_numeric(df[col], errors="coerce") >= minimum] if col else df


# ---------------------------------------------------------------------- NBA.com


@_guard
def nba_team_tracking_by_game(team: str, measure: Measure = "Possessions",
                              stats: list[str] | None = None, season: str | None = None) -> str:
    """NBA.com player-tracking stats for every game a team has played this season, one row per game.

    The only source for tracking data: touches, seconds and dribbles per touch, time of possession,
    drives, passes and potential assists, catch-and-shoot, pull-ups, speed/distance, rebound
    chances, rim defense, elbow/post/paint touches. Also returns NBA.com's official season figure.
    Can fail if NBA.com blocks the user's network; say so if it does.

    Args:
        team: Team name, city, or abbreviation, e.g. "Hornets", "CHA".
        measure: Tracking category. Possessions has TOUCHES, AVG_SEC_PER_TOUCH, AVG_DRIB_PER_TOUCH, TIME_OF_POSS.
        stats: Column names to return, e.g. ["AVG_SEC_PER_TOUCH"]. Omit to list available columns.
        season: Season like "2025-26". Omit for the current season.
    """
    t = find_team(team)
    df = nba().team_tracking_by_game(t, measure, season)
    text = render(df, f"NBA.com tracking/{measure}, {t.full_name}, by game",
                  ["GAME_DATE", "MATCHUP", "WL"], stats, summarize=True)
    if stats and not df.empty:
        season_row = nba().tracking(measure, season, team=t)
        cols = [c for c in _resolve(df, stats) if c in season_row.columns]
        if not season_row.empty and cols:
            vals = ", ".join(f"{c} {season_row.iloc[0][c]:.4g}" for c in cols)
            text += f"\n\nNBA.com official season figure: {vals}"
    return text


@_guard
def nba_tracking_table(measure: Measure = "Possessions", players: bool = False,
                       team: str | None = None, stats: list[str] | None = None,
                       sort_by: str | None = None, ascending: bool = False, limit: int = 15,
                       min_games: int = 0, season: str | None = None) -> str:
    """NBA.com season-to-date tracking table for all teams, or for players (optionally one team's).

    Use for league rankings or comparisons of tracking stats, e.g. which teams have the most
    seconds per touch, or the Hornets' players' drives per game. Values are per game.

    Args:
        measure: Tracking category (see nba_team_tracking_by_game).
        players: True for player rows, False for team rows.
        team: Limit to one team.
        stats: Columns to return. Omit to list available columns.
        sort_by: Column to sort by.
        ascending: Sort ascending instead of descending.
        limit: Max rows to return.
        min_games: Drop rows with fewer games played (GP) than this.
        season: Season like "2025-26". Omit for current.
    """
    df = nba().tracking(measure, season, player_or_team="Player" if players else "Team",
                        team=team)
    df = _min_filter(df, ("GP",), min_games)
    ids = ["PLAYER_NAME", "TEAM_ABBREVIATION", "GP"] if players else ["TEAM_NAME", "GP"]
    return render(df, f"NBA.com tracking/{measure} season table", ids, stats, sort_by,
                  ascending, limit)


# ---------------------------------------------------------------------- Basketball Reference


@_guard
def bbref_team_game_log(team: str, advanced: bool = False, stats: list[str] | None = None,
                        season: str | None = None) -> str:
    """Basketball Reference team game log: one row per game with result and score.

    Basic log: box-score totals for the team and opponent (fg, fga, fg3_pct, orb, ast, tov...;
    opponent columns start with opp_). Advanced log: team_off_rtg, team_def_rtg, pace, ts_pct,
    four factors (efg_pct, team_tov_pct, team_orb_pct, ft_rate and opp_ versions).

    Args:
        team: Team name, city, or abbreviation.
        advanced: True for the advanced (ratings/pace/four factors) log.
        stats: Columns to return. Omit to list available columns.
        season: Season like "2025-26". Omit for current.
    """
    t = find_team(team)
    df = bbref().team_game_log(t, season, advanced=advanced)
    kind = "advanced game log" if advanced else "game log"
    return render(df, f"Basketball Reference {kind}, {t.full_name}",
                  ["date", "game_location", "opp_name_abbr", "team_game_result",
                   "team_game_score", "opp_team_game_score"], stats, summarize=True)


@_guard
def bbref_team_table(team: str, table: TeamTable, stats: list[str] | None = None,
                     sort_by: str | None = None, ascending: bool = False,
                     limit: int = 25, season: str | None = None) -> str:
    """A table from a team's Basketball Reference season page, mostly one row per player.

    advanced: PER, ts_pct, usg_pct, ws, ws_per_48, bpm, obpm, dbpm, vorp. per_game_stats /
    totals_stats / per_minute_stats / per_poss: box-score rates. shooting: FG% and attempt share
    by distance. pbp_stats: position estimates and on-court +/-. team_misc: team ORtg/DRtg/pace/
    four factors with league ranks. roster, salaries2, injuries also available.

    Args:
        team: Team name, city, or abbreviation.
        table: Which table.
        stats: Columns to return. Omit to list available columns.
        sort_by: Column to sort by.
        ascending: Sort ascending instead of descending.
        limit: Max rows.
        season: Season like "2025-26". Omit for current.
    """
    t = find_team(team)
    df = bbref().team_page_table(t, table, season)
    return render(df, f"Basketball Reference {table}, {t.full_name}",
                  ["name_display", "player", "team_name", "pos", "games", "mp"],
                  stats, sort_by, ascending, limit)


@_guard
def bbref_player_game_log(player: str, stats: list[str] | None = None,
                          season: str | None = None) -> str:
    """A player's Basketball Reference game log (box score each game, including DNPs/inactive).

    Columns include mp, pts, trb, ast, stl, blk, tov, fg, fga, fg_pct, fg3, fg3a, fg3_pct, ft,
    fta, plus_minus, game_score, is_starter.

    Args:
        player: Player name (partial is fine if unique), e.g. "Brandon Miller".
        stats: Columns to return. Omit to list available columns.
        season: Season like "2025-26". Omit for current.
    """
    hits = bbref().find_player(player, season)
    if hits.empty:
        raise ToolError(f"No player matching {player!r} found in this season's stats.")
    if len(hits) > 1:
        raise ToolError("Several players match; use a fuller name:\n"
                        + hits.to_csv(index=False))
    pid, name = hits.iloc[0]["name_display_id"], hits.iloc[0]["name_display"]
    df = bbref().player_game_log(pid, season)
    return render(df, f"Basketball Reference game log, {name}",
                  ["date", "team_name_abbr", "game_location", "opp_name_abbr", "game_result",
                   "is_starter", "mp"], stats, summarize=True)


@_guard
def bbref_league_players(kind: LeagueKind = "per_game", stats: list[str] | None = None,
                         sort_by: str | None = None, ascending: bool = False,
                         min_minutes: int = 0, limit: int = 15,
                         season: str | None = None) -> str:
    """League-wide Basketball Reference player table, for leaderboards and comparisons.

    Use min_minutes to drop small samples when ranking (e.g. 200+ minutes early season).

    Args:
        kind: per_game, totals, per_minute, per_poss, advanced (PER/BPM/WS/VORP), play_by_play, shooting, adj_shooting.
        stats: Columns to return. Omit to list available columns.
        sort_by: Column to sort by.
        ascending: Sort ascending instead of descending.
        min_minutes: Drop players with fewer total minutes than this.
        limit: Max rows.
        season: Season like "2025-26". Omit for current.
    """
    df = bbref().league_players(kind, season)
    if min_minutes:
        mp = "mp" if "mp" in df.columns else None
        if mp is None and {"mp_per_g", "games"} <= set(df.columns):
            df = df.assign(_total_mp=pd.to_numeric(df["mp_per_g"], errors="coerce")
                           * pd.to_numeric(df["games"], errors="coerce"))
            mp = "_total_mp"
        if mp:
            df = df[pd.to_numeric(df[mp], errors="coerce") >= min_minutes]
    return render(df, f"Basketball Reference league {kind}",
                  ["name_display", "team_name_abbr", "pos", "games", "mp"],
                  stats, sort_by, ascending, limit)


# ---------------------------------------------------------------------- PBP Stats


@_guard
def pbp_game_log(team: str | None = None, player: str | None = None,
                 stats: list[str] | None = None, season: str | None = None) -> str:
    """PBP Stats possession-level game log for a team or a player, one row per game.

    Unique stats: SecondsPerPossOff/Def (pace of each possession), ShotQualityAvg (expected eFG
    of shots taken), Pace, OffPoss, TsPct, EfgPct, shot-zone frequency and accuracy (AtRim,
    ShortMidRange, LongMidRange, Corner3, Arc3 + Frequency/Accuracy/PctAssisted),
    SecondChancePoints, PenaltyPoints, LiveBallTurnovers, assisted/unassisted splits.

    Args:
        team: Team name or abbreviation (for a team log, or to disambiguate a player).
        player: Player name for a player log.
        stats: Columns to return. Omit to list available columns.
        season: Season like "2025-26". Omit for current.
    """
    if player:
        hits = pbp().find_player(player, team=team, season=season)
        if len(hits) != 1:
            raise ToolError(f"Need exactly one player matching {player!r}; found {len(hits)}."
                            + (f"\n{hits.to_csv(index=False)}" if len(hits) else ""))
        df = pbp().game_logs(player_id=hits.iloc[0]["EntityId"], season=season)
        label = hits.iloc[0]["Name"]
    elif team:
        t = find_team(team)
        df = pbp().game_logs(team=t, season=season)
        label = t.full_name
    else:
        raise ToolError("Give a team or a player.")
    return render(df, f"PBP Stats game log, {label}", ["Date", "Opponent", "Minutes"], stats,
                  summarize=True)


@_guard
def pbp_totals(entity: Entity = "Team", team: str | None = None,
               stats: list[str] | None = None, sort_by: str | None = None,
               ascending: bool = False, min_minutes: int = 0, limit: int = 30,
               season: str | None = None) -> str:
    """PBP Stats season totals/rates for all teams, players, or five-man lineups.

    Same stat columns as pbp_game_log, aggregated over the season. Lineups need a team; their
    Name lists the five players. Use min_minutes to drop small samples.

    Args:
        entity: Team, Player, or Lineup.
        team: Limit to one team (required for Lineup).
        stats: Columns to return. Omit to list available columns.
        sort_by: Column to sort by.
        ascending: Sort ascending instead of descending.
        min_minutes: Drop rows with fewer total Minutes than this.
        limit: Max rows.
        season: Season like "2025-26". Omit for current.
    """
    if entity == "Lineup" and not team:
        raise ToolError("Lineup totals need a team.")
    df = pbp().totals(entity, season, team=team)
    df = _min_filter(df, ("Minutes",), min_minutes)
    return render(df, f"PBP Stats {entity} totals", ["Name", "TeamAbbreviation", "GamesPlayed",
                                                     "Minutes"], stats, sort_by, ascending, limit)


@_guard
def pbp_on_off(team: str, compare: str = "PlusMinus", min_minutes: int = 0,
               season: str | None = None) -> str:
    """How a team performs with each of its players on vs. off the floor (PBP Stats).

    For counting stats (PlusMinus, Points, OpponentPoints, Rebounds...) it adds per-48-minute
    rates (On/48, Off/48, Net/48) and sorts by Net/48; prefer those over raw totals, which mostly
    reflect minutes played. Small MinutesOn samples are noisy; use min_minutes.

    Args:
        team: Team name or abbreviation.
        compare: Team stat to compare, e.g. PlusMinus, Points, OpponentPoints, Pace, TsPct.
        min_minutes: Drop players with fewer minutes on court than this.
        season: Season like "2025-26". Omit for current.
    """
    t = find_team(team)
    df = pbp().on_off(t, compare, season)
    if not df.empty:
        df = df[df["MinutesOn"] >= min_minutes]
    cols = [c for c in ("On", "Off", "On-Off", "MinutesOn", "MinutesOff", "On/48", "Off/48",
                        "Net/48") if c in df.columns]
    return render(df, f"PBP Stats on/off, {t.full_name}, team {compare}", ["Name"], cols)


TOOLS = [nba_team_tracking_by_game, nba_tracking_table, bbref_team_game_log, bbref_team_table,
         bbref_player_game_log, bbref_league_players, pbp_game_log, pbp_totals, pbp_on_off]
