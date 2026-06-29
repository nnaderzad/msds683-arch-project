import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import App from "./App";
import { DemandSignalsChart } from "./components/DemandSignalsChart";
import type { ShowDetail, ShowSummary } from "./types";

const soonSummary: ShowSummary = {
  event_id: "event_soon",
  event_name: "Soon Show",
  artist_name: "Artist One",
  venue_name: "Greek Theatre",
  city: "Berkeley",
  state_code: "CA",
  show_date: "2026-07-04T00:00:00",
  status_code: "onsale",
  price_min: 85,
  price_max: 170,
  local_interest: 65,
  yt_subscribers: 1100,
  yt_views: 21000,
  forecast_price: 120,
};

const laterSummary: ShowSummary = {
  event_id: "event_later",
  event_name: "Later Show",
  artist_name: "Artist Two",
  venue_name: "Bill Graham Civic Auditorium",
  city: "San Francisco",
  state_code: "CA",
  show_date: "2026-08-04T00:00:00",
  status_code: "onsale",
  price_min: null,
  price_max: null,
  local_interest: null,
  yt_subscribers: null,
  yt_views: null,
  forecast_price: 75,
};

const soonDetail: ShowDetail = {
  ...soonSummary,
  history: [
    {
      snapshot_date: "2026-06-24T00:00:00",
      days_to_show: 10,
      price_min: 80,
      price_max: 160,
      local_interest: 61,
      yt_subscribers: 1000,
      yt_views: 20000,
    },
    {
      snapshot_date: "2026-06-25T00:00:00",
      days_to_show: 9,
      price_min: 85,
      price_max: 170,
      local_interest: 65,
      yt_subscribers: 1100,
      yt_views: 21000,
    },
  ],
  forecast: [
    { days_to_show: 9, predicted_price: 90 },
    { days_to_show: 0, predicted_price: 120 },
  ],
};

const laterDetail: ShowDetail = {
  ...laterSummary,
  history: [
    {
      snapshot_date: "2026-06-25T00:00:00",
      days_to_show: 40,
      price_min: null,
      price_max: null,
      local_interest: null,
      yt_subscribers: null,
      yt_views: null,
    },
  ],
  forecast: [
    { days_to_show: 40, predicted_price: 50 },
    { days_to_show: 0, predicted_price: 75 },
  ],
};

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function mockSuccessfulApi() {
  const fetchMock = vi.fn((input: RequestInfo | URL) => {
    const url = String(input);

    if (url.endsWith("/shows")) {
      return Promise.resolve(jsonResponse([soonSummary, laterSummary]));
    }

    if (url.endsWith("/show/event_soon")) {
      return Promise.resolve(jsonResponse(soonDetail));
    }

    if (url.endsWith("/show/event_later")) {
      return Promise.resolve(jsonResponse(laterDetail));
    }

    return Promise.resolve(jsonResponse({ detail: "Not found" }, 404));
  });

  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

test("renders the dashboard from the live API contract", async () => {
  const fetchMock = mockSuccessfulApi();
  render(<App />);

  expect(screen.getAllByText(/loading live shows/i).length).toBeGreaterThan(0);
  expect(await screen.findByRole("heading", { name: /soon show/i })).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: /live music demand dashboard/i })).toBeInTheDocument();
  expect(screen.getByRole("combobox", { name: /demo show/i })).toHaveValue("event_soon");
  expect(screen.getByText(/artist one at greek theatre/i)).toBeInTheDocument();
  expect(await screen.findByText(/demand signals over time/i)).toBeInTheDocument();
  expect(screen.getByRole("checkbox", { name: /observed lowest price/i })).toBeChecked();
  expect(screen.getByRole("checkbox", { name: /forecast lowest price/i })).toBeChecked();
  expect(screen.getByRole("checkbox", { name: /google trends/i })).toBeChecked();
  expect(screen.getByRole("checkbox", { name: /youtube views/i })).toBeChecked();
  expect(screen.getByText(/right axis shows observed and forecasted lowest ticket price/i)).toBeInTheDocument();
  expect(screen.getAllByText(/forecasted lowest price/i).length).toBeGreaterThan(0);
  expect(fetchMock).toHaveBeenCalledWith("http://127.0.0.1:8000/shows", expect.any(Object));
  expect(fetchMock).toHaveBeenCalledWith("http://127.0.0.1:8000/show/event_soon", expect.any(Object));
});

test("selecting another show fetches that show's history and forecast", async () => {
  const user = userEvent.setup();
  const fetchMock = mockSuccessfulApi();
  render(<App />);

  await screen.findByRole("heading", { name: /soon show/i });
  await user.selectOptions(screen.getByRole("combobox", { name: /demo show/i }), "event_later");

  expect(await screen.findByRole("heading", { name: /later show/i })).toBeInTheDocument();
  expect(screen.getAllByText(/bill graham civic auditorium/i).length).toBeGreaterThan(0);
  expect(screen.getAllByText(/\$75/).length).toBeGreaterThan(0);
  expect(fetchMock).toHaveBeenCalledWith("http://127.0.0.1:8000/show/event_later", expect.any(Object));
});

test("signal toggles can hide and show chart series", async () => {
  const user = userEvent.setup();
  mockSuccessfulApi();
  render(<App />);

  await screen.findByRole("heading", { name: /soon show/i });
  const trendsToggle = await screen.findByRole("checkbox", { name: /google trends/i });

  expect(trendsToggle).toBeChecked();
  await user.click(trendsToggle);
  expect(trendsToggle).not.toBeChecked();
  await user.click(trendsToggle);
  expect(trendsToggle).toBeChecked();
});

test("shows a clear error when the live API is unavailable", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(() => Promise.resolve(jsonResponse({ detail: "Server error" }, 500))),
  );

  render(<App />);

  expect(await screen.findByRole("alert")).toHaveTextContent(/could not load the live show list/i);
  expect(screen.getByRole("combobox", { name: /demo show/i })).toBeDisabled();
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
  expect(screen.getByRole("checkbox", { name: /google trends/i })).toBeDisabled();
  expect(screen.getByRole("checkbox", { name: /youtube views/i })).toBeDisabled();
  expect(screen.getByText(/no selected signals are available/i)).toBeInTheDocument();
});
