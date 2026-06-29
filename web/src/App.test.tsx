import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import App from "./App";
import { DemandSignalsChart } from "./components/DemandSignalsChart";
import type { ShowDetail } from "./types";

test("renders the dashboard with the default mock show", () => {
  render(<App />);

  expect(screen.getByRole("heading", { name: /live music demand dashboard/i })).toBeInTheDocument();
  expect(screen.getByRole("combobox", { name: /demo show/i })).toBeInTheDocument();
  expect(screen.getByText(/turnover, narrow head, she's green/i)).toBeInTheDocument();
  expect(screen.getByText(/demand signals over time/i)).toBeInTheDocument();
  expect(screen.getByRole("checkbox", { name: /observed price/i })).toBeChecked();
  expect(screen.getByRole("checkbox", { name: /forecast price/i })).toBeChecked();
  expect(screen.getByRole("checkbox", { name: /google trends/i })).toBeChecked();
  expect(screen.getByRole("checkbox", { name: /youtube views/i })).toBeChecked();
  expect(screen.getByText(/show date/i)).toBeInTheDocument();
  expect(screen.getByText(/right axis shows observed and forecasted price/i)).toBeInTheDocument();
  expect(screen.getByText(/auto-scales per selected show/i)).toBeInTheDocument();
  expect(screen.getAllByText(/forecasted price/i).length).toBeGreaterThan(0);
});

test("selecting another show updates the selected show details", async () => {
  const user = userEvent.setup();
  render(<App />);

  await user.selectOptions(screen.getByRole("combobox", { name: /demo show/i }), "rZ7HnEZ1AfPJGN");

  expect(screen.getByRole("heading", { name: /bingo loco/i })).toBeInTheDocument();
  expect(screen.getAllByText(/san jose improv/i).length).toBeGreaterThan(0);
  expect(screen.getAllByText(/\$73/).length).toBeGreaterThan(0);
});

test("signal toggles can hide and show chart series", async () => {
  const user = userEvent.setup();
  render(<App />);

  const trendsToggle = screen.getByRole("checkbox", { name: /google trends/i });

  expect(trendsToggle).toBeChecked();
  await user.click(trendsToggle);
  expect(trendsToggle).not.toBeChecked();
  await user.click(trendsToggle);
  expect(trendsToggle).toBeChecked();
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

  expect(screen.getByRole("checkbox", { name: /observed price/i })).toBeDisabled();
  expect(screen.getByRole("checkbox", { name: /forecast price/i })).toBeDisabled();
  expect(screen.getByRole("checkbox", { name: /google trends/i })).toBeDisabled();
  expect(screen.getByRole("checkbox", { name: /youtube views/i })).toBeDisabled();
  expect(screen.getByText(/no selected signals are available/i)).toBeInTheDocument();
});
