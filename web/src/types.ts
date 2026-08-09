export type HistoryPoint = {
  snapshot_date: string;
  days_to_show: number;
  price_min: number | null;
  price_max: number | null;
  local_interest: number | null;
  yt_subscribers: number | null;
  yt_views: number | null;
};

export type ForecastPoint = {
  days_to_show: number;
  predicted_price: number;
};

// Same series as HistoryPoint but with interior price gaps filled by carrying the
// last observed price forward; price_is_filled marks carried (not observed) rows.
export type FilledHistoryPoint = {
  snapshot_date: string;
  days_to_show: number | null;
  price_min: number | null;
  price_max: number | null;
  price_is_filled: boolean;
};

export type ShowSummary = {
  event_id: string;
  event_name: string;
  artist_name: string | null;
  venue_name: string;
  city: string;
  state_code: string;
  show_date: string;
  status_code: string;
  price_min: number | null;
  price_max: number | null;
  local_interest: number | null;
  yt_subscribers: number | null;
  yt_views: number | null;
  forecast_price: number | null;
};

export type ShowDetail = ShowSummary & {
  history: HistoryPoint[];
  forecast: ForecastPoint[];
  // Optional so older API responses (without the field) still render observed-only.
  history_filled?: FilledHistoryPoint[];
};

export type GuardrailVerdict = {
  name: string;
  passed: boolean;
  detail: string;
};

export type AskStatus = "ok" | "refused" | "blocked" | "rate_limited" | "error";

export type AskResponse = {
  status: AskStatus;
  question: string;
  dataset?: "real" | "synth";
  synthetic?: boolean;
  sql?: string | null;
  rows?: Record<string, unknown>[];
  row_count?: number;
  truncated?: boolean;
  answer?: string | null;
  guardrails?: GuardrailVerdict[];
  bytes_processed?: number | null;
  model?: string;
  latency_ms?: number;
};
