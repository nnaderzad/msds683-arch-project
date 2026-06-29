import { useEffect, useMemo, useState } from "react";
import { fetchShow, fetchShows } from "./api/client";
import { DemandSignalsChart } from "./components/DemandSignalsChart";
import { MetricCard } from "./components/MetricCard";
import type { ShowDetail, ShowSummary } from "./types";
import { formatDate, formatMoney, formatNumber } from "./utils/formatters";

type LoadState = "idle" | "loading" | "success" | "error";

const HERO_EVENT_IDS = [
  "rZ7HnEZ1Af00jd",
  "rZ7HnEZ1Af-P_K",
  "rZ7HnEZ1AfP6oN",
  "rZ7HnEZ1AfPJGS",
  "rZ7HnEZ1AfPJGN",
  "rZ7HnEZ1AfPtkd",
] as const;
const MAX_DROPDOWN_SHOWS = 250;

function isAbortError(error: unknown): boolean {
  return error instanceof DOMException && error.name === "AbortError";
}

function hasValue(value: number | null): boolean {
  return value !== null && Number.isFinite(value);
}

function showScore(show: ShowSummary): number {
  const heroIndex = HERO_EVENT_IDS.indexOf(show.event_id as (typeof HERO_EVENT_IDS)[number]);
  if (heroIndex >= 0) {
    return 10_000 - heroIndex;
  }

  return (
    (hasValue(show.forecast_price) ? 80 : 0) +
    (hasValue(show.price_min) || hasValue(show.price_max) ? 40 : 0) +
    (hasValue(show.local_interest) ? 25 : 0) +
    (hasValue(show.yt_views) ? 25 : 0) +
    (show.artist_name ? 5 : 0)
  );
}

function prepareDropdownShows(liveShows: ShowSummary[]): ShowSummary[] {
  return [...liveShows]
    .sort((left, right) => {
      const scoreDelta = showScore(right) - showScore(left);
      if (scoreDelta !== 0) {
        return scoreDelta;
      }

      const dateDelta = Date.parse(left.show_date) - Date.parse(right.show_date);
      if (dateDelta !== 0) {
        return dateDelta;
      }

      return left.event_name.localeCompare(right.event_name);
    })
    .slice(0, MAX_DROPDOWN_SHOWS);
}

function formatShowOption(show: ShowSummary): string {
  return `${show.artist_name || show.event_name} at ${show.venue_name}`;
}

function App() {
  const [shows, setShows] = useState<ShowSummary[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [selectedShow, setSelectedShow] = useState<ShowDetail | null>(null);
  const [showsState, setShowsState] = useState<LoadState>("idle");
  const [showState, setShowState] = useState<LoadState>("idle");
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();

    setShowsState("loading");
    setErrorMessage(null);

    fetchShows(controller.signal)
      .then((liveShows) => {
        if (controller.signal.aborted) {
          return;
        }

        const dropdownShows = prepareDropdownShows(liveShows);
        setShows(dropdownShows);
        setSelectedId((current) => current || dropdownShows[0]?.event_id || "");
        setShowsState("success");
      })
      .catch((error: unknown) => {
        if (isAbortError(error) || controller.signal.aborted) {
          return;
        }

        setShows([]);
        setSelectedId("");
        setSelectedShow(null);
        setShowsState("error");
        setErrorMessage("Could not load the live show list. Confirm the FastAPI service is running.");
      });

    return () => controller.abort();
  }, []);

  useEffect(() => {
    if (!selectedId) {
      setSelectedShow(null);
      setShowState("idle");
      return;
    }

    const controller = new AbortController();

    setShowState("loading");
    setErrorMessage(null);

    fetchShow(selectedId, controller.signal)
      .then((show) => {
        if (controller.signal.aborted) {
          return;
        }

        setSelectedShow(show);
        setShowState("success");
      })
      .catch((error: unknown) => {
        if (isAbortError(error) || controller.signal.aborted) {
          return;
        }

        setSelectedShow(null);
        setShowState("error");
        setErrorMessage("Could not load the selected show. Try another show or restart the API.");
      });

    return () => controller.abort();
  }, [selectedId]);

  const selectedSummary = useMemo(
    () => shows.find((show) => show.event_id === selectedId) ?? null,
    [selectedId, shows],
  );
  const summaryShow = selectedShow ?? selectedSummary;
  const isLoadingShows = showsState === "loading";
  const isLoadingSelectedShow = showState === "loading" && !selectedShow;

  return (
    <main className="app-shell">
      <header className="app-header">
        <div>
          <p className="eyebrow">Team Shakshuka</p>
          <h1>Live Music Demand Dashboard</h1>
          <p className="header-copy">
            Compare ticket movement, local interest, artist attention, and forecast signals for
            curated live music events.
          </p>
        </div>
        <label className="show-picker">
          <span>Demo show</span>
          <select
            value={selectedId}
            onChange={(event) => setSelectedId(event.target.value)}
            aria-label="Demo show"
            disabled={isLoadingShows || shows.length === 0}
          >
            {isLoadingShows && <option value="">Loading live shows...</option>}
            {!isLoadingShows && shows.length === 0 && <option value="">No shows available</option>}
            {shows.map((show) => (
              <option key={show.event_id} value={show.event_id}>
                {formatShowOption(show)}
              </option>
            ))}
          </select>
        </label>
      </header>

      {errorMessage && (
        <section className="status-panel is-error" role="alert">
          <strong>Live API issue</strong>
          <p>{errorMessage}</p>
        </section>
      )}

      {isLoadingShows && (
        <section className="status-panel" aria-live="polite">
          <strong>Loading live shows</strong>
          <p>Fetching the dropdown from the FastAPI `/shows` endpoint.</p>
        </section>
      )}

      {!isLoadingShows && !summaryShow && !errorMessage && (
        <section className="status-panel" aria-live="polite">
          <strong>No demo shows returned</strong>
          <p>The API responded, but it did not return any shows for the dropdown.</p>
        </section>
      )}

      {summaryShow && (
        <section className="summary-band" aria-label="Selected show summary">
          <div>
            <p className="eyebrow">Selected show</p>
            <h2>{summaryShow.event_name}</h2>
            <p>
              {summaryShow.venue_name} · {summaryShow.city}, {summaryShow.state_code} ·{" "}
              {formatDate(summaryShow.show_date)}
            </p>
          </div>
          <div className="metric-row">
            <MetricCard
              label="Price range"
              value={`${formatMoney(summaryShow.price_min)} - ${formatMoney(summaryShow.price_max)}`}
            />
            <MetricCard label="Local interest" value={formatNumber(summaryShow.local_interest)} />
            <MetricCard label="YouTube views" value={formatNumber(summaryShow.yt_views)} />
            <MetricCard label="Forecast lowest price" value={formatMoney(summaryShow.forecast_price)} />
          </div>
        </section>
      )}

      {isLoadingSelectedShow && (
        <section className="status-panel" aria-live="polite">
          <strong>Loading selected show</strong>
          <p>Fetching history and forecast rows from /show/event_id.</p>
        </section>
      )}

      {selectedShow && <DemandSignalsChart show={selectedShow} />}
    </main>
  );
}

export default App;
