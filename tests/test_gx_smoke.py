"""C1 smoke test — the Great Expectations scaffold runs green, fully offline.

Runs in the existing CI pytest step (no GX CLI, no creds, no network): builds a
throwaway GX context in a temp dir and runs the offline smoke checkpoint over the
committed seed fixtures. This is the C1 "trivial suite passes in CI" gate; C2/C3/C4
add the real bronze/silver/gold suites on top of the same scaffold.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
GX_DIR = REPO_ROOT / "great_expectations"
sys.path.insert(0, str(GX_DIR))

gx_project = pytest.importorskip(
    "gx_project", reason="great_expectations not installed (pip install -r great_expectations/requirements.txt)"
)


def test_offline_smoke_checkpoint_passes(tmp_path):
    """The scaffold's smoke checkpoint validates the seed and succeeds."""
    result = gx_project.run_offline(project_root=tmp_path)
    assert result.success, "GX smoke checkpoint failed against the seed fixtures"


def test_checkpoint_is_registered(tmp_path):
    """The scaffold registers exactly the smoke checkpoint (the 1.x `checkpoint list`)."""
    context = gx_project.get_context(project_root=tmp_path)
    batch = gx_project.register_seed_datasource(context)
    suite = gx_project.build_smoke_suite(context)
    gx_project.build_smoke_checkpoint(context, batch, suite)
    assert gx_project.SMOKE_CHECKPOINT in gx_project.list_checkpoints(context)
