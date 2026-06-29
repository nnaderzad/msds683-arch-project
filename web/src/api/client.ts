import type { ShowDetail, ShowSummary } from "../types";

const DEFAULT_API_BASE_URL = "http://127.0.0.1:8000";

export function apiBaseUrl(): string {
  return (import.meta.env.VITE_API_BASE_URL || DEFAULT_API_BASE_URL).replace(/\/$/, "");
}

async function getJson<T>(path: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(`${apiBaseUrl()}${path}`, { signal });

  if (!response.ok) {
    throw new Error(`API request failed: ${response.status} ${response.statusText}`);
  }

  return response.json() as Promise<T>;
}

export function fetchShows(signal?: AbortSignal): Promise<ShowSummary[]> {
  return getJson<ShowSummary[]>("/shows", signal);
}

export function fetchShow(eventId: string, signal?: AbortSignal): Promise<ShowDetail> {
  return getJson<ShowDetail>(`/show/${encodeURIComponent(eventId)}`, signal);
}
