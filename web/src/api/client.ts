import type { ShowDetail, ShowSummary } from "../types";

const DEFAULT_DEV_API_BASE_URL = "http://127.0.0.1:8000";

export function apiBaseUrl(): string {
  const override = import.meta.env.VITE_API_BASE_URL;
  if (override) {
    return override.replace(/\/$/, "");
  }
  // A production build is served same-origin by the API container, so use relative URLs.
  // Local dev keeps the standalone FastAPI host.
  return import.meta.env.PROD ? "" : DEFAULT_DEV_API_BASE_URL;
}

async function getJson<T>(path: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(`${apiBaseUrl()}${path}`, { signal });

  if (!response.ok) {
    throw new Error(`API request failed: ${response.status} ${response.statusText}`);
  }

  try {
    return (await response.json()) as T;
  } catch {
    throw new Error(`API request returned malformed JSON for ${path}`);
  }
}

export function fetchShows(signal?: AbortSignal): Promise<ShowSummary[]> {
  return getJson<ShowSummary[]>("/shows", signal);
}

export function fetchShow(eventId: string, signal?: AbortSignal): Promise<ShowDetail> {
  return getJson<ShowDetail>(`/show/${encodeURIComponent(eventId)}`, signal);
}
