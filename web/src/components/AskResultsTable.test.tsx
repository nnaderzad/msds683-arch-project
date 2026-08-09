import { act, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ShowDetail } from "../types";
import { AskResultsTable, findEventIdColumn, linkableEventId } from "./AskResultsTable";

const EVENT_ID = "rZ7HnEZ1Af00jd";

const showDetail: ShowDetail = {
  event_id: EVENT_ID,
  event_name: "Everclear with American Hi-Fi",
  artist_name: "Everclear",
  venue_name: "The Independent",
  city: "San Francisco",
  state_code: "CA",
  show_date: "2026-10-24",
  status_code: "",
  price_min: 136.05,
  price_max: 236.05,
  local_interest: 55,
  yt_subscribers: 113000,
  yt_views: null,
  forecast_price: 102.292026,
  history: [],
  forecast: [],
};

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("AskResultsTable", () => {
  it("detects event id columns, preferring an exact match", () => {
    expect(findEventIdColumn(["event_id", "event_name"])).toBe("event_id");
    expect(findEventIdColumn(["EVENT_ID"])).toBe("EVENT_ID");
    expect(findEventIdColumn(["tm_event_id", "price"])).toBe("tm_event_id");
    expect(findEventIdColumn(["tm_event_id", "event_id"])).toBe("event_id");
    expect(findEventIdColumn(["event_name", "id", "event_identifier"])).toBeNull();
    expect(findEventIdColumn([])).toBeNull();
  });

  it("only treats plausible id values as linkable", () => {
    expect(linkableEventId({ event_id: EVENT_ID }, "event_id")).toBe(EVENT_ID);
    expect(linkableEventId({ event_id: "short" }, "event_id")).toBeNull();
    expect(linkableEventId({ event_id: "has some spaces" }, "event_id")).toBeNull();
    expect(linkableEventId({ event_id: "" }, "event_id")).toBeNull();
    expect(linkableEventId({ event_id: 12345678 }, "event_id")).toBeNull();
    expect(linkableEventId({ event_id: null }, "event_id")).toBeNull();
    expect(linkableEventId({ event_id: EVENT_ID }, null)).toBeNull();
  });

  it("clicking or keying a linkable row calls the pick handler with the id", async () => {
    const onOpenShow = vi.fn();
    render(
      <AskResultsTable
        rows={[
          { event_id: EVENT_ID, event_name: "Everclear with American Hi-Fi" },
          { event_id: null, event_name: "No id here" },
        ]}
        onOpenShow={onOpenShow}
      />,
    );

    // Only the row with a plausible id is a button; the other stays plain.
    expect(screen.getAllByRole("button")).toHaveLength(1);
    expect(screen.getByText("view →")).toBeInTheDocument();

    const row = screen.getByRole("button", { name: `View show ${EVENT_ID}` });
    await userEvent.click(row);
    expect(onOpenShow).toHaveBeenCalledWith(EVENT_ID);

    fireEvent.keyDown(row, { key: "Enter" });
    expect(onOpenShow).toHaveBeenCalledTimes(2);
  });

  it("renders rows without an event id column exactly as before", () => {
    render(<AskResultsTable rows={[{ cheapest_price: 136.05 }]} onOpenShow={vi.fn()} />);

    expect(screen.getByRole("cell", { name: "136.05" })).toBeInTheDocument();
    expect(screen.getAllByRole("columnheader")).toHaveLength(1);
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
    expect(screen.queryByText("view →")).not.toBeInTheDocument();
  });

  it("shows the stats card after the 300 ms debounce and caches per event_id", async () => {
    vi.useFakeTimers();
    try {
      const fetchMock = vi
        .fn()
        .mockImplementation(() =>
          Promise.resolve({ ok: true, json: () => Promise.resolve(showDetail) }),
        );
      vi.stubGlobal("fetch", fetchMock);

      render(
        <AskResultsTable
          rows={[{ event_id: EVENT_ID, event_name: "Everclear with American Hi-Fi" }]}
          onOpenShow={vi.fn()}
        />,
      );
      const row = screen.getByRole("button", { name: `View show ${EVENT_ID}` });

      // Leaving before the debounce fires never fetches.
      fireEvent.focus(row);
      fireEvent.blur(row);
      await act(async () => {
        await vi.advanceTimersByTimeAsync(400);
      });
      expect(fetchMock).not.toHaveBeenCalled();

      fireEvent.focus(row);
      await act(async () => {
        await vi.advanceTimersByTimeAsync(300);
      });

      const card = screen.getByRole("status");
      expect(card).toHaveTextContent("Everclear — Everclear with American Hi-Fi");
      expect(card).toHaveTextContent("The Independent · San Francisco, CA");
      expect(card).toHaveTextContent("$136.05–$236.05");
      expect(card).toHaveTextContent("$102.29");
      expect(card).toHaveTextContent("113K");
      expect(fetchMock).toHaveBeenCalledWith(
        `http://127.0.0.1:8000/show/${EVENT_ID}`,
        expect.any(Object),
      );

      // Dismisses on blur; refocusing reuses the cache instead of refetching.
      fireEvent.blur(row);
      expect(screen.queryByRole("status")).not.toBeInTheDocument();
      fireEvent.focus(row);
      await act(async () => {
        await vi.advanceTimersByTimeAsync(300);
      });
      expect(screen.getByRole("status")).toBeInTheDocument();
      expect(fetchMock).toHaveBeenCalledTimes(1);
    } finally {
      vi.useRealTimers();
    }
  });

  it("renders no card at all when the show fetch 404s", async () => {
    vi.useFakeTimers();
    try {
      const fetchMock = vi.fn().mockImplementation(() =>
        Promise.resolve({
          ok: false,
          status: 404,
          statusText: "Not Found",
          json: () => Promise.resolve({ detail: "not found" }),
        }),
      );
      vi.stubGlobal("fetch", fetchMock);

      render(<AskResultsTable rows={[{ event_id: EVENT_ID }]} onOpenShow={vi.fn()} />);
      const row = screen.getByRole("button", { name: `View show ${EVENT_ID}` });

      fireEvent.focus(row);
      await act(async () => {
        await vi.advanceTimersByTimeAsync(300);
      });
      expect(screen.queryByRole("status")).not.toBeInTheDocument();

      // The failure is cached: hovering again does not retry.
      fireEvent.blur(row);
      fireEvent.focus(row);
      await act(async () => {
        await vi.advanceTimersByTimeAsync(300);
      });
      expect(fetchMock).toHaveBeenCalledTimes(1);
    } finally {
      vi.useRealTimers();
    }
  });
});
