import { useMemo, useState } from "react";
import type { ShowDetail } from "./types";
import { mockShows } from "./data/mockShows";

const currency = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  maximumFractionDigits: 0,
});

const number = new Intl.NumberFormat("en-US", {
  notation: "compact",
  maximumFractionDigits: 1,
});

function formatDate(value: string): string {
  return new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
  }).format(new Date(value));
}

function formatMoney(value: number | null): string {
  return value === null ? "No price" : currency.format(value);
}

function formatNumber(value: number | null): string {
  return value === null ? "No signal" : number.format(value);
}

function formatSubscriberSignal(value: number | null): string {
  return value === null ? "Views only" : `${formatNumber(value)} subs`;
}

function latestHistory(show: ShowDetail) {
  return show.history[show.history.length - 1];
}

function App() {
  const [selectedId, setSelectedId] = useState(mockShows[0].event_id);

  const selectedShow = useMemo(
    () => mockShows.find((show) => show.event_id === selectedId) ?? mockShows[0],
    [selectedId],
  );
  const latest = latestHistory(selectedShow);
  const priceRange = `${formatMoney(selectedShow.price_min)} - ${formatMoney(
    selectedShow.price_max,
  )}`;

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
          >
            {mockShows.map((show) => (
              <option key={show.event_id} value={show.event_id}>
                {show.artist_name} at {show.venue_name}
              </option>
            ))}
          </select>
        </label>
      </header>

      <section className="summary-band" aria-label="Selected show summary">
        <div>
          <p className="eyebrow">Selected show</p>
          <h2>{selectedShow.event_name}</h2>
          <p>
            {selectedShow.venue_name} · {selectedShow.city}, {selectedShow.state_code} ·{" "}
            {formatDate(selectedShow.show_date)}
          </p>
        </div>
        <div className="metric-row">
          <Metric label="Price range" value={priceRange} />
          <Metric label="Local interest" value={formatNumber(selectedShow.local_interest)} />
          <Metric label="YouTube views" value={formatNumber(selectedShow.yt_views)} />
          <Metric label="Forecast price" value={formatMoney(selectedShow.forecast_price)} />
        </div>
      </section>

      <section className="chart-grid" aria-label="Chart placeholders">
        <ChartShell
          title="Ticket Price History"
          value={priceRange}
          detail={`${selectedShow.history.length} snapshots · latest ${formatMoney(latest?.price_min ?? null)}`}
          seriesLabel="price_min / price_max over snapshot_date"
        />
        <ChartShell
          title="Google Trends Local Interest"
          value={formatNumber(selectedShow.local_interest)}
          detail="DMA-local 0-100 interest signal"
          seriesLabel="local_interest over snapshot_date"
        />
        <ChartShell
          title="YouTube Artist Signal"
          value={formatSubscriberSignal(selectedShow.yt_subscribers)}
          detail={`${formatNumber(selectedShow.yt_views)} total views`}
          seriesLabel="yt_subscribers / yt_views over snapshot_date"
        />
        <ChartShell
          title="Forecast"
          value={formatMoney(selectedShow.forecast_price)}
          detail={`${selectedShow.forecast.length} forecast points`}
          seriesLabel="predicted_price over days_to_show"
        />
      </section>
    </main>
  );
}

type MetricProps = {
  label: string;
  value: string;
};

function Metric({ label, value }: MetricProps) {
  return (
    <div className="metric">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

type ChartShellProps = {
  title: string;
  value: string;
  detail: string;
  seriesLabel: string;
};

function ChartShell({ title, value, detail, seriesLabel }: ChartShellProps) {
  return (
    <article className="chart-shell">
      <div className="chart-heading">
        <h3>{title}</h3>
        <strong>{value}</strong>
      </div>
      <div className="chart-placeholder" aria-label={`${title} chart placeholder`}>
        <div className="axis horizontal" />
        <div className="axis vertical" />
        <div className="trend-line" />
      </div>
      <p>{detail}</p>
      <small>{seriesLabel}</small>
    </article>
  );
}

export default App;
