"""Demand heuristics for the synthetic layer (`event_demand_synth`).

Encodes the team's observed market dynamics as pure, seeded, unit-tested
functions — the "physics" the generator applies to every real event when
infilling what no source provides (TM bronze carries **0.0%** resale
priceRanges; sellout timing is unobserved):

  * Popular artist + small room  → sells out fast, resale ABOVE face
    (MGMT at Public Works, ~750 cap: gone instantly, resale >> face).
  * Mid-demand artist + big room → never sells out, resale BELOW face
    (Chris Lake at a large SF club: $100 face resold at $50–60 day-of).
  * Festival day tickets anchor to the summed draw of that day's headliners
    (Outside Lands: $250 face reselling ~$500 — 2–3 headliners whose solo
    shows each go for ~$150–300).

Everything routes through one scalar: ``demand_ratio`` = expected draw ÷
capacity. Sellout probability/speed and the resale multiplier are monotone in
that ratio. All randomness comes from an injected ``numpy.random.Generator``,
so a fixed seed reproduces the identical synthetic world (repo determinism
rule); values derived here are labeled synthetic end-to-end and never touch the
honest production tables.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

import numpy as np

# Capacity fallbacks by event type when neither research nor TM provides one —
# used ONLY in the synth dataset, labeled capacity_source='tier_estimate'.
TIER_DEFAULT_CAPACITY = {"club": 400, "concert": 2000, "festival": 20000}

FESTIVAL_NAME_RE = re.compile(r"\b(festival|fest|outside lands|coachella|day \d)\b", re.I)

# expected_draw() shape: draw = DRAW_FLOOR * 10 ** (DRAW_EXPONENT * popularity).
# Calibrated so popularity 0 → ~30 people (unknown local act), 0.55 → ~2.5k
# (regional headliner), 0.8 → ~19k, 1.0 → ~100k (stadium-tier artist).
DRAW_FLOOR = 30.0
DRAW_EXPONENT = 3.55

# Log-subscriber normalization: 10^8 subs (~top global act) maps to 1.0.
_SUBS_LOG_CEILING = 8.0

# Sellout logistic: P(sellout) = sigmoid(STEEPNESS * (ratio - MIDPOINT)).
_SELLOUT_MIDPOINT = 1.0
_SELLOUT_STEEPNESS = 4.0


@dataclass(frozen=True)
class DemandOutcome:
    """Synthetic demand verdict for one event."""

    demand_ratio: float
    sellout_probability: float
    sold_out: bool
    sellout_days_before_show: int | None
    resale_multiplier: float


def popularity_score(
    local_interest: float | None, yt_subscribers: float | None,
    w_local: float = 0.45, w_global: float = 0.55,
) -> float:
    """Blend local (Trends 0–100 within one artist+metro) and global (YouTube subs)
    popularity into one 0–1 score.

    The local term uses the artist's OWN trajectory level (a within-artist signal —
    never a cross-artist Trends comparison); the global term is log-scaled subs.
    Missing signals contribute 0 — an unknown act scores near 0, not average.
    """
    local = 0.0 if local_interest is None else min(max(local_interest, 0.0), 100.0) / 100.0
    subs = 0.0 if yt_subscribers is None else max(float(yt_subscribers), 0.0)
    global_ = min(math.log10(subs + 1.0) / _SUBS_LOG_CEILING, 1.0)
    return min(w_local * local + w_global * global_, 1.0)


def infer_event_type(event_name: str | None, capacity: int | None) -> str:
    """Classify club / concert / festival from the event name and room size."""
    if event_name and FESTIVAL_NAME_RE.search(event_name):
        return "festival"
    if capacity is not None and capacity < 1000:
        return "club"
    return "concert"


def effective_capacity(capacity: int | None, event_type: str) -> int:
    """Real capacity when known; otherwise the labeled tier estimate."""
    if capacity is not None and capacity > 0:
        return int(capacity)
    return TIER_DEFAULT_CAPACITY.get(event_type, TIER_DEFAULT_CAPACITY["concert"])


def expected_draw(popularity: float) -> float:
    """People who *want* tickets, as an exponential function of popularity."""
    return DRAW_FLOOR * 10 ** (DRAW_EXPONENT * min(max(popularity, 0.0), 1.0))


def demand_ratio(draw: float, capacity: int) -> float:
    """Demand pressure: >1 oversubscribed (sellout economics), <1 soft room."""
    return draw / max(capacity, 1)


def sellout_probability(ratio: float) -> float:
    """Logistic in the demand ratio, centered at ratio=1."""
    return 1.0 / (1.0 + math.exp(-_SELLOUT_STEEPNESS * (ratio - _SELLOUT_MIDPOINT)))


def sellout_days_before_show(
    ratio: float, onsale_horizon_days: int, rng: np.random.Generator
) -> int | None:
    """When an oversubscribed show sells out, relative to the show date.

    Heavily oversubscribed rooms (MGMT at Public Works) go almost immediately
    after onsale; mild oversubscription sells out close to the show. Returns
    None for ratio <= 1 callers (guarded here for safety).
    """
    if ratio <= 1.0:
        return None
    # Fraction of the onsale window still remaining when the room sells out:
    # ratio 1 → ~0 (sells out at the door), ratio >= 4 → ~1 (instant).
    urgency = min((ratio - 1.0) / 3.0, 1.0)
    jitter = rng.uniform(0.85, 1.15)
    days = int(round(onsale_horizon_days * min(urgency * jitter, 1.0)))
    return min(max(days, 0), onsale_horizon_days)


def resale_multiplier(ratio: float, rng: np.random.Generator) -> float:
    """Resale price ÷ face value as a monotone function of demand pressure.

    Piecewise-linear base with seeded ±10% noise:
      ratio 0.3 → ~0.55 (Chris Lake day-of fire sale), 1.0 → ~1.0,
      2.0 → ~1.9, 4.0+ → ~2.8–3.3 (MGMT / festival scalping territory).
    """
    if ratio <= 1.0:
        base = 0.5 + 0.5 * max(ratio, 0.0)          # 0 → 0.5, 1 → 1.0
    elif ratio <= 2.0:
        base = 1.0 + 0.9 * (ratio - 1.0)            # 1 → 1.0, 2 → 1.9
    else:
        base = 1.9 + 0.6 * min(ratio - 2.0, 2.0)    # 2 → 1.9, 4+ → 3.1 cap
    noise = rng.uniform(0.9, 1.1)
    return round(base * noise, 2)


def resale_multiplier_at(
    final_multiplier: float, days_to_show: int, horizon_days: int
) -> float:
    """Resale multiplier trajectory: starts at face when onsale opens, converges
    to the final multiplier as the show approaches (superlinear near the date —
    that's when both scalping premiums and fire sales happen)."""
    if horizon_days <= 0:
        return final_multiplier
    progress = 1.0 - min(max(days_to_show, 0), horizon_days) / horizon_days
    return round(1.0 + (final_multiplier - 1.0) * progress**1.5, 3)


def festival_day_face_price(headliner_solo_prices: list[float]) -> float:
    """Festival day-ticket face value anchored to the day's headliner lineup.

    A day pass bundles the top acts, so its face tracks the summed draw of the
    top-3 headliners' solo prices, discounted for the bundle (you can only be at
    one stage at a time). Outside Lands check: solo prices ~[300, 150, 120] →
    face ≈ $256 (matches the observed ~$250 day ticket).
    """
    if not headliner_solo_prices:
        return TIER_DEFAULT_CAPACITY["club"] / 10.0  # degenerate: ~$40 local fest
    top = sorted((p for p in headliner_solo_prices if p and p > 0), reverse=True)[:3]
    return round(0.45 * sum(top), 2)


def simulate_event(
    *,
    event_name: str | None,
    capacity: int | None,
    local_interest: float | None,
    yt_subscribers: float | None,
    onsale_horizon_days: int,
    rng: np.random.Generator,
) -> DemandOutcome:
    """Full per-event pipeline: signals → demand ratio → sellout + resale."""
    event_type = infer_event_type(event_name, capacity)
    cap = effective_capacity(capacity, event_type)
    popularity = popularity_score(local_interest, yt_subscribers)
    ratio = demand_ratio(expected_draw(popularity), cap)
    prob = sellout_probability(ratio)
    sold_out = bool(rng.random() < prob)
    days = sellout_days_before_show(ratio, onsale_horizon_days, rng) if sold_out else None
    return DemandOutcome(
        demand_ratio=round(ratio, 3),
        sellout_probability=round(prob, 3),
        sold_out=sold_out,
        sellout_days_before_show=days,
        resale_multiplier=resale_multiplier(ratio, rng),
    )
