import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import App from "./App";
import { DemandSignalsChart } from "./components/DemandSignalsChart";
import { DEFAULT_HERO_EVENT_ID, HERO_SHOWS } from "./data/heroShows";
import type { ShowDetail, ShowSummary } from "./types";

// The dropdown renders from the pre-cached HERO_SHOWS (no /shows fetch); only the selected
// show is fetched live from /show/{id}. Derive fixtures from the real generated heroes.
const defaultHero = HERO_SHOWS.find((show) => show.event_id === DEFAULT_HERO_EVENT_ID)!;
const secondHero = HERO_SHOWS.find((show) => show.event_id !== DEFAULT_HERO_EVENT_ID)!;

const rx = (value: string) => new RegExp(value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), "i");

function detailFor(summary: ShowSummary): ShowDetail {
  // A full 3-source detail so every chart signal toggle is available.
  return {
    ...summary,
    local_interest: 55,
    yt_views: 21000,
    history: [
      {
        snapshot_date: "2026-06-24T00:00:00",
        days_to_show: 105,
        price_min: summary.price_min,
        price_max: summary.price_max,
        local_interest: 50,
        yt_subscribers: summary.yt_subscribers,
        yt_views: 20000,
      },
      {
        snapshot_date: "2026-06-25T00:00:00",
        days_to_show: 104,
        price_min: summary.price_min,
        price_max: summary.price_max,
        local_interest: 55,
        yt_subscribers: summary.yt_subscribers,
        yt_views: 21000,
      },
    ],
    forecast: [
      { days_to_show: 104, predicted_price: summary.price_min ?? 50 },
      { days_to_show: 0, predicted_price: summary.forecast_price ?? 60 },
    ],
  };
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function mockSuccessfulApi() {
  const fetchMock = vi.fn((input: RequestInfo | URL) => {
    const url = String(input);

    if (url.endsWith(`/show/${defaultHero.event_id}`)) {
      return Promise.resolve(jsonResponse(detailFor(defaultHero)));
    }

    if (url.endsWith(`/show/${secondHero.event_id}`)) {
      return Promise.resolve(jsonResponse(detailFor(secondHero)));
    }

    return Promise.resolve(jsonResponse({ detail: "Not found" }, 404));
  });

  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

test("renders the dashboard from the pre-cached heroes and the live show detail", async () => {
  const fetchMock = mockSuccessfulApi();
  render(<App />);

  // The curated default hero is selected without any /shows fetch.
  expect(await screen.findByRole("heading", { name: rx(defaultHero.artist_name!) })).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: /live music demand dashboard/i })).toBeInTheDocument();
  expect(screen.getByRole("combobox", { name: /demo show/i })).toHaveValue(DEFAULT_HERO_EVENT_ID);
  expect(screen.getByText(rx(`${defaultHero.artist_name} at ${defaultHero.venue_name}`))).toBeInTheDocument();

  // The chart and all three signal series come from the live /show/{id} detail.
  expect(await screen.findByText(/demand signals over time/i)).toBeInTheDocument();
  expect(screen.getByRole("checkbox", { name: /observed lowest price/i })).toBeChecked();
  expect(screen.getByRole("checkbox", { name: /forecast lowest price/i })).toBeChecked();
  expect(screen.getByRole("checkbox", { name: /local popularity/i })).toBeChecked();
  expect(screen.getByRole("checkbox", { name: /global popularity/i })).toBeChecked();

  // Only the selected show is fetched — never the full /shows list.
  expect(fetchMock).toHaveBeenCalledWith(
    `http://127.0.0.1:8000/show/${DEFAULT_HERO_EVENT_ID}`,
    expect.any(Object),
  );
  expect(fetchMock).not.toHaveBeenCalledWith("http://127.0.0.1:8000/shows", expect.any(Object));
});

test("selecting another hero fetches that show's history and forecast", async () => {
  const user = userEvent.setup();
  const fetchMock = mockSuccessfulApi();
  render(<App />);

  await screen.findByRole("heading", { name: rx(defaultHero.artist_name!) });
  await user.selectOptions(screen.getByRole("combobox", { name: /demo show/i }), secondHero.event_id);

  expect(await screen.findByRole("heading", { name: rx(secondHero.artist_name!) })).toBeInTheDocument();
  expect(fetchMock).toHaveBeenCalledWith(
    `http://127.0.0.1:8000/show/${secondHero.event_id}`,
    expect.any(Object),
  );
});

test("signal toggles can hide and show chart series", async () => {
  const user = userEvent.setup();
  mockSuccessfulApi();
  render(<App />);

  await screen.findByRole("heading", { name: rx(defaultHero.artist_name!) });
  const trendsToggle = await screen.findByRole("checkbox", { name: /local popularity/i });

  expect(trendsToggle).toBeChecked();
  await user.click(trendsToggle);
  expect(trendsToggle).not.toBeChecked();
  await user.click(trendsToggle);
  expect(trendsToggle).toBeChecked();
});

test("shows a clear error when the selected show cannot be loaded", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(() => Promise.resolve(jsonResponse({ detail: "Server error" }, 500))),
  );

  render(<App />);

  expect(await screen.findByRole("alert")).toHaveTextContent(/could not load the selected show/i);
  // The dropdown stays usable (heroes are pre-cached, not fetched).
  expect(screen.getByRole("combobox", { name: /demo show/i })).toBeEnabled();
});

test("missing signals are disabled instead of crashing the chart", () => {
  const missingSignalShow: ShowDetail = {
    event_id: "missing-signals",
    event_name: "Missing Signal Demo",
    artist_name: "Unknown Artist",
    venue_name: "Unknown Venue",
    city: "Oakland",
    state_code: "CA",
    show_date: "2026-09-01T00:00:00",
    status_code: "onsale",
    price_min: null,
    price_max: null,
    local_interest: null,
    yt_subscribers: null,
    yt_views: null,
    forecast_price: null,
    history: [
      {
        snapshot_date: "2026-06-25T00:00:00",
        days_to_show: 68,
        price_min: null,
        price_max: null,
        local_interest: null,
        yt_subscribers: null,
        yt_views: null,
      },
    ],
    forecast: [],
  };

  render(<DemandSignalsChart show={missingSignalShow} />);

  expect(screen.getByRole("checkbox", { name: /observed lowest price/i })).toBeDisabled();
  expect(screen.getByRole("checkbox", { name: /forecast lowest price/i })).toBeDisabled();
  expect(screen.getByRole("checkbox", { name: /local popularity/i })).toBeDisabled();
  expect(screen.getByRole("checkbox", { name: /global popularity/i })).toBeDisabled();
  expect(screen.getByText(/no selected signals are available/i)).toBeInTheDocument();
});
