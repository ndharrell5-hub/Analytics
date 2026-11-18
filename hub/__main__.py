"""Command line entry point:  python -m hub <command> ...

Examples
  python -m hub by-game Hornets --stat AVG_SEC_PER_TOUCH
  python -m hub by-game CHA --measure Drives --stat DRIVES,DRIVE_PTS --csv hornets_drives.csv
  python -m hub season --measure Possessions --stat AVG_SEC_PER_TOUCH --sort
  python -m hub measures
"""

from __future__ import annotations

import argparse
import sys

import pandas as pd

from hub.sources.nba_stats import (
    TRACKING_MEASURES,
    NBAStatsBlocked,
    NBAStatsClient,
    current_season,
)
from hub.teams import find_team


def _stats(arg: str | None) -> list[str]:
    return [s.strip().upper() for s in arg.split(",")] if arg else []


def _check_columns(df: pd.DataFrame, stats: list[str]) -> None:
    missing = [s for s in stats if s not in df.columns]
    if missing:
        numeric = [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c])]
        sys.exit(f"Unknown stat(s) {missing}. Available: {', '.join(numeric)}")


def cmd_by_game(args: argparse.Namespace, client: NBAStatsClient) -> None:
    team = find_team(args.team)
    season = args.season or current_season()
    print(f"Fetching {team.full_name} {args.measure} tracking, {season} {args.season_type}...",
          file=sys.stderr)
    df = client.team_tracking_by_game(team, args.measure, season, args.season_type)
    if df.empty:
        sys.exit("No games found.")
    stats = _stats(args.stat)
    _check_columns(df, stats)
    shown = df[["GAME_DATE", "MATCHUP", "WL"] + stats] if stats else df

    print(f"\n{team.full_name} — {args.measure} by game ({season} {args.season_type})\n")
    print(shown.to_string(index=False))

    if stats:
        season_row = client.tracking(args.measure, season, args.season_type, team=team)
        print(f"\nGames: {len(df)}")
        for s in stats:
            line = f"{s}: mean of games = {df[s].mean():.2f}"
            if not season_row.empty and s in season_row.columns:
                line += f" | NBA.com season figure = {season_row.iloc[0][s]:.2f}"
            print(line)
    if args.csv:
        df.to_csv(args.csv, index=False)
        print(f"\nSaved full table to {args.csv}", file=sys.stderr)


def cmd_season(args: argparse.Namespace, client: NBAStatsClient) -> None:
    season = args.season or current_season()
    df = client.tracking(args.measure, season, args.season_type,
                         player_or_team="Player" if args.players else "Team",
                         team=args.team)
    stats = _stats(args.stat)
    _check_columns(df, stats)
    name_col = "PLAYER_NAME" if args.players else "TEAM_NAME"
    shown = df[[name_col] + stats] if stats else df
    if stats and args.sort:
        shown = shown.sort_values(stats[0], ascending=False)
    print(shown.to_string(index=False))
    if args.csv:
        df.to_csv(args.csv, index=False)


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="python -m hub", description="NBA analytics research hub")
    sub = p.add_subparsers(dest="cmd", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--measure", default="Possessions", choices=sorted(TRACKING_MEASURES))
    common.add_argument("--stat", help="comma-separated columns, e.g. AVG_SEC_PER_TOUCH,TOUCHES")
    common.add_argument("--season", help="e.g. 2026-27 (default: current)")
    common.add_argument("--season-type", default="Regular Season")
    common.add_argument("--csv", help="write the full table to this CSV file")

    g = sub.add_parser("by-game", parents=[common], help="a team's tracking stats in every game")
    g.add_argument("team", help="CHA, Hornets, Charlotte, ...")

    s = sub.add_parser("season", parents=[common], help="season-to-date tracking table")
    s.add_argument("--team", help="limit to one team")
    s.add_argument("--players", action="store_true", help="player rows instead of team rows")
    s.add_argument("--sort", action="store_true", help="sort by the first --stat, descending")

    sub.add_parser("measures", help="list tracking measure types")

    args = p.parse_args(argv)
    if args.cmd == "measures":
        for k, v in TRACKING_MEASURES.items():
            print(f"{k:14} {v}")
        return

    client = NBAStatsClient()
    try:
        {"by-game": cmd_by_game, "season": cmd_season}[args.cmd](args, client)
    except NBAStatsBlocked as err:
        sys.exit(f"\n{err}")


if __name__ == "__main__":
    main()
