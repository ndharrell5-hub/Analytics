# NBA Analytics Research Hub

A research tool for answering specific NBA questions from respected data sources.

**Status:** Step 1. NBA.com stats connector with caching, rate limiting, and a CLI.
Planned: Basketball Reference, PBP Stats, Dunks & Threes, then a plain-English question layer.

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

> **Run on a home or office network.** NBA.com drops connections from cloud servers and many
> VPNs. If you're blocked, you'll see a "Could not reach stats.nba.com" message after ~90s.

## Examples

Hornets average seconds per touch, every game this season:

```bash
python -m hub by-game Hornets --stat AVG_SEC_PER_TOUCH
```

Multiple stats, saved to CSV:

```bash
python -m hub by-game CHA --stat AVG_SEC_PER_TOUCH,TOUCHES,AVG_DRIB_PER_TOUCH --csv hornets.csv
```

Other tracking categories (drives, catch & shoot, passing, rim defense, ...):

```bash
python -m hub by-game CHA --measure Drives --stat DRIVES,DRIVE_PTS,DRIVE_FG_PCT
```

League-wide table, sorted:

```bash
python -m hub season --measure Possessions --stat AVG_SEC_PER_TOUCH --sort
```

Players instead of teams:

```bash
python -m hub season --players --team CHA --measure Possessions --stat TOUCHES,AVG_SEC_PER_TOUCH --sort
```

List measure types (`python -m hub measures`). Pass a bad `--stat` to see every available column.

## How it works

NBA.com has no per-game tracking log, so `by-game` pulls the team's game log
(`teamgamelogs`), then calls `leaguedashptstats` once per game with `DateFrom = DateTo =`
the game date. Responses are cached in `.cache/responses.sqlite`: finished games are cached
permanently, season-to-date tables for 6 hours. A second run is instant.

The "mean of games" is a simple average of per-game values. The "NBA.com season figure"
is NBA.com's own season number, which weights by touches, so the two differ slightly.

## Tests

```bash
python -m pytest -q
```

Tests run offline against synthetic responses in NBA.com's real format.
