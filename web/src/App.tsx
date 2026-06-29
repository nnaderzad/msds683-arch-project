import { useMemo, useState } from "react";
import { DemandSignalsChart } from "./components/DemandSignalsChart";
import { MetricCard } from "./components/MetricCard";
import { mockShows } from "./data/mockShows";
import { formatDate, formatMoney, formatNumber } from "./utils/formatters";

function App() {
  const [selectedId, setSelectedId] = useState(mockShows[0].event_id);
  const selectedShow = useMemo(
    () => mockShows.find((show) => show.event_id === selectedId) ?? mockShows[0],
    [selectedId],
  );
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
          <MetricCard label="Price range" value={priceRange} />
          <MetricCard label="Local interest" value={formatNumber(selectedShow.local_interest)} />
          <MetricCard label="YouTube views" value={formatNumber(selectedShow.yt_views)} />
          <MetricCard label="Forecast price" value={formatMoney(selectedShow.forecast_price)} />
        </div>
      </section>

      <DemandSignalsChart show={selectedShow} />
    </main>
  );
}

export default App;
