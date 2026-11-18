# NBA Analytics Research Hub

A research tool for answering specific NBA questions from respected data sources.

| Source | What it's best for | Status |
|---|---|---|
| **NBA.com stats** | Player tracking (touches, sec/touch, drives, catch & shoot, rim defense, speed) | ✅ (run on a home network) |
| **Basketball Reference** | Box-score game logs, advanced game logs (ORtg/DRtg/pace/four factors), BPM/WS/VORP, player game logs | ✅ |
| **PBP Stats** | Possession data: seconds per possession, shot quality, lineups, on/off, shot zones, putbacks | ✅ |

Ask questions in plain English (`python -m hub ask`), or use the per-source commands directly.
Planned: Dunks & Threes (EPM).

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

For plain-English questions, you also need an Anthropic API key (console.anthropic.com):

```bash
export ANTHROPIC_API_KEY=sk-ant-...
```

> **NBA.com only works on a home or office network.** It drops connections from cloud servers
> and many VPNs. If you're blocked you'll see "Could not reach stats.nba.com" after ~90s.
> Basketball Reference and PBP Stats work anywhere.

## Ask in plain English

```bash
python -m hub ask "What has been the Hornets' avg sec per touch in every game this season?"
```

Or start an interactive session, where follow-up questions keep context:

```bash
python -m hub ask
```

Claude picks the source and the right stat columns, fetches the data on your machine, and
answers with the numbers, a table and the source. Each lookup is printed as it happens
(e.g. `→ PBP Stats game log (team=Hornets, ...)`), so you can see where every number came
from. If a source is unavailable (NBA.com off a home network), it says so rather than
substituting a different stat without telling you.

More examples:

- "Which Hornets lineups have played the most minutes, and how have they done?"
- "Rank the league's teams by seconds per possession on offense."
- "How has Brandon Miller shot from three over his last five games?"
- "Who are the top 10 players in BPM with at least 250 minutes?"
- "How do the Hornets do with Naz Reid on the floor versus off?"

**Model and cost:** uses Claude Opus 5.5 at medium effort, with prompt caching on the
instructions and tool definitions. A typical question makes 1–3 data lookups. To change the
model or effort, edit `MODEL` / `EFFORT` in `hub/ask.py`.

## Commands

Run `python -m hub --help` for the full list, or `python -m hub <command> --help`.

Every command takes `--stat` (comma-separated columns; a bad name lists every valid one),
`--season 2025-26`, and `--csv file.csv` (saves every column, not just the ones shown).
Table commands also take `--sort`, `--asc`, `--limit N` and `--min COL=VALUE`.

### NBA.com tracking

```bash
python -m hub by-game Hornets --stat AVG_SEC_PER_TOUCH
python -m hub by-game CHA --measure Drives --stat DRIVES,DRIVE_PTS,DRIVE_FG_PCT
python -m hub season --measure Possessions --stat AVG_SEC_PER_TOUCH --sort
python -m hub season --players --team CHA --stat TOUCHES,AVG_SEC_PER_TOUCH --sort
python -m hub measures
```

### Basketball Reference

```bash
python -m hub bbref-gamelog Hornets --stat fg3_pct,tov,orb
python -m hub bbref-gamelog Hornets --advanced --stat team_off_rtg,team_def_rtg,pace
python -m hub bbref-team CHA --table advanced --stat bpm,ws,usg_pct --sort
python -m hub bbref-player "Brandon Miller" --stat pts,trb,ast,fg3_pct
python -m hub bbref-league --kind advanced --stat bpm --sort --min mp=250 --limit 15
```

Team tables: `roster, team_and_opponent, team_misc, per_game_stats, totals_stats,
per_minute_stats, per_poss, advanced, adj_shooting, shooting, pbp_stats, salaries2, injuries`.
League kinds: `per_game, totals, per_minute, per_poss, advanced, play_by_play, shooting,
adj_shooting`.

### PBP Stats

```bash
python -m hub pbp-gamelog Hornets --stat SecondsPerPossOff,ShotQualityAvg,Pace
python -m hub pbp-gamelog --player "Brandon Miller" --stat Points,TsPct
python -m hub pbp-totals --entity Team --stat SecondsPerPossOff,Pace --sort --asc
python -m hub pbp-totals --entity Lineup --team CHA --stat Minutes,PlusMinus --sort --limit 10
python -m hub pbp-onoff CHA --compare PlusMinus --min-minutes 100
```

For counting stats (plus-minus, points, rebounds...), on/off adds per-48-minute rates
(`On/48`, `Off/48`, `Net/48`) and sorts by `Net/48`. Raw on/off totals mostly reflect how
many minutes someone played.

## How it works

- **Caching:** every response is stored in `.cache/responses.sqlite`. Finished games and past
  seasons are kept permanently; current-season tables refresh after 6 hours. Repeat
  questions are instant. Delete `.cache/` to force a refresh.
- **Politeness:** requests are spaced out per source (NBA.com 0.8s, PBP Stats 2s,
  Basketball Reference 3.5s, which keeps under its ~20 requests/minute limit). Retries
  back off and honor `Retry-After`.
- **NBA.com per-game tracking:** there's no per-game tracking log, so `by-game` pulls the
  team's game log, then queries `leaguedashptstats` once per game date.
- **Basketball Reference** columns are the site's own `data-stat` names. Linked cells also
  get an `_id` column (e.g. `name_display_id = millebr02`).
- **Team names:** anything works: `CHA`, `CHO`, `Hornets`, `Charlotte`, `Charlotte Hornets`.

## Terms of use

These are personal-research tools. Basketball Reference allows light personal use but
blocks bulk scraping. PBP Stats is a one-person project. Keep the caching and rate limits
in place.

## Tests

```bash
python -m pytest -q
```

Tests run offline against synthetic responses in each source's real format. The question
layer is tested with the real SDK tool runner against a scripted fake API, so no key is needed.
