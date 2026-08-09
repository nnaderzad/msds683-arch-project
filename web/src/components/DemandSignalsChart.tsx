import { useMemo, useState } from "react";
import {
  CartesianGrid,
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
import { formatNumber, formatPrice, formatShortDate } from "../utils/formatters";
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

type AxisLabelViewBox = { x?: number; y?: number; width?: number; height?: number };

// Custom left-axis label so "Local" (green) and "Global" (orange) are color-matched
// to their lines; a plain Recharts string label can only be a single color.
function PopularityAxisLabel({ viewBox }: { viewBox?: AxisLabelViewBox }) {
  const { x = 0, y = 0, height = 0 } = viewBox ?? {};
  const cx = x + 14;
  const cy = y + height / 2;

  return (
    <text
      x={cx}
      y={cy}
      transform={`rotate(-90, ${cx}, ${cy})`}
      textAnchor="middle"
      fontSize={12}
      fontWeight={600}
    >
      <tspan fill="#3f8f5f">Local interest</tspan>
      <tspan fill="#475569">{" & "}</tspan>
      <tspan fill="#dd6b20">YouTube</tspan>
      <tspan fill="#475569"> (0–100)</tspan>
    </text>
  );
}

type LegendSwatchProps = {
  color: string;
  dash?: string | null;
  marker?: boolean;
  hollow?: boolean;
};

// Legend swatch drawn with the same stroke/dash/marker as the series it names,
// so "dashed line" and "line with circles" are answerable from the legend itself.
function LegendSwatch({ color, dash, marker, hollow }: LegendSwatchProps) {
  return (
    <svg className="legend-swatch" width={28} height={12} viewBox="0 0 28 12" aria-hidden="true">
      <line
        x1={2}
        y1={6}
        x2={26}
        y2={6}
        stroke={color}
        strokeWidth={hollow ? 2 : 3}
        strokeDasharray={dash ?? undefined}
        strokeLinecap="round"
      />
      {marker &&
        (hollow ? (
          <circle cx={14} cy={6} r={4} fill="#ffffff" stroke={color} strokeWidth={2} />
        ) : (
          <circle cx={14} cy={6} r={4} fill={color} />
        ))}
    </svg>
  );
}

// Tooltip/series names come from the same legend entries so the two never drift.
const signalLabels = Object.fromEntries(
  signalOptions.map((signal) => [signal.key, signal.label]),
) as Record<SignalKey, string>;

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
    return formatPrice(row.observedPriceRaw);
  }

  if (key === "forecast") {
    return formatPrice(row.forecastPriceRaw);
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
  const showIso = dateKey(show.show_date);
  const showDateLabel = formatShortDate(showIso);
  // "Today" only makes sense for upcoming shows; for past shows the series ends at the
  // show date so today is off-chart. Anchor on the first category at/after today.
  const todayIso = new Date().toISOString().slice(0, 10);
  const todayLabel =
    todayIso < showIso ? combinedData.find((row) => row.date >= todayIso)?.label : undefined;
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
                  checked={signalAvailability[signal.key] && visibleSignals[signal.key]}
                  disabled={disabled}
                  onChange={() => toggleSignal(signal.key)}
                />
                <LegendSwatch color={signal.color} dash={signal.dash} marker={signal.marker} />
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
          <LineChart data={combinedData} margin={{ top: 12, right: 72, bottom: 4, left: 12 }}>
            <CartesianGrid stroke="#e2edf5" vertical={false} />
            <XAxis dataKey="label" tickLine={false} axisLine={{ stroke: "#bdd7e8" }} />
            <YAxis
              yAxisId="index"
              domain={[0, 105]}
              ticks={[0, 25, 50, 75, 100]}
              tickLine={false}
              axisLine={{ stroke: "#bdd7e8" }}
              tickFormatter={(value) => `${value}`}
              label={<PopularityAxisLabel />}
            />
            <YAxis
              yAxisId="price"
              orientation="right"
              domain={priceDomain}
              tickLine={false}
              axisLine={{ stroke: "#8bb8d4" }}
              tickFormatter={(value) => formatPrice(Number(value))}
              width={64}
              label={{
                value: "Ticket price ($)",
                angle: -90,
                position: "insideRight",
                style: { textAnchor: "middle", fill: "#475569", fontSize: 12, fontWeight: 600 },
              }}
            />
            <Tooltip content={<DemandTooltip />} />
            {visibleSignals.price && signalAvailability.price && (
              <Line
                type="monotone"
                dataKey="price"
                yAxisId="price"
                name={signalLabels.price}
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
                name={signalLabels.forecast}
                stroke="#2f6f96"
                strokeWidth={3}
                strokeDasharray="8 6"
                dot={false}
                connectNulls={false}
                isAnimationActive={false}
              />
            )}
            {visibleSignals.trends && signalAvailability.trends && (
              <Line
                type="monotone"
                dataKey="trends"
                yAxisId="index"
                name={signalLabels.trends}
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
                name={signalLabels.youtube}
                stroke="#dd6b20"
                strokeWidth={3}
                dot={{ r: 4 }}
                connectNulls={false}
                isAnimationActive={false}
              />
            )}
            {todayLabel && (
              <ReferenceLine
                x={todayLabel}
                stroke="#0f172a"
                strokeWidth={2}
                strokeDasharray="2 3"
                label={{
                  value: "Today",
                  position: "insideTopRight",
                  fill: "#0f172a",
                  fontSize: 12,
                  fontWeight: 700,
                }}
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

      <div className="combined-notes">
        <MetricCard
          label="Latest lowest price"
          value={formatPrice(observedLowestPrice(latest?.price_min ?? null))}
        />
        <MetricCard label="Trend signal" value={formatNumber(latest?.local_interest ?? null)} />
        <MetricCard label="YouTube signal" value={formatNumber(latest?.yt_views ?? null)} />
        <MetricCard label="Forecasted lowest price" value={formatPrice(show.forecast_price)} />
      </div>
    </section>
  );
}
