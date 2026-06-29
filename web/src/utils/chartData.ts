import type { HistoryPoint, ShowDetail } from "../types";
import { formatShortDate } from "./formatters";

export const signalOptions = [
  { key: "price", label: "Observed lowest price", color: "#2f6f96" },
  { key: "forecast", label: "Forecast lowest price", color: "#2f6f96" },
  { key: "trends", label: "Local popularity", color: "#3f8f5f" },
  { key: "youtube", label: "Global popularity", color: "#dd6b20" },
] as const;

export type SignalKey = (typeof signalOptions)[number]["key"];
export type SignalVisibility = Record<SignalKey, boolean>;
export type SignalAvailability = Record<SignalKey, boolean>;

export type ChartRow = {
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

type RawChartRow = {
  date: string;
  observedPriceRaw: number | null;
  forecastPriceRaw: number | null;
  trendsRaw: number | null;
  youtubeRaw: number | null;
};

export function latestHistory(show: ShowDetail): HistoryPoint | null {
  return show.history.at(-1) ?? null;
}

export function observedLowestPrice(priceMin: number | null): number | null {
  return priceMin;
}

export function dateKey(value: string): string {
  return new Date(value).toISOString().slice(0, 10);
}

export function dateFromDaysToShow(showDate: string, daysToShow: number): string {
  const date = new Date(showDate);
  date.setDate(date.getDate() - daysToShow);
  return date.toISOString().slice(0, 10);
}

export function createIndexScale(values: Array<number | null>) {
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

export function buildDemandChartData(show: ShowDetail): ChartRow[] {
  const rowsByDate = new Map<string, RawChartRow>();
  const showIso = dateKey(show.show_date);

  show.history.forEach((point) => {
    const snapshotDate = dateKey(point.snapshot_date);

    // Nothing about an event should accrue after it happens: drop snapshots taken
    // past the show date so popularity and observed price both end at the show.
    if (snapshotDate > showIso) {
      return;
    }

    rowsByDate.set(snapshotDate, {
      date: snapshotDate,
      observedPriceRaw: observedLowestPrice(point.price_min),
      forecastPriceRaw: null,
      trendsRaw: point.local_interest,
      youtubeRaw: point.yt_views,
    });
  });

  // Anchor the forecast to the latest *retained* snapshot (post-show rows are filtered
  // out above), not the raw history, so a past show's chart still ends at the show date.
  const latestRow = Array.from(rowsByDate.values()).reduce<RawChartRow | null>(
    (latest, row) => (latest === null || row.date > latest.date ? row : latest),
    null,
  );
  const latestDate = latestRow ? latestRow.date : null;
  const latestObservedPrice = latestRow ? latestRow.observedPriceRaw : null;

  if (latestDate && latestObservedPrice !== null) {
    rowsByDate.set(latestDate, {
      ...(rowsByDate.get(latestDate) ?? {
        date: latestDate,
        observedPriceRaw: null,
        trendsRaw: null,
        youtubeRaw: null,
      }),
      forecastPriceRaw: latestObservedPrice,
    });
  }

  show.forecast.forEach((point) => {
    if (!Number.isFinite(point.predicted_price)) {
      return;
    }

    const forecastDate = dateFromDaysToShow(show.show_date, point.days_to_show);

    if (latestDate && forecastDate <= latestDate) {
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
}

export function getSignalAvailability(rows: ChartRow[]): SignalAvailability {
  return {
    price: rows.some((row) => row.observedPriceRaw !== null),
    forecast: rows.some((row) => row.forecastPriceRaw !== null && row.observedPriceRaw === null),
    trends: rows.some((row) => row.trendsRaw !== null),
    youtube: rows.some((row) => row.youtubeRaw !== null),
  };
}

export function priceAxisDomain(rows: ChartRow[]): [number, number] {
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
