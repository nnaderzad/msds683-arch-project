"""Serve the repo's committed documentation to the public "How it works" page.

The docs render in the dashboard so the architecture story ships WITH the
product: every deploy bundles the current markdown (see api/Dockerfile), so the
public page can never drift from the repo. Only names in the curated catalog
below are served — the name is the key, never a path, so there is no traversal
surface.

Local dev resolves the same repo-relative paths from the checkout; the Docker
image copies them to the same layout under /app.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parent.parent

# Ordered as they should appear in the UI's doc picker.
DOC_CATALOG: dict[str, dict[str, str]] = {
    "architecture": {
        "path": "docs/architecture.md",
        "title": "Architecture & design decisions",
        "description": "System overview, tech-stack choices with rejected alternatives, and the detail map.",
    },
    "readme": {
        "path": "README.md",
        "title": "Project overview",
        "description": "What this is: sources, medallion layers, repo layout, cost.",
    },
    "data-model": {
        "path": "docs/data-model.md",
        "title": "Data model",
        "description": "The locked schema — silver constellation + gold star, with ER diagrams.",
    },
    "transformations": {
        "path": "docs/transformations_showcase.md",
        "title": "Transformations, stage by stage",
        "description": "Every bronze→silver→gold transform with sample schemas and SQL.",
    },
    "gold-refresh": {
        "path": "pipeline/GOLD_REFRESH.md",
        "title": "Nightly gold refresh",
        "description": "The one scheduled job that rebuilds the whole analytical state, fail-fast.",
    },
    "collection-review": {
        "path": "docs/collection_efficiency_review.md",
        "title": "Collection strategy review",
        "description": "Why each source is polled the way it is (findings 1–12, decisions D1–D8).",
    },
    "forecast-model": {
        "path": "docs/forecast_model_decision.md",
        "title": "Forecast model decision",
        "description": "Anchor + drift: the evidence, the decision, and the rollback path.",
    },
    "repo-state": {
        "path": "docs/REPO_STATE.md",
        "title": "Live system state",
        "description": "Where things stand: system map, data freshness, incident log.",
    },
    "lakehouse-plan": {
        "path": "docs/lakehouse-plan.md",
        "title": "Lakehouse build roadmap",
        "description": "The team task board: agent, synthetic layer, benchmarks, demo, blog.",
    },
}


def _doc_path(name: str) -> Path | None:
    entry = DOC_CATALOG.get(name)
    if entry is None:
        return None
    path = _ROOT / entry["path"]
    return path if path.is_file() else None


def list_docs() -> list[dict[str, Any]]:
    """Catalog entries whose file actually exists in this build, in UI order."""
    return [
        {"name": name, "title": entry["title"], "description": entry["description"]}
        for name, entry in DOC_CATALOG.items()
        if _doc_path(name) is not None
    ]


def read_doc(name: str) -> dict[str, Any] | None:
    """One doc's markdown, or None when unknown/absent (the route 404s)."""
    path = _doc_path(name)
    if path is None:
        return None
    entry = DOC_CATALOG[name]
    return {"name": name, "title": entry["title"], "markdown": path.read_text(encoding="utf-8")}
