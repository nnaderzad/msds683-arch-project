import { useMemo, useState } from "react";
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
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

const signalOptions = [
  { key: "price", label: "Observed price", color: "#2f6f96" },
  { key: "forecast", label: "Forecast price", color: "#2f6f96" },
  { key: "trends", label: "Google Trends", color: "#3f8f5f" },
  { key: "youtube", label: "YouTube views", color: "#b7791f" },
] as const;

type SignalKey = (typeof signalOptions)[number]["key"];
type SignalVisibility = Record<SignalKey, boolean>;
type ChartRow = {
  date: string;
  label: string;
  observedPriceRaw: number | null;
  forecastPriceRaw: number | null;
  trendsRaw: number | null;
  youtubeRaw: number | null;
  price: number | null;
  forecast: number | null;
  trends: number | null;
  youtube: number | null;
};
type ChartTooltipPayload = {
  color?: string;
  dataKey?: string | number;
  name?: string | number;
  payload?: ChartRow;
  value?: number;
};
type DemandTooltipProps = {
  active?: boolean;
  label?: string;
  payload?: ChartTooltipPayload[];
};

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

function priceMidpoint(priceMin: number | null, priceMax: number | null): number | null {
  if (priceMin !== null && priceMax !== null) {
    return (priceMin + priceMax) / 2;
  }

  return priceMin ?? priceMax;
}

function dateKey(value: string): string {
  return new Date(value).toISOString().slice(0, 10);
}

function dateFromDaysToShow(showDate: string, daysToShow: number): string {
  const date = new Date(showDate);
  date.setDate(date.getDate() - daysToShow);
  return date.toISOString().slice(0, 10);
}

function formatShortDate(value: string): string {
  return new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "numeric",
  }).format(new Date(`${value}T00:00:00`));
}

function createIndexScale(values: Array<number | null>) {
  const floor = 15;
  const ceiling = 85;
  const presentValues = values.filter((value): value is number => value !== null);

  if (presentValues.length === 0) {
    return () => null;
  }

  const min = Math.min(...presentValues);
  const max = Math.max(...presentValues);

  return (value: number | null) => {
    if (value === null) {
      return null;
    }

    if (min === max) {
      return 50;
    }

    return floor + ((value - min) / (max - min)) * (ceiling - floor);
  };
}

function priceAxisDomain(rows: ChartRow[]): [number, number] {
  const values = rows
    .flatMap((row) => [row.observedPriceRaw, row.forecastPriceRaw])
    .filter((value): value is number => value !== null);

  if (values.length === 0) {
    return [0, 100];
  }

  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = max - min;
  const padding = range === 0 ? Math.max(10, max * 0.1) : Math.max(8, range * 0.25);

  return [Math.max(0, Math.floor(min - padding)), Math.ceil(max + padding)];
}

function tooltipRawValue(row: ChartRow | undefined, key: string): string {
  if (!row) {
    return "No data";
  }

  if (key === "price") {
    return formatMoney(row.observedPriceRaw);
  }

  if (key === "forecast") {
    return formatMoney(row.forecastPriceRaw);
  }

  if (key === "trends") {
    return row.trendsRaw === null ? "No signal" : `${row.trendsRaw} interest`;
  }

  if (key === "youtube") {
    return row.youtubeRaw === null ? "No signal" : `${formatNumber(row.youtubeRaw)} views`;
  }

  return "No data";
}

function tooltipScaleLabel(key: string, value: number | undefined): string {
  if (key === "price" || key === "forecast") {
    return "Right axis: price";
  }

  return `Indexed: ${Math.round(Number(value ?? 0))}`;
}

function DemandTooltip({ active, label, payload }: DemandTooltipProps) {
  if (!active || !payload?.length) {
    return null;
  }

  return (
    <div className="chart-tooltip">
      <strong>{label}</strong>
      {payload.map((item) => {
        const key = String(item.dataKey);
        const row = item.payload as ChartRow | undefined;

        return (
          <div key={key} className="tooltip-row">
            <span style={{ backgroundColor: item.color }} />
            <p>
              {item.name}: <b>{tooltipRawValue(row, key)}</b>
              <small>{tooltipScaleLabel(key, item.value)}</small>
            </p>
          </div>
        );
      })}
    </div>
  );
}

function App() {
  const [selectedId, setSelectedId] = useState(mockShows[0].event_id);
  const [visibleSignals, setVisibleSignals] = useState<SignalVisibility>({
    price: true,
    forecast: true,
    trends: true,
    youtube: true,
  });

  const selectedShow = useMemo(
    () => mockShows.find((show) => show.event_id === selectedId) ?? mockShows[0],
    [selectedId],
  );
  const latest = latestHistory(selectedShow);
  const priceRange = `${formatMoney(selectedShow.price_min)} - ${formatMoney(
    selectedShow.price_max,
  )}`;
  const showDateLabel = formatShortDate(dateKey(selectedShow.show_date));
  const combinedData = useMemo(() => {
    const latestPoint = latestHistory(selectedShow);
    const latestDate = dateKey(latestPoint.snapshot_date);
    const latestObservedPrice = priceMidpoint(latestPoint.price_min, latestPoint.price_max);
    const rowsByDate = new Map<
      string,
      {
        date: string;
        observedPriceRaw: number | null;
        forecastPriceRaw: number | null;
        trendsRaw: number | null;
        youtubeRaw: number | null;
      }
    >();

    selectedShow.history.forEach((point) => {
      rowsByDate.set(dateKey(point.snapshot_date), {
        date: dateKey(point.snapshot_date),
        observedPriceRaw: priceMidpoint(point.price_min, point.price_max),
        forecastPriceRaw: null,
        trendsRaw: point.local_interest,
        youtubeRaw: point.yt_views,
      });
    });

    rowsByDate.set(latestDate, {
      ...(rowsByDate.get(latestDate) ?? {
        date: latestDate,
        observedPriceRaw: null,
        trendsRaw: null,
        youtubeRaw: null,
      }),
      forecastPriceRaw: latestObservedPrice,
    });

    selectedShow.forecast.forEach((point) => {
      const forecastDate = dateFromDaysToShow(selectedShow.show_date, point.days_to_show);

      if (forecastDate <= latestDate) {
        return;
      }

      rowsByDate.set(forecastDate, {
        ...(rowsByDate.get(forecastDate) ?? {
          date: forecastDate,
          observedPriceRaw: null,
          trendsRaw: null,
          youtubeRaw: null,
        }),
        forecastPriceRaw: point.predicted_price,
      });
    });

    const rows = Array.from(rowsByDate.values()).sort((left, right) =>
      left.date.localeCompare(right.date),
    );
    const youtubeScale = createIndexScale(rows.map((row) => row.youtubeRaw));

    return rows.map((row): ChartRow => ({
      ...row,
      label: formatShortDate(row.date),
      price: row.observedPriceRaw,
      forecast: row.forecastPriceRaw,
      trends: row.trendsRaw,
      youtube: youtubeScale(row.youtubeRaw),
    }));
  }, [selectedShow]);
  const priceDomain = useMemo(() => priceAxisDomain(combinedData), [combinedData]);

  const toggleSignal = (signal: SignalKey) => {
    setVisibleSignals((current) => ({
      ...current,
      [signal]: !current[signal],
    }));
  };

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

      <section className="combined-panel" aria-label="Demand signals over time">
        <div className="combined-heading">
          <div>
            <p className="eyebrow">Combined signal view</p>
            <h3>Demand Signals Over Time</h3>
            <p>
              Price uses the right dollar axis; Trends and YouTube use the left 0-100 index so
              demand signals can still be compared.
            </p>
          </div>
          <div className="signal-controls" aria-label="Signal toggles">
            {signalOptions.map((signal) => (
              <label key={signal.key}>
                <input
                  type="checkbox"
                  style={{ accentColor: signal.color }}
                  checked={visibleSignals[signal.key]}
                  onChange={() => toggleSignal(signal.key)}
                />
                <span>{signal.label}</span>
              </label>
            ))}
          </div>
        </div>

        <div className="combined-chart">
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={combinedData} margin={{ top: 12, right: 56, bottom: 4, left: -16 }}>
              <CartesianGrid stroke="#e2edf5" vertical={false} />
              <XAxis dataKey="label" tickLine={false} axisLine={{ stroke: "#bdd7e8" }} />
              <YAxis
                yAxisId="index"
                domain={[0, 105]}
                ticks={[0, 25, 50, 75, 100]}
                tickLine={false}
                axisLine={{ stroke: "#bdd7e8" }}
                tickFormatter={(value) => `${value}`}
              />
              <YAxis
                yAxisId="price"
                orientation="right"
                domain={priceDomain}
                tickLine={false}
                axisLine={{ stroke: "#8bb8d4" }}
                tickFormatter={(value) => currency.format(Number(value))}
                width={64}
              />
              <Tooltip content={<DemandTooltip />} />
              <Legend />
              {visibleSignals.price && (
                <Line
                  type="monotone"
                  dataKey="price"
                  yAxisId="price"
                  name="Observed price"
                  stroke="#2f6f96"
                  strokeWidth={3}
                  dot={{ r: 4 }}
                  connectNulls={false}
                  isAnimationActive={false}
                />
              )}
              {visibleSignals.forecast && (
                <Line
                  type="monotone"
                  dataKey="forecast"
                  yAxisId="price"
                  name="Forecast price"
                  stroke="#2f6f96"
                  strokeWidth={3}
                  strokeDasharray="8 6"
                  dot={{ r: 4 }}
                  connectNulls={false}
                  isAnimationActive={false}
                />
              )}
              {visibleSignals.trends && (
                <Line
                  type="monotone"
                  dataKey="trends"
                  yAxisId="index"
                  name="Google Trends"
                  stroke="#3f8f5f"
                  strokeWidth={3}
                  dot={{ r: 4 }}
                  connectNulls={false}
                  isAnimationActive={false}
                />
              )}
              {visibleSignals.youtube && (
                <Line
                  type="monotone"
                  dataKey="youtube"
                  yAxisId="index"
                  name="YouTube views"
                  stroke="#b7791f"
                  strokeWidth={3}
                  dot={{ r: 4 }}
                  connectNulls={false}
                  isAnimationActive={false}
                />
              )}
              <ReferenceLine
                x={showDateLabel}
                stroke="#64748b"
                strokeDasharray="4 4"
                label={{
                  value: "Show date",
                  position: "insideTopLeft",
                  fill: "#475569",
                  fontSize: 12,
                  fontWeight: 700,
                }}
              />
            </LineChart>
          </ResponsiveContainer>
        </div>
        <p className="axis-note">
          Left axis is indexed 0-100 for Trends and YouTube. Right axis shows observed and
          forecasted price in dollars and auto-scales per selected show. The vertical marker labels
          the show date.
        </p>

        <div className="combined-notes">
          <Metric
            label="Price signal"
            value={`Midpoint ${formatMoney(priceMidpoint(latest?.price_min ?? null, latest?.price_max ?? null))}`}
          />
          <Metric label="Trend signal" value={formatNumber(latest?.local_interest ?? null)} />
          <Metric label="YouTube signal" value={formatNumber(latest?.yt_views ?? null)} />
          <Metric
            label="Forecasted price"
            value={formatMoney(selectedShow.forecast_price)}
          />
        </div>
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

export default App;
