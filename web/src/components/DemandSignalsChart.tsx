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
import type { ShowDetail } from "../types";
import {
  buildDemandChartData,
  dateKey,
  getSignalAvailability,
  latestHistory,
  observedLowestPrice,
  priceAxisDomain,
  signalOptions,
  type ChartRow,
  type SignalKey,
  type SignalVisibility,
} from "../utils/chartData";
import { formatAxisMoney, formatMoney, formatNumber, formatShortDate } from "../utils/formatters";
import { MetricCard } from "./MetricCard";

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

type DemandSignalsChartProps = {
  show: ShowDetail;
};

const defaultVisibility: SignalVisibility = {
  price: true,
  forecast: true,
  trends: true,
  youtube: true,
};

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

export function DemandSignalsChart({ show }: DemandSignalsChartProps) {
  const [visibleSignals, setVisibleSignals] = useState<SignalVisibility>(defaultVisibility);
  const combinedData = useMemo(() => buildDemandChartData(show), [show]);
  const signalAvailability = useMemo(() => getSignalAvailability(combinedData), [combinedData]);
  const priceDomain = useMemo(() => priceAxisDomain(combinedData), [combinedData]);
  const latest = latestHistory(show);
  const showDateLabel = formatShortDate(dateKey(show.show_date));
  const hasVisibleSignals = signalOptions.some(
    (signal) => visibleSignals[signal.key] && signalAvailability[signal.key],
  );

  const toggleSignal = (signal: SignalKey) => {
    if (!signalAvailability[signal]) {
      return;
    }

    setVisibleSignals((current) => ({
      ...current,
      [signal]: !current[signal],
    }));
  };

  return (
    <section className="combined-panel" aria-label="Demand signals over time">
      <div className="combined-heading">
        <div>
          <p className="eyebrow">Combined signal view</p>
          <h3>Demand Signals Over Time</h3>
          <p>
            Price uses the right dollar axis; Trends and YouTube use the left 0-100 index so demand
            signals can still be compared.
          </p>
        </div>
        <div className="signal-controls" aria-label="Signal toggles">
          {signalOptions.map((signal) => {
            const disabled = !signalAvailability[signal.key];

            return (
              <label key={signal.key} className={disabled ? "is-disabled" : undefined}>
                <input
                  type="checkbox"
                  style={{ accentColor: signal.color }}
                  checked={signalAvailability[signal.key] && visibleSignals[signal.key]}
                  disabled={disabled}
                  onChange={() => toggleSignal(signal.key)}
                />
                <span>{signal.label}</span>
              </label>
            );
          })}
        </div>
      </div>

      <div className="combined-chart">
        {!hasVisibleSignals && (
          <div className="empty-chart-message">No selected signals are available for this show.</div>
        )}
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
              tickFormatter={(value) => formatAxisMoney(Number(value))}
              width={64}
            />
            <Tooltip content={<DemandTooltip />} />
            <Legend />
            {visibleSignals.price && signalAvailability.price && (
              <Line
                type="monotone"
                dataKey="price"
                yAxisId="price"
                name="Observed lowest price"
                stroke="#2f6f96"
                strokeWidth={3}
                dot={{ r: 4 }}
                connectNulls={false}
                isAnimationActive={false}
              />
            )}
            {visibleSignals.forecast && signalAvailability.forecast && (
              <Line
                type="monotone"
                dataKey="forecast"
                yAxisId="price"
                name="Forecast lowest price"
                stroke="#2f6f96"
                strokeWidth={3}
                strokeDasharray="8 6"
                dot={{ r: 4 }}
                connectNulls={false}
                isAnimationActive={false}
              />
            )}
            {visibleSignals.trends && signalAvailability.trends && (
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
            {visibleSignals.youtube && signalAvailability.youtube && (
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
        Left axis is indexed 0-100 for Trends and YouTube. Right axis shows observed and forecasted
        lowest ticket price in dollars and auto-scales per selected show. The vertical marker labels
        the show date.
      </p>

      <div className="combined-notes">
        <MetricCard
          label="Latest lowest price"
          value={formatMoney(observedLowestPrice(latest?.price_min ?? null))}
        />
        <MetricCard label="Trend signal" value={formatNumber(latest?.local_interest ?? null)} />
        <MetricCard label="YouTube signal" value={formatNumber(latest?.yt_views ?? null)} />
        <MetricCard label="Forecasted lowest price" value={formatMoney(show.forecast_price)} />
      </div>
    </section>
  );
}
