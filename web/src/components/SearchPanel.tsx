import { useEffect, useState } from "react";
import { fetchGenres, searchShows } from "../api/client";
import type { ShowSummary } from "../types";
import { formatDate } from "../utils/formatters";

// The Bay Area is DMA 807 — the project's home metro and the demo's default story
// ("upcoming EDM shows in the Bay Area under $50").
const BAY_AREA_DMA = "807";

const HORIZONS = [
  { label: "Any time", value: "" },
  { label: "Next 7 days", value: "7" },
  { label: "Next 14 days", value: "14" },
  { label: "Next 30 days", value: "30" },
  { label: "Next 90 days", value: "90" },
];

function formatPrice(value: number | null): string {
  return value == null ? "—" : `$${value.toFixed(0)}`;
}

type Props = {
  onPick: (show: ShowSummary) => void;
};

export function SearchPanel({ onPick }: Props) {
  const [genres, setGenres] = useState<string[]>([]);
  const [q, setQ] = useState("");
  const [genre, setGenre] = useState("");
  const [bayAreaOnly, setBayAreaOnly] = useState(false);
  const [state, setState] = useState("");
  const [maxPrice, setMaxPrice] = useState("");
  const [horizon, setHorizon] = useState("30");
  const [results, setResults] = useState<ShowSummary[] | null>(null);
  const [phase, setPhase] = useState<"idle" | "loading" | "done" | "failed">("idle");

  useEffect(() => {
    const controller = new AbortController();
    fetchGenres(controller.signal)
      .then(setGenres)
      .catch(() => setGenres([]));
    return () => controller.abort();
  }, []);

  const submit = () => {
    if (phase === "loading") {
      return;
    }
    setPhase("loading");
    searchShows({
      q: q.trim() || undefined,
      genre: genre || undefined,
      dma: bayAreaOnly ? BAY_AREA_DMA : undefined,
      state: state.trim() || undefined,
      max_price: maxPrice.trim() ? Number(maxPrice) : undefined,
      days_ahead: horizon ? Number(horizon) : undefined,
      limit: 25,
    })
      .then((rows) => {
        setResults(rows);
        setPhase("done");
      })
      .catch(() => setPhase("failed"));
  };

  return (
    <section className="search-panel" aria-label="Search shows">
      <form
        className="search-fields"
        onSubmit={(event) => {
          event.preventDefault();
          submit();
        }}
      >
        <input
          value={q}
          onChange={(event) => setQ(event.target.value)}
          placeholder="Artist, event, or venue"
          aria-label="Search text"
          maxLength={80}
        />
        <select value={genre} onChange={(event) => setGenre(event.target.value)} aria-label="Genre">
          <option value="">All genres</option>
          {genres.map((value) => (
            <option key={value} value={value}>
              {value}
            </option>
          ))}
        </select>
        <input
          value={state}
          onChange={(event) => setState(event.target.value.toUpperCase())}
          placeholder="State (CA)"
          aria-label="State"
          maxLength={2}
          className="search-state"
        />
        <input
          value={maxPrice}
          onChange={(event) => setMaxPrice(event.target.value.replace(/[^0-9.]/g, ""))}
          placeholder="Max $ (projected)"
          aria-label="Max projected price"
          inputMode="numeric"
          className="search-price"
        />
        <select
          value={horizon}
          onChange={(event) => setHorizon(event.target.value)}
          aria-label="Time horizon"
        >
          {HORIZONS.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </select>
        <label className="search-bay">
          <input
            type="checkbox"
            checked={bayAreaOnly}
            onChange={(event) => setBayAreaOnly(event.target.checked)}
          />
          Bay Area only
        </label>
        <button type="submit" disabled={phase === "loading"}>
          {phase === "loading" ? "Searching…" : "Search"}
        </button>
      </form>

      {phase === "failed" && (
        <section className="status-panel is-error" role="alert">
          <strong>Search failed</strong>
          <p>Could not reach /search. Check the API and try again.</p>
        </section>
      )}

      {phase === "done" && results && results.length === 0 && (
        <p className="search-empty">No shows match those filters — try widening them.</p>
      )}

      {phase === "done" && results && results.length > 0 && (
        <div className="ask-table-wrap">
          <table className="ask-table search-results" aria-label="Search results">
            <thead>
              <tr>
                <th>Event</th>
                <th>Artist</th>
                <th>Venue</th>
                <th>Where</th>
                <th>Date</th>
                <th>Observed</th>
                <th>Projected</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {results.map((show) => (
                <tr key={show.event_id}>
                  <td>{show.event_name}</td>
                  <td>{show.artist_name ?? "—"}</td>
                  <td>{show.venue_name}</td>
                  <td>
                    {show.city}, {show.state_code}
                  </td>
                  <td>{formatDate(show.show_date)}</td>
                  <td>{formatPrice(show.price_min)}</td>
                  <td>{formatPrice(show.forecast_price)}</td>
                  <td>
                    <button type="button" className="search-view" onClick={() => onPick(show)}>
                      View
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
