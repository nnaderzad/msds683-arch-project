import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ShowSummary } from "../types";
import { SearchPanel } from "./SearchPanel";

const edmShow: ShowSummary = {
  event_id: "edm1",
  event_name: "Warehouse Rave",
  artist_name: "DJ Fixture",
  venue_name: "Public Works",
  city: "San Francisco",
  state_code: "CA",
  show_date: "2026-08-15",
  status_code: "onsale",
  price_min: 30,
  price_max: 60,
  local_interest: 80,
  yt_subscribers: 5000,
  yt_views: null,
  forecast_price: 45,
};

function mockFetch(results: ShowSummary[]) {
  const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL) => {
    const url = String(input);
    const payload = url.includes("/genres") ? ["Dance/Electronic", "Rock"] : results;
    return Promise.resolve({ ok: true, json: () => Promise.resolve(payload) });
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("SearchPanel", () => {
  it("loads genres, searches with compound filters, and picks a result", async () => {
    const fetchMock = mockFetch([edmShow]);
    const onPick = vi.fn();
    render(<SearchPanel onPick={onPick} />);

    await waitFor(() => {
      expect(screen.getByRole("option", { name: "Dance/Electronic" })).toBeInTheDocument();
    });

    await userEvent.selectOptions(screen.getByLabelText("Genre"), "Dance/Electronic");
    await userEvent.click(screen.getByLabelText(/Bay Area only/i));
    await userEvent.type(screen.getByLabelText("Max projected price"), "50");
    await userEvent.selectOptions(screen.getByLabelText("Time horizon"), "14");
    await userEvent.click(screen.getByRole("button", { name: "Search" }));

    await waitFor(() => {
      expect(screen.getByText("Warehouse Rave")).toBeInTheDocument();
    });

    const searchCall = fetchMock.mock.calls
      .map((call) => String(call[0]))
      .find((url) => url.includes("/search"));
    expect(searchCall).toContain("genre=Dance%2FElectronic");
    expect(searchCall).toContain("dma=807");
    expect(searchCall).toContain("max_price=50");
    expect(searchCall).toContain("days_ahead=14");

    await userEvent.click(screen.getByRole("button", { name: "View Warehouse Rave" }));
    expect(onPick).toHaveBeenCalledWith(edmShow);
  });

  it("renders result prices as $X.XX and picks a row from the keyboard", async () => {
    mockFetch([edmShow]);
    const onPick = vi.fn();
    render(<SearchPanel onPick={onPick} />);
    await userEvent.click(screen.getByRole("button", { name: "Search" }));

    await waitFor(() => {
      expect(screen.getByText("$30.00")).toBeInTheDocument();
    });
    expect(screen.getByText("$45.00")).toBeInTheDocument();

    const row = screen.getByRole("button", { name: "View Warehouse Rave" });
    row.focus();
    await userEvent.keyboard("{Enter}");
    expect(onPick).toHaveBeenCalledWith(edmShow);
  });

  it("shows an empty-state message when nothing matches", async () => {
    mockFetch([]);
    render(<SearchPanel onPick={vi.fn()} />);
    await userEvent.click(screen.getByRole("button", { name: "Search" }));

    await waitFor(() => {
      expect(screen.getByText(/No shows match/)).toBeInTheDocument();
    });
  });
});
