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
};
