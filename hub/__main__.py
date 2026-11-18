"""Command line entry point:  python -m hub <command> ...

NBA.com (tracking)
  python -m hub by-game Hornets --stat AVG_SEC_PER_TOUCH
  python -m hub season --measure Drives --stat DRIVES,DRIVE_PTS --sort
Basketball Reference
  python -m hub bbref-gamelog Hornets --advanced --stat team_off_rtg,team_def_rtg,pace
  python -m hub bbref-team CHA --table advanced --stat bpm,ws,usg_pct --sort
  python -m hub bbref-player "Brandon Miller" --stat pts,trb,ast,fg3_pct
  python -m hub bbref-league --kind advanced --stat bpm --sort --min mp=200 --limit 15
PBP Stats
  python -m hub pbp-gamelog Hornets --stat SecondsPerPossOff,ShotQualityAvg,Pace
  python -m hub pbp-gamelog --player "Brandon Miller" --stat Points,TsPct
  python -m hub pbp-totals --entity Team --stat SecondsPerPossOff,Pace --sort --asc
  python -m hub pbp-totals --entity Lineup --team CHA --stat Minutes,PlusMinus --sort
  python -m hub pbp-onoff CHA --compare PlusMinus --min-minutes 100
"""

from __future__ import annotations

import argparse
import sys

import pandas as pd

from hub.http import SourceUnavailable
from hub.sources.bbref import LEAGUE_PAGES, TEAM_PAGE_TABLES, BBRefClient
from hub.sources.nba_stats import TRACKING_MEASURES, NBAStatsClient, current_season
from hub.sources.pbpstats import ENTITY_TYPES, PBPStatsClient
from hub.teams import find_team

# ---------------------------------------------------------------------- output helpers


def _stats(arg: str | None) -> list[str]:
    return [s.strip() for s in arg.split(",")] if arg else []


def _resolve_columns(df: pd.DataFrame, stats: list[str]) -> list[str]:
    """Match requested stats to columns case-insensitively; exit with the options if not found."""
    lookup = {c.lower(): c for c in df.columns}
    resolved, missing = [], []
    for s in stats:
        (resolved if s.lower() in lookup else missing).append(lookup.get(s.lower(), s))
    if missing:
        numeric = [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c])]
        sys.exit(f"Unknown stat(s) {missing}.\nAvailable: {', '.join(numeric)}")
    return resolved


def _apply_min(df: pd.DataFrame, mins: list[str] | None) -> pd.DataFrame:
    """--min mp=200 --min games=5"""
    for m in mins or []:
        col, _, val = m.partition("=")
        (col,) = _resolve_columns(df, [col])
        df = df[pd.to_numeric(df[col], errors="coerce") >= float(val)]
    return df


def show(df: pd.DataFrame, id_cols: list[str], args: argparse.Namespace,
         title: str = "", summarize: bool = False) -> None:
    if df.empty:
        sys.exit("No rows returned.")
    stats = _resolve_columns(df, _stats(args.stat))
    df = _apply_min(df, getattr(args, "min", None))
    id_cols = [c for c in id_cols if c in df.columns and c not in stats]
    out = df[id_cols + stats] if stats else df
    if stats and getattr(args, "sort", False):
        out = out.sort_values(stats[0], ascending=getattr(args, "asc", False))
    if getattr(args, "limit", None):
        out = out.head(args.limit)
    if title:
        print(f"\n{title}\n")
    with pd.option_context("display.max_columns", None, "display.width", 200):
        print(out.to_string(index=False))
    if summarize and stats:
        print(f"\nGames: {len(df)}")
        for s in stats:
            if pd.api.types.is_numeric_dtype(df[s]):
                print(f"{s}: mean = {df[s].mean():.3f}  min = {df[s].min():.3f}  "
                      f"max = {df[s].max():.3f}")
    if args.csv:
        df.to_csv(args.csv, index=False)
        print(f"\nSaved full table ({len(df.columns)} columns) to {args.csv}", file=sys.stderr)


# ---------------------------------------------------------------------- NBA.com


def cmd_by_game(args):
    client = NBAStatsClient()
    team = find_team(args.team)
    season = args.season or current_season()
    print(f"Fetching {team.full_name} {args.measure} tracking, {season}...", file=sys.stderr)
    df = client.team_tracking_by_game(team, args.measure, season, args.season_type)
    show(df, ["GAME_DATE", "MATCHUP", "WL"], args,
         f"{team.full_name}: NBA.com {args.measure} tracking by game ({season})",
         summarize=True)
    stats = _resolve_columns(df, _stats(args.stat)) if not df.empty else []
    season_row = client.tracking(args.measure, season, args.season_type, team=team)
    for s in stats:
        if not season_row.empty and s in season_row.columns:
            print(f"{s}: NBA.com season figure = {season_row.iloc[0][s]:.2f}")


def cmd_season(args):
    players = args.players
    df = NBAStatsClient().tracking(args.measure, args.season, args.season_type,
                                   player_or_team="Player" if players else "Team",
                                   team=args.team)
    show(df, ["PLAYER_NAME", "TEAM_ABBREVIATION"] if players else ["TEAM_NAME"], args)


# ---------------------------------------------------------------------- Basketball Reference


def cmd_bbref_gamelog(args):
    team = find_team(args.team)
    df = BBRefClient().team_game_log(team, args.season, advanced=args.advanced,
                                     playoffs=args.playoffs)
    kind = "advanced game log" if args.advanced else "game log"
    show(df, ["date", "game_location", "opp_name_abbr", "team_game_result",
              "team_game_score", "opp_team_game_score"], args,
         f"{team.full_name}: Basketball Reference {kind}", summarize=True)


def cmd_bbref_team(args):
    team = find_team(args.team)
    df = BBRefClient().team_page_table(team, args.table, args.season)
    show(df, ["name_display", "player", "team_name", "pos", "games", "mp"], args,
         f"{team.full_name}: {TEAM_PAGE_TABLES.get(args.table, args.table)}")


def cmd_bbref_player(args):
    client = BBRefClient()
    if args.id:
        pid, label = args.name, args.name
    else:
        hits = client.find_player(args.name, args.season)
        if hits.empty:
            sys.exit(f"No player matching {args.name!r} this season.")
        if len(hits) > 1:
            print(hits.to_string(index=False), file=sys.stderr)
            sys.exit("\nMultiple matches; be more specific, or pass a Basketball Reference "
                     "id with --id.")
        pid, label = hits.iloc[0]["name_display_id"], hits.iloc[0]["name_display"]
    df = client.player_game_log(pid, args.season)
    show(df, ["date", "team_name_abbr", "game_location", "opp_name_abbr", "game_result",
              "is_starter", "mp"], args, f"{label}: game log", summarize=True)


def cmd_bbref_league(args):
    df = BBRefClient().league_players(args.kind, args.season)
    show(df, ["name_display", "team_name_abbr", "pos", "games", "mp"], args)


# ---------------------------------------------------------------------- PBP Stats


def cmd_pbp_gamelog(args):
    client = PBPStatsClient()
    if args.player:
        hits = client.find_player(args.player, team=args.team, season=args.season)
        if len(hits) != 1:
            print(hits.to_string(index=False) if len(hits) else "", file=sys.stderr)
            sys.exit(f"Need exactly one player matching {args.player!r}; found {len(hits)}.")
        df = client.game_logs(player_id=hits.iloc[0]["EntityId"], season=args.season,
                              season_type=args.season_type)
        title = f"{hits.iloc[0]['Name']}: PBP Stats game log"
    else:
        if not args.team:
            sys.exit("Give a team (e.g. Hornets) or --player NAME.")
        team = find_team(args.team)
        df = client.game_logs(team=team, season=args.season, season_type=args.season_type)
        title = f"{team.full_name}: PBP Stats game log"
    show(df, ["Date", "Opponent", "Minutes"], args, title, summarize=True)


def cmd_pbp_totals(args):
    df = PBPStatsClient().totals(args.entity, args.season, args.season_type, team=args.team)
    show(df, ["Name", "TeamAbbreviation", "GamesPlayed", "Minutes"], args,
         f"PBP Stats {args.entity} totals")


def cmd_pbp_onoff(args):
    team = find_team(args.team)
    df = PBPStatsClient().on_off(team, args.compare, args.season, args.season_type)
    df = df[df["MinutesOn"] >= args.min_minutes]
    show(df, ["Name"], args, f"{team.full_name}: team {args.compare} with each player on/off")


# ---------------------------------------------------------------------- parser


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m hub", description="NBA analytics research hub",
                                formatter_class=argparse.RawDescriptionHelpFormatter,
                                epilog=__doc__.split("\n", 2)[2])
    sub = p.add_subparsers(dest="cmd", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--stat", help="comma-separated columns to show (bad name lists them all)")
    common.add_argument("--season", help="e.g. 2026-27 (default: current)")
    common.add_argument("--season-type", default="Regular Season")
    common.add_argument("--csv", help="write the full table to this CSV file")

    table = argparse.ArgumentParser(add_help=False)
    table.add_argument("--sort", action="store_true", help="sort by the first --stat")
    table.add_argument("--asc", action="store_true", help="sort ascending")
    table.add_argument("--limit", type=int, help="show only the top N rows")
    table.add_argument("--min", action="append", metavar="COL=VALUE",
                       help="row filter, e.g. --min mp=200 (repeatable)")

    def add(name, help_, func, *parents):
        sp = sub.add_parser(name, parents=[common, *parents], help=help_)
        sp.set_defaults(func=func)
        return sp

    s = add("by-game", "NBA.com tracking stats for every game a team played", cmd_by_game)
    s.add_argument("team")
    s.add_argument("--measure", default="Possessions", choices=sorted(TRACKING_MEASURES))

    s = add("season", "NBA.com season-to-date tracking table", cmd_season, table)
    s.add_argument("--measure", default="Possessions", choices=sorted(TRACKING_MEASURES))
    s.add_argument("--team")
    s.add_argument("--players", action="store_true", help="player rows instead of team rows")

    s = add("bbref-gamelog", "Basketball Reference team game log", cmd_bbref_gamelog)
    s.add_argument("team")
    s.add_argument("--advanced", action="store_true", help="ORtg/DRtg/pace/four factors")
    s.add_argument("--playoffs", action="store_true")

    s = add("bbref-team", "a table from a team's Basketball Reference page", cmd_bbref_team, table)
    s.add_argument("team")
    s.add_argument("--table", default="per_game_stats", choices=sorted(TEAM_PAGE_TABLES))

    s = add("bbref-player", "a player's Basketball Reference game log", cmd_bbref_player)
    s.add_argument("name", help='player name, or a bbref id with --id (e.g. "millebr02")')
    s.add_argument("--id", action="store_true", help="treat NAME as a Basketball Reference id")

    s = add("bbref-league", "league-wide Basketball Reference player table", cmd_bbref_league,
            table)
    s.add_argument("--kind", default="per_game", choices=sorted(LEAGUE_PAGES))

    s = add("pbp-gamelog", "PBP Stats game log for a team or player", cmd_pbp_gamelog)
    s.add_argument("team", nargs="?", help="team (or the player's team, to disambiguate)")
    s.add_argument("--player", help="player name instead of a team game log")

    s = add("pbp-totals", "PBP Stats season totals for teams, players, or lineups",
            cmd_pbp_totals, table)
    s.add_argument("--entity", default="Team", choices=ENTITY_TYPES)
    s.add_argument("--team", help="limit to one team")

    s = add("pbp-onoff", "PBP Stats on/off splits for a team's players", cmd_pbp_onoff, table)
    s.add_argument("team")
    s.add_argument("--compare", default="PlusMinus",
                   help="team stat to compare on vs off (e.g. PlusMinus, Points, Pace)")
    s.add_argument("--min-minutes", type=int, default=0)

    sub.add_parser("measures", help="list NBA.com tracking measure types").set_defaults(
        func=lambda a: [print(f"{k:14} {v}") for k, v in TRACKING_MEASURES.items()])
    return p


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    try:
        args.func(args)
    except SourceUnavailable as err:
        sys.exit(f"\n{err}")


if __name__ == "__main__":
    main()
