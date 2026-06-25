"""Offline unit tests for the pure transforms in eda/tm_price_eda.py.

No network/BQ: every test feeds small inline rows shaped like the bq CSV output.
GeoLookup uses the committed reference CSVs. Plotting is smoke-tested only if
matplotlib is installed (it is not a CI dependency).
"""

import sys
from pathlib import Path

import pytest

EDA = Path(__file__).resolve().parent.parent / "eda"
sys.path.insert(0, str(EDA))
import tm_price_eda as eda  # noqa: E402

# GeoLookup is imported by the module after it inserts google_trends_api on path.
GeoLookup = eda.GeoLookup


def raw(event_id, *, snapshot_days, days_with_price, genre="Rock",
        first="50", last="50", zip_code="10001", state="NY",
        names="Some Act", show_date="2026-08-01"):
    """Build one per-event row shaped like the aggregated bq CSV output (strings)."""
    return {
        "event_id": event_id, "event_name": f"Show {event_id}", "genre": genre,
        "venue_name": "V", "venue_city": "C", "venue_state_code": state,
        "venue_postal_code": zip_code, "attraction_names": names,
        "show_date": show_date, "snapshot_days": str(snapshot_days),
        "days_with_price": str(days_with_price), "price_min_first": first,
        "price_min_last": last, "price_min_min": first, "price_max_max": last,
        "price_types": "standard", "price_currency": "USD",
    }


def test_compute_metrics_completeness_and_flags():
    rows = [
        raw("A", snapshot_days=4, days_with_price=4, genre="Dance/Electronic",
            first="50", last="60", zip_code="94115", state="CA"),
        raw("B", snapshot_days=4, days_with_price=1),
        raw("C", snapshot_days=2, days_with_price=2),
    ]
    out = {r["event_id"]: r for r in eda.compute_metrics(rows, total_days=4,
                                                         as_of="2026-07-02")}
    # A: priced every day, full span -> complete; price moved; EDM.
    assert out["A"]["price_completeness"] == 1.0
    assert out["A"]["coverage_span"] == 1.0
    assert out["A"]["is_complete"] is True
    assert out["A"]["price_changed"] is True
    assert out["A"]["is_edm"] is True
    assert out["A"]["headliner"] == "Some Act"
    assert out["A"]["days_to_show"] == 30  # 2026-08-01 - 2026-07-02
    # B: priced 1/4 days -> incomplete despite full span.
    assert out["B"]["price_completeness"] == 0.25
    assert out["B"]["is_complete"] is False
    # C: priced every present day but only half the window -> incomplete.
    assert out["C"]["coverage_span"] == 0.5
    assert out["C"]["is_complete"] is False


def test_compute_metrics_handles_missing_values():
    [row] = eda.compute_metrics([raw("Z", snapshot_days=0, days_with_price=0,
                                     first="", last="", show_date="")],
                                total_days=4, as_of="2026-07-02")
    assert row["price_completeness"] == 0.0
    assert row["coverage_span"] == 0.0
    assert row["price_changed"] is False
    assert row["days_to_show"] is None


def test_enrich_geo_resolves_bay_area():
    rows = eda.compute_metrics(
        [raw("A", snapshot_days=4, days_with_price=4, zip_code="94115", state="CA"),
         raw("B", snapshot_days=4, days_with_price=4, zip_code="10001", state="NY")],
        total_days=4, as_of="2026-07-02")
    eda.enrich_geo(rows, GeoLookup())
    by_id = {r["event_id"]: r for r in rows}
    assert by_id["A"]["dma"] == "807"
    assert by_id["A"]["is_bay_area"] is True
    assert by_id["B"]["dma"]            # resolves to some DMA
    assert by_id["B"]["is_bay_area"] is False


def test_coverage_by_dimension_counts_complete():
    rows = eda.compute_metrics([
        raw("A", snapshot_days=4, days_with_price=4, genre="Dance/Electronic"),
        raw("B", snapshot_days=4, days_with_price=4, genre="Dance/Electronic"),
        raw("C", snapshot_days=4, days_with_price=1, genre="Rock"),
    ], total_days=4, as_of="2026-07-02")
    by_genre = {d["value"]: d for d in eda.coverage_by_dimension(rows, "genre")}
    assert by_genre["Dance/Electronic"]["n_events"] == 2
    assert by_genre["Dance/Electronic"]["n_complete"] == 2
    assert by_genre["Dance/Electronic"]["pct_complete"] == 100.0
    assert by_genre["Rock"]["n_complete"] == 0


def test_pricetype_breakdown_ignores_priceless_events():
    rows = eda.compute_metrics([
        raw("A", snapshot_days=4, days_with_price=4),
        raw("B", snapshot_days=4, days_with_price=0),  # priceless -> excluded
    ], total_days=4, as_of="2026-07-02")
    bd = eda.pricetype_breakdown(rows)
    assert bd == [{"price_types": "standard", "n_events": 1}]


def test_days_with_price_histogram_buckets():
    rows = eda.compute_metrics([
        raw("A", snapshot_days=4, days_with_price=0),
        raw("B", snapshot_days=4, days_with_price=3),
        raw("C", snapshot_days=4, days_with_price=4),
    ], total_days=4, as_of="2026-07-02")
    hist = {h["days_with_price"]: h["n_events"] for h in
            eda.days_with_price_histogram(rows, total_days=4)}
    assert hist["0"] == 1
    assert hist["1-4"] == 1          # B (3 priced days)
    assert hist["4 (all)"] == 1      # C


def test_select_plot_events_injects_edm_example():
    rows = eda.compute_metrics([
        raw("B", snapshot_days=5, days_with_price=5, genre="Rock"),
        raw("A", snapshot_days=4, days_with_price=4, genre="Dance/Electronic"),
    ], total_days=5, as_of="2026-07-02")
    picks = eda.select_plot_events(rows, n=1)
    assert [p["event_id"] for p in picks] == ["A"]  # EDM injected over the Rock top pick


def test_build_series_for_plot_orders_and_filters():
    series_rows = [
        {"event_id": "A", "snapshot_date": "2026-06-13", "price_min": "55", "price_max": "80"},
        {"event_id": "A", "snapshot_date": "2026-06-11", "price_min": "50", "price_max": "75"},
        {"event_id": "B", "snapshot_date": "2026-06-11", "price_min": "10", "price_max": "20"},
    ]
    series = eda.build_series_for_plot(series_rows, "A")
    assert [d for d, _, _ in series] == ["2026-06-11", "2026-06-13"]
    assert series[0] == ("2026-06-11", 50.0, 75.0)


def test_dt_from_uri():
    assert eda.dt_from_uri("gs://b/ticketmaster/dt=2026-06-25/x.parquet") == "2026-06-25"
    assert eda.dt_from_uri("gs://b/ticketmaster/nope.parquet") is None


def test_plot_smoke(tmp_path):
    pytest.importorskip("matplotlib")
    out = tmp_path / "p.png"
    eda.plot_price_trajectory(
        [("2026-06-11", 50.0, 75.0), ("2026-06-12", 55.0, 80.0)], "T", out)
    assert out.exists() and out.stat().st_size > 0
