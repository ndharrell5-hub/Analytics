"""Static NBA team lookup. Team IDs on NBA.com have been stable for decades."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Team:
    id: int
    abbr: str
    city: str
    name: str

    @property
    def full_name(self) -> str:
        return f"{self.city} {self.name}"

    @property
    def bbref_abbr(self) -> str:
        """Basketball Reference uses a few legacy codes."""
        return _BBREF_ABBR.get(self.abbr, self.abbr)


_BBREF_ABBR = {"BKN": "BRK", "CHA": "CHO", "PHX": "PHO"}


TEAMS: list[Team] = [
    Team(1610612737, "ATL", "Atlanta", "Hawks"),
    Team(1610612738, "BOS", "Boston", "Celtics"),
    Team(1610612751, "BKN", "Brooklyn", "Nets"),
    Team(1610612766, "CHA", "Charlotte", "Hornets"),
    Team(1610612741, "CHI", "Chicago", "Bulls"),
    Team(1610612739, "CLE", "Cleveland", "Cavaliers"),
    Team(1610612742, "DAL", "Dallas", "Mavericks"),
    Team(1610612743, "DEN", "Denver", "Nuggets"),
    Team(1610612765, "DET", "Detroit", "Pistons"),
    Team(1610612744, "GSW", "Golden State", "Warriors"),
    Team(1610612745, "HOU", "Houston", "Rockets"),
    Team(1610612754, "IND", "Indiana", "Pacers"),
    Team(1610612746, "LAC", "LA", "Clippers"),
    Team(1610612747, "LAL", "Los Angeles", "Lakers"),
    Team(1610612763, "MEM", "Memphis", "Grizzlies"),
    Team(1610612748, "MIA", "Miami", "Heat"),
    Team(1610612749, "MIL", "Milwaukee", "Bucks"),
    Team(1610612750, "MIN", "Minnesota", "Timberwolves"),
    Team(1610612740, "NOP", "New Orleans", "Pelicans"),
    Team(1610612752, "NYK", "New York", "Knicks"),
    Team(1610612760, "OKC", "Oklahoma City", "Thunder"),
    Team(1610612753, "ORL", "Orlando", "Magic"),
    Team(1610612755, "PHI", "Philadelphia", "76ers"),
    Team(1610612756, "PHX", "Phoenix", "Suns"),
    Team(1610612757, "POR", "Portland", "Trail Blazers"),
    Team(1610612758, "SAC", "Sacramento", "Kings"),
    Team(1610612759, "SAS", "San Antonio", "Spurs"),
    Team(1610612761, "TOR", "Toronto", "Raptors"),
    Team(1610612762, "UTA", "Utah", "Jazz"),
    Team(1610612764, "WAS", "Washington", "Wizards"),
]

_ALIASES = {"PHO": "PHX", "BRK": "BKN", "CHO": "CHA", "GS": "GSW", "NO": "NOP",
            "NY": "NYK", "SA": "SAS", "UTAH": "UTA", "WSH": "WAS", "SIXERS": "PHI",
            "BLAZERS": "POR", "WOLVES": "MIN", "CAVS": "CLE", "MAVS": "DAL"}


def find_team(query: str | int) -> Team:
    """Resolve an abbreviation, nickname, city, full name, or team ID to a Team."""
    if isinstance(query, int) or str(query).isdigit():
        for t in TEAMS:
            if t.id == int(query):
                return t
        raise ValueError(f"Unknown team id: {query}")

    q = str(query).strip().upper()
    q = _ALIASES.get(q, q)
    for t in TEAMS:
        if q in (t.abbr, t.name.upper(), t.city.upper(), t.full_name.upper()):
            return t
    matches = [t for t in TEAMS if q in t.full_name.upper()]
    if len(matches) == 1:
        return matches[0]
    raise ValueError(f"Could not resolve team: {query!r}")
