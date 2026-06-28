"""Offline unit tests for the pure gap classifier in eda/diagnose_price_gaps.py.

No network/BQ: feeds small inline presence rows shaped like the bq CSV output
(event_id, snapshot_date, has_price as "true"/"false" strings).
"""

import sys
from pathlib import Path

EDA = Path(__file__).resolve().parent.parent / "eda"
sys.path.insert(0, str(EDA))
import diagnose_price_gaps as diag  # noqa: E402

DATES = ["2026-06-11", "2026-06-12", "2026-06-13", "2026-06-14"]


def presence(event_id, days):
    """days: dict snapshot_date -> has_price bool. Returns bq-shaped rows."""
    return [{"event_id": event_id, "snapshot_date": d, "has_price": str(p).lower()}
            for d, p in days.items()]


def test_window_dates_is_sorted_distinct():
    rows = [  # duplicate snapshot_date across two rows -> deduped + sorted
        {"event_id": "A", "snapshot_date": "2026-06-13", "has_price": "true"},
        {"event_id": "A", "snapshot_date": "2026-06-11", "has_price": "true"},
        {"event_id": "B", "snapshot_date": "2026-06-13", "has_price": "false"},
    ]
    assert diag.window_dates(rows) == ["2026-06-11", "2026-06-13"]


def test_classify_interior_gap_is_coverage():
    # B present 11,12,14 (missing 13 between first/last), priced every present day.
    rows = presence("B", {"2026-06-11": True, "2026-06-12": True, "2026-06-14": True})
    [c] = diag.classify_event_gaps(rows, DATES)
    assert c["interior_absent"] == 1          # the missing 13th — a sweep drop
    assert c["unpriced_present"] == 0
    assert c["leading_absent"] == 0 and c["trailing_absent"] == 0


def test_classify_unpriced_present_is_source():
    # C present all 4 days but priced only the first two.
    rows = presence("C", {"2026-06-11": True, "2026-06-12": True,
                          "2026-06-13": False, "2026-06-14": False})
    [c] = diag.classify_event_gaps(rows, DATES)
    assert c["interior_absent"] == 0
    assert c["unpriced_present"] == 2         # captured but no price — re-fetch can't fix


def test_classify_edges_not_counted_interior():
    # D entered late (present 13,14): the missing 11,12 are leading-edge, not interior.
    rows = presence("D", {"2026-06-13": True, "2026-06-14": True})
    [d] = diag.classify_event_gaps(rows, DATES)
    assert d["leading_absent"] == 2 and d["interior_absent"] == 0
    # E left early (present 11,12): missing 13,14 are trailing-edge.
    rows = presence("E", {"2026-06-11": True, "2026-06-12": True})
    [e] = diag.classify_event_gaps(rows, DATES)
    assert e["trailing_absent"] == 2 and e["interior_absent"] == 0


def test_summarize_coverage_vs_source_split():
    rows = (
        presence("A", {d: True for d in DATES})                          # complete
        + presence("B", {"2026-06-11": True, "2026-06-12": True, "2026-06-14": True})  # 1 interior
        + presence("C", {"2026-06-11": True, "2026-06-12": True,
                         "2026-06-13": False, "2026-06-14": False})      # 2 source
    )
    classified = diag.classify_event_gaps(rows, DATES)
    s = diag.summarize_gaps(classified, total_days=len(DATES))
    assert s["priced_events"] == 3
    assert s["interior_coverage_gap_days"] == 1
    assert s["unpriced_present_source_gap_days"] == 2
    assert s["pct_gap_days_fixable_by_refetch"] == round(100 * 1 / 3, 1)
    assert s["events_with_interior_gap"] == 1          # B
    assert s["events_source_gap_only"] == 1            # C
