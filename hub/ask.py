"""Plain-English questions:  python -m hub ask "What's been the Hornets' sec per touch each game?"

Claude reads the question, calls the data tools in hub/tools.py (which run on this machine, so
NBA.com works from a home network), and answers with the numbers and their source. Uses the
Anthropic SDK's tool runner, which handles the call-tool / send-result loop.

Needs an Anthropic API key in ANTHROPIC_API_KEY (or a profile from `ant auth login`).
"""

from __future__ import annotations

import sys
from datetime import date
from typing import Any, Callable

import anthropic
from anthropic import beta_tool

from hub import tools
from hub.sources.nba_stats import current_season

MODEL = "claude-opus-5-5"
EFFORT = "medium"
MAX_TOOL_ROUNDS = 15

SYSTEM_PROMPT = """\
You are an NBA research assistant. You answer questions with data you fetch using your tools, \
never from memory. Every number in your answer must come from a tool result in this \
conversation.

Sources and what each is best for:
- NBA.com (nba_* tools): player-tracking data, and the only source for it. That covers \
touches, seconds and dribbles per touch, time of possession, drives, passing and potential \
assists, catch-and-shoot, pull-ups, speed and distance, rebound chances, rim defense, and \
elbow, post and paint touches.
- Basketball Reference (bbref_* tools): box-score game logs, advanced game logs (offensive \
and defensive rating, pace, four factors), player game logs, and player advanced metrics \
(PER, BPM, Win Shares, VORP, usage).
- PBP Stats (pbp_* tools): possession-level data from play-by-play. That covers seconds per \
possession, shot quality, shot-zone frequency and accuracy, lineups, on/off splits, \
second-chance and penalty splits.

How to work:
- Pick the source that actually measures what was asked. If a column name is unclear, call \
the tool without `stats` to list the columns, then call again.
- Make independent tool calls in parallel, for example when comparing two teams.
- If a source is unavailable (NBA.com often blocks some networks), say so plainly. You may \
offer the closest related stat from another source, but label it as a different measure; \
never present it as the stat that was asked for.
- Use per-48 or per-possession rates rather than raw totals when comparing players with \
different minutes, and flag small samples.
- When a season figure exists from the source itself (such as NBA.com's official season \
number), prefer it to an average you computed, and say which one you report.

How to answer (the user reads this in a terminal):
- Lead with a direct one or two sentence answer.
- Then give the data as a compact plain-text table when there are several rows.
- End with one line naming the source(s), and any caveat that matters (sample size, \
garbage time not removed, a different measure used).
- No headings. Keep it short. Basketball terms are fine; explain a metric only if it's obscure.
"""

TOOL_LABELS = {
    "nba_team_tracking_by_game": "NBA.com tracking, by game",
    "nba_tracking_table": "NBA.com tracking table",
    "bbref_team_game_log": "Basketball Reference team game log",
    "bbref_team_table": "Basketball Reference team page",
    "bbref_player_game_log": "Basketball Reference player game log",
    "bbref_league_players": "Basketball Reference league table",
    "pbp_game_log": "PBP Stats game log",
    "pbp_totals": "PBP Stats totals",
    "pbp_on_off": "PBP Stats on/off",
}


def _describe_call(name: str, args: dict[str, Any]) -> str:
    shown = ", ".join(f"{k}={v}" for k, v in args.items() if v not in (None, [], False))
    return f"  → {TOOL_LABELS.get(name, name)}" + (f" ({shown})" if shown else "")


class Researcher:
    """Holds one conversation; each ask() continues it so follow-up questions work."""

    def __init__(self, client: anthropic.Anthropic | None = None, model: str = MODEL,
                 effort: str = EFFORT, log: Callable[[str], None] | None = None):
        self.client = client or anthropic.Anthropic()
        self.model = model
        self.effort = effort
        self.log = log or (lambda line: print(line, file=sys.stderr))
        self.tools = [beta_tool(fn) for fn in tools.TOOLS]
        self.history: list[Any] = []

    def ask(self, question: str) -> str:
        if not self.history:  # date context once, in the first user turn
            question = (f"(Today is {date.today():%B %d, %Y}; the current season is "
                        f"{current_season()}.)\n\n{question}")
        messages = [*self.history, {"role": "user", "content": question}]

        runner = self.client.beta.messages.tool_runner(
            model=self.model,
            max_tokens=16000,
            system=SYSTEM_PROMPT,
            tools=self.tools,
            messages=messages,
            output_config={"effort": self.effort},
            cache_control={"type": "ephemeral"},
            # If Claude declines a request on safety grounds, retry on a fallback model.
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            max_iterations=MAX_TOOL_ROUNDS,
        )

        final = None
        for message in runner:
            messages.append(message.to_param())  # keeps thinking blocks intact for replay
            for block in message.content:
                if block.type == "tool_use":
                    self.log(_describe_call(block.name, dict(block.input)))
            tool_results = runner.generate_tool_call_response()
            if tool_results is not None:
                messages.append(tool_results)
            final = message

        self.history = messages
        if final is None:
            return "(No response.)"
        if final.stop_reason == "refusal":
            return "Claude declined to answer this one."
        if final.stop_reason == "tool_use":
            return (f"(Stopped after {MAX_TOOL_ROUNDS} rounds of data lookups without a final "
                    "answer. Try a narrower question.)")
        text = "\n".join(b.text for b in final.content if b.type == "text").strip()
        if final.stop_reason == "max_tokens":
            text += "\n\n(Answer was cut off at the length limit.)"
        return text or "(No text in the response.)"


NO_KEY_HELP = ("No Anthropic API key found. Create one at console.anthropic.com, then:\n"
               "  export ANTHROPIC_API_KEY=...   (or run `ant auth login`)")


def _api_error_message(err: anthropic.APIError) -> str:
    if isinstance(err, anthropic.AuthenticationError):
        return "Anthropic API key invalid or revoked.\n" + NO_KEY_HELP
    if isinstance(err, anthropic.RateLimitError):
        return "Anthropic rate limit hit; wait a minute and try again."
    if isinstance(err, anthropic.APIConnectionError):
        return "Could not reach the Anthropic API; check your connection."
    return f"Anthropic API error: {err}"


def run(question: str | None) -> None:
    try:
        researcher = Researcher()
    except anthropic.AnthropicError as err:
        sys.exit(f"Could not create the Anthropic client: {err}\n"
                 "Set ANTHROPIC_API_KEY, or run `ant auth login`.")

    def one(q: str) -> None:
        try:
            print("\n" + researcher.ask(q) + "\n")
        except anthropic.APIError as err:
            print(f"\n{_api_error_message(err)}\n", file=sys.stderr)
        except TypeError as err:
            if "authentication" not in str(err):
                raise
            sys.exit(NO_KEY_HELP)

    if question:
        one(question)
        return
    print("NBA research hub. Ask a question; follow-ups keep context. Ctrl-D or 'quit' to exit.")
    while True:
        try:
            q = input("\n? ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if q.lower() in ("quit", "exit", "q"):
            return
        if q:
            one(q)
