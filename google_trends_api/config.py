"""Configuration for the music-demand Google Trends POC.

Two things live here so the fetch logic stays generic:

1. ``METROS`` — the 10 large US metros we track, keyed by a Google Trends DMA
   geo code in the ``US-<STATE>-<DMA>`` form that pytrends accepts (verified:
   bare ``US-<DMA>`` returns HTTP 400). The Bay Area (DMA 807) is the project's
   home market; the rest let us compare artists touring into other large metros.
   These are deliberately *large* media markets — we avoid small DMAs, whose
   low search volume produces noisy 0/100 spikes.
2. ``ARTISTS`` — the roster we pull search interest for. ``query`` is what we
   actually send to Google Trends; it can differ from the display ``name`` to
   disambiguate common words (e.g. "Fisher" -> "Fisher DJ").

Geo/DMA codes were confirmed against ``interest_by_region(resolution="DMA")``.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Metro:
    """One Google Trends media market (Nielsen DMA)."""

    name: str          # human label
    geo: str           # Google Trends geo code, US-<STATE>-<DMA>
    dma: int           # Nielsen DMA number (for joins / reference)


@dataclass(frozen=True)
class Artist:
    """One act (or festival) we track on Google Trends."""

    name: str          # display name
    query: str         # exact term sent to Google Trends
    category: str      # edm | pop | hiphop | rnb | festival


# --- Metro presets -----------------------------------------------------------
# Ten large US live-music metros (Nielsen DMAs). Weighted toward major
# touring/EDM markets rather than strict population rank: Las Vegas, Austin,
# and Denver punch above their population for live music. SF is the anchor.
US_METROS: list[Metro] = [
    Metro("San Francisco-Oakland-San Jose", "US-CA-807", 807),
    Metro("Los Angeles", "US-CA-803", 803),
    Metro("New York", "US-NY-501", 501),
    Metro("Chicago", "US-IL-602", 602),
    Metro("Las Vegas", "US-NV-839", 839),
    Metro("Miami-Ft. Lauderdale", "US-FL-528", 528),
    Metro("Denver", "US-CO-751", 751),
    Metro("Seattle-Tacoma", "US-WA-819", 819),
    Metro("Austin", "US-TX-635", 635),
    Metro("Atlanta", "US-GA-524", 524),
]

# Bay Area only — narrow preset that lines up with the Ticketmaster POC scope.
BAY_AREA: list[Metro] = [US_METROS[0]]

# Named presets selectable from the CLI via --geo.
GEO_PRESETS: dict[str, list[Metro]] = {
    "us-metros": US_METROS,
    "bay-area": BAY_AREA,
}

DEFAULT_GEO_PRESET = "us-metros"


# --- Artist roster -----------------------------------------------------------
# Starts from a core of touring EDM acts and adds artists/festivals with big
# upcoming Bay Area shows. ``query`` disambiguates terms that collide with
# common words; see README "Known limitations".
ARTISTS: list[Artist] = [
    # Core touring EDM
    Artist("Skrillex", "Skrillex", "edm"),
    Artist("Calvin Harris", "Calvin Harris", "edm"),
    Artist("Deadmau5", "Deadmau5", "edm"),
    Artist("Fisher", "Fisher DJ", "edm"),
    Artist("Charlotte de Witte", "Charlotte de Witte", "edm"),
    Artist("Zedd", "Zedd", "edm"),
    Artist("Illenium", "Illenium", "edm"),
    Artist("Kaytranada", "Kaytranada", "edm"),
    Artist("ODESZA", "ODESZA", "edm"),
    Artist("John Summit", "John Summit", "edm"),
    Artist("Fred again..", "Fred again", "edm"),
    Artist("Sammy Virji", "Sammy Virji", "edm"),
    Artist("Nimino", "Nimino", "edm"),
    Artist("Swedish House Mafia", "Swedish House Mafia", "edm"),
    Artist("Four Tet", "Four Tet", "edm"),
    Artist("Ben Böhmer", "Ben Bohmer", "edm"),
    Artist("Laszewo", "Laszewo", "edm"),
    Artist("RÜFÜS DU SOL", "Rufus Du Sol", "edm"),
    Artist("Disco Lines", "Disco Lines", "edm"),
    # Indie / electronic-adjacent
    Artist("Parcels", "Parcels band", "edm"),
    Artist("The xx", "The xx", "edm"),
    Artist("Empire of the Sun", "Empire of the Sun band", "edm"),
    # Pop / hip-hop / R&B with big Bay Area shows
    Artist("Charli XCX", "Charli XCX", "pop"),
    Artist("Tinashe", "Tinashe", "pop"),
    Artist("Ariana Grande", "Ariana Grande", "pop"),
    Artist("Ed Sheeran", "Ed Sheeran", "pop"),
    Artist("Shakira", "Shakira", "pop"),
    Artist("A$AP Rocky", "ASAP Rocky", "hiphop"),
    Artist("Usher", "Usher", "rnb"),
    Artist("Chris Brown", "Chris Brown", "rnb"),
    # Festivals
    Artist("Portola Festival", "Portola Festival", "festival"),
    Artist("Outside Lands", "Outside Lands", "festival"),
]


def artists_by_category(category: str) -> list[Artist]:
    """Return roster entries matching a category (e.g. 'edm', 'festival')."""

    return [a for a in ARTISTS if a.category == category]
