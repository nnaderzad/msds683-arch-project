import type {
  AskExchange,
  AskFeedbackRequest,
  AskFeedbackResponse,
  AskResponse,
  ShowDetail,
  ShowSummary,
} from "../types";

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

export type SearchParams = {
  q?: string;
  genre?: string;
  state?: string;
  dma?: string;
  max_price?: number;
  days_ahead?: number;
  limit?: number;
};

export function fetchGenres(signal?: AbortSignal): Promise<string[]> {
  return getJson<string[]>("/genres", signal);
}

export function searchShows(params: SearchParams, signal?: AbortSignal): Promise<ShowSummary[]> {
  const query = new URLSearchParams();
  Object.entries(params).forEach(([key, value]) => {
    if (value !== undefined && value !== null && `${value}` !== "") {
      query.set(key, String(value));
    }
  });
  const suffix = query.toString() ? `?${query.toString()}` : "";
  return getJson<ShowSummary[]>(`/search${suffix}`, signal);
}

export async function askQuestion(
  question: string,
  dataset: "real" | "synth" = "real",
  history?: AskExchange[],
  signal?: AbortSignal,
): Promise<AskResponse> {
  // history is at most 3 completed exchanges, older first; omit it when empty.
  const body =
    history && history.length > 0
      ? { question, dataset, history: history.slice(-3) }
      : { question, dataset };
  const response = await fetch(`${apiBaseUrl()}/ask`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal,
  });

  // /ask returns 200 with a status discriminator for every agent outcome; a non-200
  // here is a transport/validation problem, not an agent verdict.
  if (!response.ok) {
    throw new Error(`Ask request failed: ${response.status} ${response.statusText}`);
  }

  return (await response.json()) as AskResponse;
}

export async function sendAskFeedback(payload: AskFeedbackRequest): Promise<AskFeedbackResponse> {
  const response = await fetch(`${apiBaseUrl()}/ask_feedback`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });

  if (!response.ok) {
    throw new Error(`Feedback request failed: ${response.status} ${response.statusText}`);
  }

  return (await response.json()) as AskFeedbackResponse;
}
