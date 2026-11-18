"""Offline tests for the NBA.com client. Responses below are synthetic but use NBA.com's
real response shape and column names; the numbers are made up."""

from datetime import date

import pytest
import requests

from hub.cache import ResponseCache
from hub.sources.nba_stats import (
    NBAStatsBlocked,
    NBAStatsClient,
    current_season,
    parse_game_date,
)
from hub.teams import find_team

CHA = 1610612766
PT_HEADERS = ["TEAM_ID", "TEAM_ABBREVIATION", "TEAM_NAME", "GP", "W", "L", "MIN", "POINTS",
              "TOUCHES", "FRONT_CT_TOUCHES", "TIME_OF_POSS", "AVG_SEC_PER_TOUCH",
              "AVG_DRIB_PER_TOUCH", "PTS_PER_TOUCH"]

GAMES = {  # date -> (game id, matchup, W/L, sec per touch, touches)
    "10/22/2026": ("0022600005", "CHA vs. ATL", "W", 2.91, 431.0),
    "10/24/2026": ("0022600021", "CHA @ MIA", "L", 3.12, 402.0),
    "10/26/2026": ("0022600040", "CHA @ ORL", "L", 2.77, 445.0),
}


def pt_row(team_id, abbr, name, sec, touches):
    return [team_id, abbr, name, 1, 1, 0, 48.0, 110.0, touches, 300.0, 24.0, sec, 2.0, 0.25]


class FakeResponse:
    def __init__(self, body, status=200):
        self._body, self.status_code = body, status

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(self.status_code)


class FakeSession:
    """Routes requests like stats.nba.com would and records every call."""

    def __init__(self):
        self.headers, self.calls = {}, []

    def get(self, url, params, timeout):
        endpoint = url.rsplit("/", 1)[-1]
        self.calls.append((endpoint, dict(params)))
        if endpoint == "teamgamelogs":
            rows = [[CHA, gid, f"{d[6:]}-{d[:2]}-{d[3:5]}T00:00:00", m, wl, 110]
                    for d, (gid, m, wl, _, _) in reversed(list(GAMES.items()))]
            return FakeResponse({"resultSets": [{
                "name": "TeamGameLogs",
                "headers": ["TEAM_ID", "GAME_ID", "GAME_DATE", "MATCHUP", "WL", "PTS"],
                "rowSet": rows}]})
        if endpoint == "leaguedashptstats":
            d = params["DateFrom"]
            if d:
                _, _, _, sec, touches = GAMES[d]
            else:  # season-to-date
                sec, touches = 2.93, 426.0
            # Include another team to prove filtering works.
            rows = [pt_row(1610612737, "ATL", "Atlanta Hawks", 3.5, 400.0),
                    pt_row(CHA, "CHA", "Charlotte Hornets", sec, touches)]
            return FakeResponse({"resultSets": [{
                "name": "LeagueDashPtStats", "headers": PT_HEADERS, "rowSet": rows}]})
        raise AssertionError(endpoint)


@pytest.fixture
def client(tmp_path):
    return NBAStatsClient(cache=ResponseCache(tmp_path / "c.sqlite"),
                          session=FakeSession(), min_interval=0)


def test_team_lookup():
    assert find_team("hornets").id == CHA
    assert find_team("CHO").abbr == "CHA"
    assert find_team("Charlotte").id == CHA
    assert find_team(CHA).name == "Hornets"
    with pytest.raises(ValueError):
        find_team("Sonics")


def test_current_season():
    assert current_season(date(2026, 11, 18)) == "2026-27"
    assert current_season(date(2027, 3, 1)) == "2026-27"
    assert current_season(date(2026, 7, 1)) == "2025-26"


@pytest.mark.parametrize("raw", ["2026-11-16T00:00:00", "NOV 16, 2026", "11/16/2026"])
def test_parse_game_date(raw):
    assert parse_game_date(raw) == date(2026, 11, 16)


def test_by_game_joins_every_game(client):
    df = client.team_tracking_by_game("Hornets", "Possessions", "2026-27")
    assert list(df["MATCHUP"]) == ["CHA vs. ATL", "CHA @ MIA", "CHA @ ORL"]  # chronological
    assert list(df["AVG_SEC_PER_TOUCH"]) == [2.91, 3.12, 2.77]
    assert set(df["TEAM_ID"]) == {CHA}  # other team filtered out

    pt_calls = [p for e, p in client.session.calls if e == "leaguedashptstats"]
    assert [p["DateFrom"] for p in pt_calls] == list(GAMES)
    assert all(p["DateFrom"] == p["DateTo"] and p["PtMeasureType"] == "Possessions"
               and p["TeamID"] == CHA for p in pt_calls)


def test_cache_avoids_repeat_requests(client):
    client.team_tracking_by_game("CHA", season="2026-27")
    n = len(client.session.calls)
    client.team_tracking_by_game("CHA", season="2026-27")
    assert len(client.session.calls) == n


def test_blocked_connection_gives_clear_error(tmp_path, monkeypatch):
    class DeadSession(FakeSession):
        def get(self, url, params, timeout):
            raise requests.ConnectionError("Empty reply from server")

    monkeypatch.setattr("hub.http.time.sleep", lambda s: None)
    c = NBAStatsClient(cache=ResponseCache(tmp_path / "c.sqlite"),
                       session=DeadSession(), min_interval=0)
    with pytest.raises(NBAStatsBlocked, match="home/office network"):
        c.team_game_log("CHA")


def test_cli_by_game(client, monkeypatch, capsys):
    from hub import __main__ as cli
    monkeypatch.setattr(cli, "NBAStatsClient", lambda: client)
    cli.main(["by-game", "Hornets", "--stat", "AVG_SEC_PER_TOUCH", "--season", "2026-27"])
    out = capsys.readouterr().out
    assert "CHA @ MIA" in out and "3.12" in out
    assert "AVG_SEC_PER_TOUCH: mean = 2.933" in out
    assert "NBA.com season figure = 2.93" in out
