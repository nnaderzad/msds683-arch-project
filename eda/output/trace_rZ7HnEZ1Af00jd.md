# Event trace — `rZ7HnEZ1Af00jd` (bronze → forecast)

Generated 2026-07-08T08:11:28+00:00 by `eda/data_review.py --trace rZ7HnEZ1Af00jd`.

## 0. Raw (bronze) — the event as Ticketmaster sends it

`gs://data-architecture-498123-raw/ticketmaster/dt=2026-07-08/ticketmaster_CA_20260708T010002Z.json` (`images`/`_links` omitted)

```json
{
  "name": "Everclear with American Hi-Fi",
  "type": "event",
  "id": "rZ7HnEZ1Af00jd",
  "test": false,
  "url": "https://www.ticketweb.com/event/everclear-with-american-hi-fi-the-independent-tickets/14924703",
  "locale": "en-us",
  "sales": {
    "public": {
      "startDateTime": "2026-05-19T17:00:00Z",
      "startTBD": false,
      "startTBA": false,
      "endDateTime": "2026-10-25T04:00:00Z"
    }
  },
  "dates": {
    "access": {
      "startDateTime": "2026-10-25T03:30:00Z",
      "startApproximate": false,
      "endApproximate": false
    },
    "start": {
      "localDate": "2026-10-24",
      "localTime": "21:00:00",
      "dateTime": "2026-10-25T04:00:00Z",
      "dateTBD": false,
      "dateTBA": false,
      "timeTBA": false,
      "noSpecificTime": false
    },
    "end": {
      "approximate": false,
      "noSpecificTime": false
    },
    "timezone": "America/Los_Angeles",
    "status": {
      "code": "onsale"
    },
    "spanMultipleDays": false
  },
  "classifications": [
    {
      "primary": true,
      "segment": {
        "id": "KZFzniwnSyZfZ7v7nJ",
        "name": "Music"
      },
      "genre": {
        "id": "KnvZfZ7vAvl",
        "name": "Other"
      },
      "type": {
        "id": "KZAyXgnZfZ7v7nI",
        "name": "Undefined"
      },
      "subType": {
        "id": "KZFzBErXgnZfZ7v7lJ",
        "name": "Undefined"
      },
      "family": false
    },
    {
      "primary": false,
      "segment": {
        "id": "KZFzniwnSyZfZ7v7nJ",
        "name": "Music"
      },
      "genre": {
        "id": "KnvZfZ7vAvv",
        "name": "Alternative"
      },
      "subGenre": {
        "id": "KZazBEonSMnZfZ7vAvn",
        "name": "Alternative Rock"
      },
      "type": {
        "id": "KZAyXgnZfZ7v7nI",
        "name": "Undefined"
      },
      "subType": {
        "id": "KZFzBErXgnZfZ7v7lJ",
        "name": "Undefined"
      },
      "family": false
    }
  ],
  "info": "Please note - there is a delivery delay set for 2 weeks prior to show.Spotify Presale: 5/19 at 10amBandsintown Presale: …",
  "pleaseNote": "This event is 21 and over. Any ticket holder unable to present valid identification indicating that they are at least 21…",
  "priceRanges": [
    {
      "type": "standard",
      "currency": "USD",
      "min": 136.05,
      "max": 236.05
    }
  ],
  "nameOrigin": "custom",
  "_embedded": {
    "venues": [
      {
        "name": "The Independent",
        "type": "venue",
        "id": "rZ7HnEZadXm",
        "test": false,
        "url": "https://www.ticketweb.com/venue/the-independent-san-francisco-ca/16302",
        "locale": "en-us",
        "postalCode": "94117",
        "timezone": "America/Los_Angeles",
        "city": {
          "name": "San Francisco"
        },
        "state": {
          "name": "California",
          "stateCode": "CA"
        },
        "country": {
          "name": "United States Of America",
          "countryCode": "US"
        },
        "address": {
          "line1": "628 Divisadero St"
        },
        "location": {
          "longitude": "-122.4378",
          "latitude": "37.7755"
        },
        "upcomingEvents": {
          "ticketweb": 83,
          "_total": 83,
          "_filtered": 0
        }
      }
    ],
    "attractions": [
      {
        "name": "Everclear",
        "type": "attraction",
        "id": "K8vZ9171480",
        "test": false,
        "url": "https://www.ticketmaster.com/everclear-tickets/artist/776809",
        "locale": "en-us",
        "externalLinks": {
          "youtube": "[… 1 items]",
          "twitter": "[… 1 items]",
          "itunes": "[… 1 items]",
          "lastfm": "[… 1 items]",
          "spotify": "[… 1 items]",
          "wiki": "[… 1 items]",
          "facebook": "[… 1 items]",
          "musicbrainz": "[… 1 items]",
          "homepage": "[… 1 items]"
        },
        "classifications": [
          "{… 7 keys}"
        ],
        "upcomingEvents": {
          "tmr": 12,
          "ticketmaster": 17,
          "ticketweb": 1,
          "_total": 30,
          "_filtered": 0
        }
      },
      {
        "name": "American Hi-Fi",
        "type": "attraction",
        "id": "K8vZ9171Sv0",
        "test": false,
        "url": "https://www.ticketmaster.com/american-hifi-tickets/artist/807208",
        "locale": "en-us",
        "externalLinks": {
          "twitter": "[… 1 items]"
        },
        "classifications": [
          "{… 7 keys}"
        ],
        "upcomingEvents": {
          "ticketmaster": 13,
          "ticketweb": 1,
          "_total": 14,
          "_filtered": 0
        }
      }
    ]
  }
}
```

## 1. Silver `tm_events` — current-state row (MERGE upsert)

| event_id | event_name | local_date | status_code | price_min | price_max | venue_name | attraction_names | genre | first_seen |
|---|---|---|---|---|---|---|---|---|---|
| rZ7HnEZ1Af00jd | Everclear with American Hi-Fi | 2026-10-24 | onsale | 136.05 | 236.05 | The Independent | Everclear|American Hi-Fi | Other | 2026-06-11 21:56:57+00 |

## 2. Silver `tm_observations` — honest per-day history (no forward-fill)

| snapshot_date | price_min | price_max | status_code | n_captures | price_disagreed |
|---|---|---|---|---|---|
| 2026-06-11 | 136.05 | 236.05 | onsale | 3 | false |
| 2026-06-12 | 136.05 | 236.05 | onsale | 6 | false |
| 2026-06-13 | 136.05 | 236.05 | onsale | 6 | false |
| … 19 more days … |  |  |  |  |  |
| 2026-07-06 | 136.05 | 236.05 | onsale | 2 | false |
| 2026-07-07 | 136.05 | 236.05 | onsale | 2 | false |
| 2026-07-08 | 136.05 | 236.05 | onsale | 1 | false |

## 3. Headliner resolution (`bridge_event_artist` → `dim_artist`)

| artist_id | artist_name | is_headliner |
|---|---|---|
| 8650601693132637689 | Everclear | true |
| 4205536515222722402 | American Hi-Fi | false |

## 4. Demand signals for the headliner (Bay Area DMA 807)

`fact_trends_daily` (artist_id=8650601693132637689, last 5 days):

| snapshot_date | interest | is_partial |
|---|---|---|
| 2026-07-05 | 0 | true |
| 2026-07-04 | 13 | false |
| 2026-07-03 | 12 | false |
| 2026-07-02 | 13 | false |
| 2026-07-01 | 10 | false |

`fact_youtube` (last 3 snapshots):

| snapshot_date | official_subscribers | official_total_views |
|---|---|---|
| 2026-07-07 | 113000 | 208724368 |
| 2026-07-06 | 113000 | 208724368 |
| 2026-07-05 | 113000 | 208724368 |

## 5. Gold `fact_event_demand` — one row per (event, snapshot day)

| snapshot_date | days_to_show | price_min | price_max | local_interest | yt_subscribers |
|---|---|---|---|---|---|
| 2026-07-07 | 109 | 136.05 | 236.05 |  | 113000 |
| 2026-07-06 | 110 | 136.05 | 236.05 |  | 113000 |
| 2026-07-05 | 111 | 136.05 | 236.05 |  | 113000 |
| 2026-07-01 | 115 | 136.05 | 236.05 |  |  |
| 2026-06-30 | 116 | 136.05 | 236.05 |  | 113000 |

## 6. Gold `forecast_event_price` — precomputed anchor+drift curve

| days_to_show | predicted_price |
|---|---|
| 0 | 112.25782815186177 |
| 30 | 120.98792999260246 |
| 60 | 125.33429221963927 |
| 90 | 128.9452200069079 |

Full transformation SQL per stage: `docs/transformations_showcase.md`.
