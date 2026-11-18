"""Offline tests for the Basketball Reference and PBP Stats connectors.
Fixtures mimic each site's real markup/JSON shape; numbers are made up."""

from datetime import date

import pytest

from hub.cache import ResponseCache
from hub.sources.bbref import BBRefClient, parse_tables, season_end_year
from hub.sources.pbpstats import PBPStatsClient
from hub.teams import find_team

BBREF_GAMELOG = """<html><body>
<table id="team_game_log_reg" class="stats_table"><thead><tr><th>Rk</th></tr></thead><tbody>
<tr><th data-stat="ranker">1</th><td data-stat="date"><a href="/boxscores/202610210BRK.html">2026-10-21</a></td>
 <td data-stat="game_location">@</td><td data-stat="opp_name_abbr"><a href="/teams/BRK/2027.html">BRK</a></td>
 <td data-stat="team_game_result">W</td><td data-stat="team_game_score">110</td><td data-stat="fg3_pct">.313</td></tr>
<tr class="thead"><th data-stat="ranker">Rk</th><td data-stat="date">Date</td></tr>
<tr><th data-stat="ranker">2</th><td data-stat="date"><a href="/boxscores/202610230CHO.html">2026-10-23</a></td>
 <td data-stat="game_location"></td><td data-stat="opp_name_abbr"><a href="/teams/ATL/2027.html">ATL</a></td>
 <td data-stat="team_game_result">L</td><td data-stat="team_game_score">81</td><td data-stat="fg3_pct">.250</td></tr>
</tbody></table>
<div id="all_hidden"><!--
<table id="advanced"><tbody><tr><th data-stat="ranker">1</th>
 <td data-stat="name_display"><a href="/players/d/diabamo01.html">Moussa Diabaté</a></td>
 <td data-stat="bpm">0.6</td></tr></tbody></table>
--></div></body></html>"""


class FakeResponse:
    def __init__(self, body, text=False):
        self._body, self.status_code, self.encoding = body, 200, "utf-8"
        self.text = body if text else None

    def json(self):
        return self._body

    def raise_for_status(self):
        pass


class FakeSession:
    def __init__(self, routes):
        self.headers, self.routes, self.calls = {}, routes, []

    def get(self, url, params, timeout):
        self.calls.append((url, dict(params)))
        for fragment, body in self.routes.items():
            if fragment in url:
                return FakeResponse(body, text=isinstance(body, str))
        raise AssertionError(f"unexpected url {url}")


def cache(tmp_path):
    return ResponseCache(tmp_path / "c.sqlite")


# ---------------------------------------------------------------- Basketball Reference


def test_parse_tables_handles_comments_links_and_header_rows():
    tables = parse_tables(BBREF_GAMELOG)
    assert set(tables) == {"team_game_log_reg", "advanced"}  # commented table found
    gl = tables["team_game_log_reg"]
    assert len(gl) == 2  # repeated header row dropped
    assert list(gl["fg3_pct"]) == [0.313, 0.25]
    assert list(gl["opp_name_abbr_id"]) == ["BRK", "ATL"]  # team links -> abbreviation
    adv = tables["advanced"]
    assert adv.loc[0, "name_display"] == "Moussa Diabaté"
    assert adv.loc[0, "name_display_id"] == "diabamo01"


def test_bbref_team_game_log_uses_bbref_codes_and_end_year(tmp_path):
    session = FakeSession({"/teams/CHO/2027/gamelog/": BBREF_GAMELOG})
    df = BBRefClient(cache(tmp_path), session, min_interval=0).team_game_log("Hornets", "2026-27")
    assert list(df["date"]) == [date(2026, 10, 21), date(2026, 10, 23)]
    assert list(df["game_location"]) == ["@", "vs"]
    assert season_end_year("2026-27") == 2027
    assert find_team("Nets").bbref_abbr == "BRK" and find_team("Suns").bbref_abbr == "PHO"


# ---------------------------------------------------------------- PBP Stats


def test_pbp_game_logs_sorted_and_team_id_passed(tmp_path):
    rows = [{"Date": "2026-10-23", "Opponent": "ATL", "SecondsPerPossOff": 15.6},
            {"Date": "2026-10-21", "Opponent": "BKN", "SecondsPerPossOff": 15.1}]
    session = FakeSession({"get-game-logs": {"multi_row_table_data": rows}})
    df = PBPStatsClient(cache(tmp_path), session, min_interval=0).game_logs(
        team="CHA", season="2026-27")
    assert list(df["Opponent"]) == ["BKN", "ATL"]
    params = session.calls[0][1]
    assert params["EntityType"] == "Team" and params["EntityId"] == 1610612766


def test_pbp_game_logs_requires_one_entity(tmp_path):
    c = PBPStatsClient(cache(tmp_path), FakeSession({}), min_interval=0)
    with pytest.raises(ValueError):
        c.game_logs()
    with pytest.raises(ValueError):
        c.game_logs(team="CHA", player_id=1)


def test_pbp_on_off_converts_strings_and_adds_per_48(tmp_path):
    results = [
        {"Name": "A", "On": "15.000", "Off": "-155.000", "On-Off": "170.000",
         "MinutesOn": 240, "MinutesOff": 384},
        {"Name": "B", "On": "-66.000", "Off": "-74.000", "On-Off": "8.000",
         "MinutesOn": 336, "MinutesOff": 288},
        {"Name": "C", "On": "-2.000", "Off": "-138.000", "On-Off": "9.000",
         "MinutesOn": 6, "MinutesOff": 618},
    ]
    session = FakeSession({"get-on-off": {"results": results, "headers": []}})
    df = PBPStatsClient(cache(tmp_path), session, min_interval=0).on_off("CHA", season="2026-27")
    assert df["On-Off"].dtype.kind == "f"
    a = df[df["Name"] == "A"].iloc[0]
    assert a["On/48"] == 3.0 and a["Off/48"] == pytest.approx(-19.4) and a["Net/48"] == 22.4
    assert list(df["Name"]) == sorted(df["Name"], key=lambda n: -df.set_index("Name")
                                      .loc[n, "Net/48"])


def test_pbp_rate_stat_has_no_per_48(tmp_path):
    results = [{"Name": "A", "On": "98.1", "Off": "96.0", "On-Off": "2.1",
                "MinutesOn": 200, "MinutesOff": 400}]
    session = FakeSession({"get-on-off": {"results": results, "headers": []}})
    df = PBPStatsClient(cache(tmp_path), session, min_interval=0).on_off(
        "CHA", stat="Pace", season="2026-27")
    assert "Net/48" not in df.columns
