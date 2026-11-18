"""End-to-end test of the question layer with the real SDK tool runner and a scripted fake
Anthropic API (HTTP-level mock), so no API key or network is needed."""

import json

import anthropic
import httpx2
import pandas as pd
import pytest

from hub import tools
from hub.ask import Researcher
from hub.sources.nba_stats import NBAStatsBlocked


class FakePBP:
    def game_logs(self, team=None, player_id=None, lineup_id=None, season=None):
        return pd.DataFrame({"Date": ["2026-10-21", "2026-10-23"], "Opponent": ["BKN", "ATL"],
                             "Minutes": ["48:00", "48:00"], "SecondsPerPossOff": [15.1, 15.64]})


class BlockedNBA:
    def team_tracking_by_game(self, *a, **k):
        raise NBAStatsBlocked("Could not reach stats.nba.com. run this from a home network.")


def assistant(content, stop_reason):
    return {"id": "msg_x", "type": "message", "role": "assistant", "model": "claude-opus-5-5",
            "content": content, "stop_reason": stop_reason, "stop_sequence": None,
            "usage": {"input_tokens": 10, "output_tokens": 10}}


def tool_use(id_, name, input_):
    return {"type": "tool_use", "id": id_, "name": name, "input": input_}


def make_client(script):
    """A real Anthropic client whose HTTP layer replays `script` and records each request."""
    requests = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(json.loads(request.content))
        return httpx2.Response(200, json=script[len(requests) - 1])

    client = anthropic.Anthropic(
        api_key="test-key", base_url="http://fake-anthropic.test", max_retries=0,
        http_client=anthropic.DefaultHttpxClient(transport=httpx2.MockTransport(handler)))
    return client, requests


@pytest.fixture(autouse=True)
def fake_sources(monkeypatch):
    monkeypatch.setattr(tools, "pbp", lambda: FakePBP())
    monkeypatch.setattr(tools, "nba", lambda: BlockedNBA())


def test_tool_round_trip_blocked_source_and_follow_up():
    script = [
        # Turn 1: Claude calls two tools in parallel.
        assistant([tool_use("t1", "nba_team_tracking_by_game",
                            {"team": "Hornets", "stats": ["AVG_SEC_PER_TOUCH"]}),
                   tool_use("t2", "pbp_game_log",
                            {"team": "Hornets", "stats": ["SecondsPerPossOff"]})], "tool_use"),
        assistant([{"type": "text", "text": "NBA.com was unavailable. Seconds per "
                    "possession averaged 15.37."}], "end_turn"),
        # Follow-up question, answered without tools.
        assistant([{"type": "text", "text": "The slowest game was vs ATL (15.64)."}],
                  "end_turn"),
    ]
    client, requests = make_client(script)
    calls = []
    r = Researcher(client=client, log=calls.append)

    answer = r.ask("What has been the Hornets' avg sec per touch in every game?")
    assert answer.startswith("NBA.com was unavailable")
    assert calls == ["  → NBA.com tracking, by game (team=Hornets, stats=['AVG_SEC_PER_TOUCH'])",
                     "  → PBP Stats game log (team=Hornets, stats=['SecondsPerPossOff'])"]

    first = requests[0]
    assert first["model"] == "claude-opus-5-5"
    assert first["output_config"] == {"effort": "medium"}
    assert first["fallbacks"] == "default"
    assert {t["name"] for t in first["tools"]} == {f.__name__ for f in tools.TOOLS}
    assert "current season is" in first["messages"][0]["content"]

    # Both tool results go back in ONE user message; the blocked source is flagged as an error.
    results = requests[1]["messages"][-1]
    assert results["role"] == "user" and len(results["content"]) == 2
    by_id = {r["tool_use_id"]: r for r in results["content"]}
    assert by_id["t1"]["is_error"] is True
    assert "SOURCE UNAVAILABLE" in json.dumps(by_id["t1"]["content"])
    assert "15.64" in json.dumps(by_id["t2"]["content"])
    assert not by_id["t2"].get("is_error")

    # Follow-up keeps the whole conversation, tool calls included.
    assert r.ask("Which game was slowest?") == "The slowest game was vs ATL (15.64)."
    follow = requests[2]["messages"]
    assert [m["role"] for m in follow] == ["user", "assistant", "user", "assistant", "user"]
    assert follow[-1]["content"] == "Which game was slowest?"


def test_refusal_and_runaway_loop_are_reported():
    client, _ = make_client([assistant([], "refusal")])
    assert "declined" in Researcher(client=client, log=lambda s: None).ask("q")

    loop = [assistant([tool_use(f"t{i}", "pbp_game_log", {"team": "CHA"})], "tool_use")
            for i in range(20)]
    client, requests = make_client(loop)
    answer = Researcher(client=client, log=lambda s: None).ask("q")
    assert "Stopped after" in answer and len(requests) == 15


def test_tool_lists_columns_when_stats_omitted():
    out = tools.pbp_game_log(team="CHA")
    assert "Call again with `stats`" in out and "SecondsPerPossOff" in out
