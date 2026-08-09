import type { ShowDetail } from "../types";
import {
  buildDemandChartData,
  getSignalAvailability,
  hasFilledPriceHistory,
  observedLowestPrice,
  priceAxisDomain,
} from "./chartData";

const baseShow: ShowDetail = {
  event_id: "test-event",
  event_name: "Test Event",
  artist_name: "Test Artist",
  venue_name: "Test Venue",
  city: "San Francisco",
  state_code: "CA",
  show_date: "2026-07-10T00:00:00",
  status_code: "onsale",
  price_min: 20,
  price_max: 60,
  local_interest: 50,
  yt_subscribers: 1000,
  yt_views: 10000,
  forecast_price: 55,
  history: [
    {
      snapshot_date: "2026-07-01T00:00:00",
      days_to_show: 9,
      price_min: 20,
      price_max: 60,
      local_interest: 40,
      yt_subscribers: 1000,
      yt_views: 10000,
    },
    {
      snapshot_date: "2026-07-02T00:00:00",
      days_to_show: 8,
      price_min: 30,
      price_max: 70,
      local_interest: 50,
      yt_subscribers: 1100,
      yt_views: 12000,
    },
  ],
  forecast: [
    { days_to_show: 8, predicted_price: 50 },
    { days_to_show: 4, predicted_price: 52 },
    { days_to_show: 0, predicted_price: 55 },
  ],
};

test("uses price_min as the observed lowest-price series", () => {
  expect(observedLowestPrice(40)).toBe(40);
  expect(observedLowestPrice(null)).toBeNull();
});

test("builds chart rows with observed prices, future forecast points, and indexed YouTube values", () => {
  const rows = buildDemandChartData(baseShow);

  expect(rows).toHaveLength(4);
  expect(rows[0]).toMatchObject({
    label: "Jul 1",
    observedPriceRaw: 20,
    trendsRaw: 40,
  });
  expect(rows[1]).toMatchObject({
    label: "Jul 2",
    observedPriceRaw: 30,
    forecastPriceRaw: 30,
  });
  expect(rows[2]).toMatchObject({
    label: "Jul 6",
    observedPriceRaw: null,
    forecastPriceRaw: 52,
  });
  expect(rows[3]).toMatchObject({
    label: "Jul 10",
    forecastPriceRaw: 55,
  });
  expect(rows[0].youtube).toBe(15);
  expect(rows[1].youtube).toBe(85);
});

test("merges history_filled into the rows only when fillPrices is on", () => {
  // Observed on Jul 1, gap on Jul 2 (price null), carried forward from Jul 1.
  const gappyShow: ShowDetail = {
    ...baseShow,
    history: [
      baseShow.history[0],
      { ...baseShow.history[1], price_min: null, price_max: null },
    ],
    history_filled: [
      {
        snapshot_date: "2026-07-01T00:00:00",
        days_to_show: 9,
        price_min: 20,
        price_max: 60,
        price_is_filled: false,
      },
      {
        snapshot_date: "2026-07-02T00:00:00",
        days_to_show: 8,
        price_min: 20,
        price_max: 60,
        price_is_filled: true,
      },
    ],
  };

  const filled = buildDemandChartData(gappyShow, { fillPrices: true });
  expect(filled[0]).toMatchObject({ priceFilledRaw: 20, priceIsFilled: false });
  expect(filled[1]).toMatchObject({
    observedPriceRaw: null,
    priceFilledRaw: 20,
    priceIsFilled: true,
  });

  // Off (or omitted) is exactly the observed-only view.
  const observedOnly = buildDemandChartData(gappyShow);
  expect(observedOnly.every((row) => row.priceFilledRaw === null)).toBe(true);
  expect(observedOnly.every((row) => !row.priceIsFilled)).toBe(true);
});

test("drops history_filled snapshots taken after the show date", () => {
  const pastShow: ShowDetail = {
    ...baseShow,
    show_date: "2026-07-02T00:00:00",
    history_filled: [
      {
        snapshot_date: "2026-07-05T00:00:00",
        days_to_show: -3,
        price_min: 99,
        price_max: 120,
        price_is_filled: true,
      },
    ],
    forecast: [],
  };
  const rows = buildDemandChartData(pastShow, { fillPrices: true });

  expect(rows.every((row) => row.date <= "2026-07-02")).toBe(true);
  expect(rows.some((row) => row.priceFilledRaw === 99)).toBe(false);
});

test("reports whether a show has a filled price history", () => {
  expect(hasFilledPriceHistory(baseShow)).toBe(false);
  expect(hasFilledPriceHistory({ ...baseShow, history_filled: [] })).toBe(false);
  expect(
    hasFilledPriceHistory({
      ...baseShow,
      history_filled: [
        {
          snapshot_date: "2026-07-01T00:00:00",
          days_to_show: 9,
          price_min: 20,
          price_max: 60,
          price_is_filled: false,
        },
      ],
    }),
  ).toBe(true);
});

test("drops snapshots taken after the show date for popularity and observed price", () => {
  const pastShow: ShowDetail = {
    ...baseShow,
    show_date: "2026-07-02T00:00:00",
    history: [
      ...baseShow.history,
      {
        snapshot_date: "2026-07-05T00:00:00",
        days_to_show: -3,
        price_min: 99,
        price_max: 120,
        local_interest: 70,
        yt_subscribers: 1300,
        yt_views: 15000,
      },
    ],
    forecast: [],
  };
  const rows = buildDemandChartData(pastShow);

  expect(rows.every((row) => row.date <= "2026-07-02")).toBe(true);
  expect(rows.some((row) => row.observedPriceRaw === 99)).toBe(false);
  expect(rows.some((row) => row.trendsRaw === 70)).toBe(false);
});

test("reports unavailable signals when source values are all null", () => {
  const missingSignalShow: ShowDetail = {
    ...baseShow,
    price_min: null,
    price_max: null,
    local_interest: null,
    yt_subscribers: null,
    yt_views: null,
    forecast_price: null,
    history: [
      {
        snapshot_date: "2026-07-01T00:00:00",
        days_to_show: 9,
        price_min: null,
        price_max: null,
        local_interest: null,
        yt_subscribers: null,
        yt_views: null,
      },
    ],
    forecast: [],
  };
  const rows = buildDemandChartData(missingSignalShow);

  expect(getSignalAvailability(rows)).toEqual({
    price: false,
    forecast: false,
    trends: false,
    youtube: false,
  });
  expect(priceAxisDomain(rows)).toEqual([0, 100]);
});
