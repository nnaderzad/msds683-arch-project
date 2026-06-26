"""Deterministic surrogate keys shared across the silver + gold builds.

The locked schema (``docs/data-model.md``) gives the facts integer ``artist_id`` /
``venue_id`` foreign keys. We generate those surrogates **deterministically from the
natural key** (a stable hash of the normalized name / source id) rather than from a
sequence, so every transform computes the *same* id for the same entity **without a
build-order dependency**: ``fact_trends`` (A1), ``fact_youtube`` (A2), the dimensions
(A3) and the gold star (B1) can each be built independently and still join.

This is the project's answer to the schema's open "surrogate vs natural key" question:
deterministic surrogates = stable, joinable, and re-runnable (no LLM, no DB round-trip).

Pure stdlib + offline; the same function runs in the transform and in its unit tests.
"""

from __future__ import annotations

import hashlib

_INT63 = (1 << 63) - 1  # keep ids in BigQuery's signed INT64 positive range


def normalize_name(name: str | None) -> str:
    """Case/whitespace-insensitive natural key used for every artist surrogate.

    The artist display name is the join key across all three sources (Trends
    ``artist``, YouTube ``query``, Ticketmaster ``attraction_names``), so the
    surrogate must be computed from the *same* normalized form everywhere.
    """
    return " ".join(str(name or "").split()).casefold()


def _stable_int(text: str) -> int:
    """A deterministic positive 63-bit int from text (BLAKE2b, engine-independent)."""
    digest = hashlib.blake2b(text.encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, "big") & _INT63


def artist_id(artist_name: str | None) -> int:
    """Surrogate id for an artist, from its normalized display name."""
    return _stable_int("artist:" + normalize_name(artist_name))


def venue_id(ticketmaster_venue_id: str | None) -> int:
    """Surrogate id for a venue, from its Ticketmaster venue id (the natural key)."""
    return _stable_int("venue:" + str(ticketmaster_venue_id or "").strip())


def snapshot_id(*parts: object) -> str:
    """Deterministic id for a fact row from its business-key parts.

    e.g. ``snapshot_id("trends", artist_id, dma_code, snapshot_date)`` — stable across
    runs, so re-loading the same snapshot MERGEs in place (idempotent).
    """
    key = "|".join(str(p) for p in parts)
    return hashlib.blake2b(key.encode("utf-8"), digest_size=16).hexdigest()
