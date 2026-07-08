# Data review — bronze → gold, all sources

Generated 2026-07-08T08:08:00+00:00 by `eda/data_review.py` (project `data-architecture-498123`, dataset `event_demand_analytics`). Re-run the same command to refresh; narrative companion: `eda/data_review_2026-07.md`.

## Bronze inventory (`gs://<project>-raw/<source>/dt=<UTC-day>/`)

| source | partitions | first | last | files_latest | total_MiB |
|---|---|---|---|---|---|
| ticketmaster | 26 | 2026-06-08 | 2026-07-08 | 51 | 46,247.6 |
| google_trends | 21 | 2026-06-14 | 2026-07-07 | 392 | 324.9 |
| youtube | 21 | 2026-06-14 | 2026-07-07 | 1 | 4.0 |
| nineteenhz | 1 | 2026-07-08 | 2026-07-08 | 1 | 0.2 |
| ra | 1 | 2026-07-08 | 2026-07-08 | 1 | 0.1 |
| ticketpages | 1 | 2026-07-08 | 2026-07-08 | 1 | 0.1 |

Google Trends calls/day = files/day (one file per API call; the same
ledger as `google_trends_api/check_call_rate.py`). Last 7 partitions:

| dt (UTC) | calls |
|---|---|
| 2026-06-28 | 145 |
| 2026-06-29 | 28 |
| 2026-06-30 | 89 |
| 2026-07-04 | 156 |
| 2026-07-05 | 460 |
| 2026-07-06 | 434 |
| 2026-07-07 | 392 |

## Raw-format samples (newest capture per source family)

### Ticketmaster — one Discovery event (of a per-state JSON array; `images`/`_links` omitted for brevity)

`gs://data-architecture-498123-raw/ticketmaster/dt=2026-07-08/ticketmaster_CA_20260708T010002Z.json`

```json
{
  "name": "Elevation Rhythm - The Goodbye Yesterday Tour",
  "type": "event",
  "id": "G5vYZ_k3MJLRf",
  "test": false,
  "url": "https://www.ticketmaster.com/elevation-rhythm-the-goodbye-yesterday-tour-sacramento-california-07-07-2026/event/1C006464…",
  "locale": "en-us",
  "sales": {
    "public": {
      "startDateTime": "2026-03-06T18:00:00Z",
      "startTBD": false,
      "startTBA": false,
      "endDateTime": "2026-07-08T03:30:00Z"
    },
    "presales": [
      {
        "startDateTime": "2026-03-05T18:00:00Z",
        "endDateTime": "2026-07-01T05:00:00Z",
        "name": "VIP Packages"
      },
      {
        "startDateTime": "2026-03-05T18:00:00Z",
        "endDateTime": "2026-03-06T18:00:00Z",
        "name": "TICKET HOLDER PRE-SALE"
      }
    ]
  },
  "dates": {
    "start": {
      "localDate": "2026-07-07",
      "localTime": "18:30:00",
      "dateTime": "2026-07-08T01:30:00Z",
      "dateTBD": false,
      "dateTBA": false,
      "timeTBA": false,
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
        "id": "KnvZfZ7vAe7",
        "name": "Religious"
      },
      "subGenre": {
        "id": "KZazBEonSMnZfZ7v6ea",
        "name": "Gospel"
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
  "promoter": {
    "id": "653",
    "name": "LIVE NATION MUSIC",
    "description": "LIVE NATION MUSIC / NTL / USA"
  },
  "promoters": [
    {
      "id": "653",
      "name": "LIVE NATION MUSIC",
      "description": "LIVE NATION MUSIC / NTL / USA"
    }
  ],
  "info": "DOORS:6:30pm SHOW:7:30pm This is an all ages show - everyone is welcome. NO RE-ENTRY All schedules and support bands are…",
  "products": [
    {
      "name": "Fast Lane Access - Elevation Rhythm - Not a Concert Ticket",
      "id": "G5vYZ_kKIRaEc",
      "url": "https://www.ticketmaster.com/fast-lane-access-elevation-rhythm-not-sacramento-california-07-07-2026/event/1C006464E8BC99…",
      "type": "Special Entry",
      "classifications": [
        {
          "primary": true,
          "segment": "{… 2 keys}",
          "genre": "{… 2 keys}",
          "subGenre": "{… 2 keys}",
          "type": "{… 2 keys}",
          "subType": "{… 2 keys}",
          "family": false
        }
      ]
    },
    {
      "name": "Good Luck Lounge Access- Elevation Rhythm - Not a Concert Ticket",
      "id": "G5vYZ_kKtJacX",
      "url": "https://www.ticketmaster.com/good-luck-lounge-access-elevation-rhythm-sacramento-california-07-07-2026/event/1C006464E99…",
      "type": "Club Access",
      "classifications": [
        {
          "primary": true,
          "segment": "{… 2 keys}",
          "genre": "{… 2 keys}",
          "subGenre": "{… 2 keys}",
          "type": "{… 2 keys}",
          "subType": "{… 2 keys}",
          "family": false
        }
      ]
    },
    "... 3 more items"
  ],
  "seatmap": {
    "staticUrl": "https://mapsapi.tmol.io/maps/geometry/3/event/1C006464B799FBD4/staticImage?type=png&systemId=HOST"
  },
  "accessibility": {
    "info": "There is a special seating area for those with mobility limitations. We are able to accommodate up to two people per par…"
  },
  "ticketLimit": {
    "info": "Please note, there is an 8 ticket limit on this event."
  },
  "ageRestrictions": {
    "legalAgeEnforced": false
  },
  "ticketing": {
    "safeTix": {
      "enabled": true
    },
    "allInclusivePricing": {
      "enabled": true
    }
  },
  "nameOrigin": "custom",
  "ticketTextLines": {
    "en-us": {
      "line6": "TUE JUL 07 2026 DRS 630PM ",
      "line5": "1417 R STREET * SACRAMENTO",
      "line4": "      ACE OF SPADES       ",
      "line3": " The GoodbyeYesterdayTour ",
      "line2": "     Elevation Rhythm     ",
      "line1": "      TPR. Presents       "
    }
  },
  "_embedded": {
    "venues": [
      {
        "name": "Ace of Spades",
        "type": "venue",
        "id": "KovZpZAJ6lvA",
        "test": false,
        "url": "https://www.ticketmaster.com/ace-of-spades-tickets-sacramento/venue/229953",
        "locale": "en-us",
        "postalCode": "95811",
        "timezone": "America/Los_Angeles",
        "city": {
          "name": "Sacramento"
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
          "line1": "1417 R St."
        },
        "location": {
          "longitude": "-121.49087928",
          "latitude": "38.56999300"
        },
        "markets": [
          "{… 2 keys}"
        ],
        "dmas": [
          "{… 1 keys}",
          "{… 1 keys}",
          "... 5 more items"
        ],
        "boxOfficeInfo": {
          "openHoursDetail": "Ace of Spades Box office is open on Show nights 2 hours prior to doors and 5-9 Thursday - Saturday.",
          "acceptedPaymentDetail": "Ace of Spades is Cashless we accept all Major Credit Cards.",
          "willCallDetail": "Will call tickets can only be picked up at the venue Box Office on the night of the event. The original purchaser must b…"
        },
        "parkingDetail": "Nearby street parking is available. Additonal Parking is available for $5/space at the SEIU parking lot on R Street betw…",
        "accessibleSeatingDetail": "Yes! Ace of Spades strives to make our venue and live experiences inclusive and accessible. For more questions, or infor…",
        "generalInfo": {
          "generalRule": "All events are all ages unless otherwise noted. All attendees are required to purchase a full price ticket regardless of…",
          "childRule": "Children ages 3 and under do not need a ticket. Some events may have age restrictions in place so please check our websi…"
        },
        "upcomingEvents": {
          "ticketmaster": 59,
          "_total": 59,
          "_filtered": 0
        },
        "ada": {
          "adaPhones": "Please visit https://www.aceofspadessac.com/accessibility for accessibility information and contact options.",
          "adaCustomCopy": "For information about accessible seating, accommodations, and venue access, please visit the Ace of Spades accessibility…",
          "adaHours": "The Ace of Spades Box Office opens 1 hour before doors on show days only."
        }
      }
    ],
    "attractions": [
      {
        "name": "Elevation Rhythm",
        "type": "attraction",
        "id": "K8vZ917hd50",
        "test": false,
        "url": "https://www.ticketmaster.com/elevation-rhythm-tickets/artist/2967739",
        "locale": "en-us",
        "externalLinks": {
          "musicbrainz": "[… 1 items]"
        },
        "classifications": [
          "{… 7 keys}"
        ],
        "upcomingEvents": {
          "ticketmaster": 18,
          "ticketweb": 2,
          "_total": 20,
          "_filtered": 0
        }
      }
    ]
  }
}
```

### Google Trends — iot national (daily series, geo=US)

`gs://data-architecture-498123-raw/google_trends/dt=2026-07-07/google_trends_iot_US_veggi_20260707T165438Z.json`

```json
{
  "source": "google_trends",
  "endpoint": "interest_over_time",
  "extract_ts_utc": "2026-07-07T16:54:38+00:00",
  "artist": "veggi",
  "query": "veggi",
  "geo": "US",
  "geo_code": "US",
  "resolution": "national",
  "timeframe": "2025-10-11 2026-07-07",
  "granularity": "daily",
  "n_records": 270,
  "records": [
    {
      "date": "2025-10-11T00:00:00.000",
      "veggi": 0,
      "isPartial": false
    },
    {
      "date": "2025-10-12T00:00:00.000",
      "veggi": 0,
      "isPartial": false
    },
    {
      "date": "2025-10-13T00:00:00.000",
      "veggi": 0,
      "isPartial": false
    },
    "... 267 more items"
  ]
}
```

### Google Trends — iot per-DMA (daily series, geo=US-XX-DMA)

`gs://data-architecture-498123-raw/google_trends/dt=2026-07-07/google_trends_iot_US-WA-819_Temples_20260707T185811Z.json`

```json
{
  "source": "google_trends",
  "endpoint": "interest_over_time",
  "extract_ts_utc": "2026-07-07T18:58:11+00:00",
  "artist": "Temples",
  "query": "Temples",
  "geo": "US-WA-819",
  "geo_code": "US-WA-819",
  "resolution": "dma_series",
  "timeframe": "2025-10-11 2026-07-07",
  "granularity": "daily",
  "n_records": 270,
  "records": [
    {
      "date": "2025-10-11T00:00:00.000",
      "Temples": 0,
      "isPartial": false
    },
    {
      "date": "2025-10-12T00:00:00.000",
      "Temples": 0,
      "isPartial": false
    },
    {
      "date": "2025-10-13T00:00:00.000",
      "Temples": 0,
      "isPartial": false
    },
    "... 267 more items"
  ]
}
```

### Google Trends — ibr DMA snapshot (cross-DMA cross-section)

`gs://data-architecture-498123-raw/google_trends/dt=2026-07-07/google_trends_ibr_DMA_paris-jackson_20260707T162935Z.json`

```json
{
  "source": "google_trends",
  "endpoint": "interest_by_region",
  "extract_ts_utc": "2026-07-07T16:29:35+00:00",
  "artist": "paris jackson",
  "query": "paris jackson",
  "geo": "US",
  "geo_code": null,
  "resolution": "DMA",
  "timeframe": "today 12-m",
  "granularity": "snapshot",
  "n_records": 210,
  "records": [
    {
      "geoName": "Abilene-Sweetwater TX",
      "geoCode": "662",
      "paris jackson": 10
    },
    {
      "geoName": "Albany GA",
      "geoCode": "525",
      "paris jackson": 8
    },
    {
      "geoName": "Albany-Schenectady-Troy NY",
      "geoCode": "532",
      "paris jackson": 10
    },
    "... 207 more items"
  ]
}
```

### YouTube — daily channel-stats rollup

`gs://data-architecture-498123-raw/youtube/dt=2026-07-07/youtube_20260707T163128Z.json`

```json
{
  "source": "youtube",
  "extract_ts_utc": "2026-07-07T16:31:28+00:00",
  "n_records": 398,
  "records": [
    {
      "query": "Chasing Abbey",
      "official_channel_id": "UCjQSj96YTCBJWNNHyROnk-A",
      "official_channel_title": "Chasing Abbey",
      "official_subscribers": 35700,
      "official_total_views": 8404106,
      "official_video_count": 94,
      "topic_channel_id": "UC1-VefzIMgLBHCcQZuE_MfA",
      "topic_channel_title": "Chasing Abbey - Topic",
      "topic_total_views": 3640695,
      "topic_video_count": 27
    },
    {
      "query": "Temples",
      "official_channel_id": "UCdorDsEgYHYAFhWC2a2l8YA",
      "official_channel_title": "TemplesOfficial",
      "official_subscribers": 63300,
      "official_total_views": 24638375,
      "official_video_count": 51,
      "topic_channel_id": "UCAwEVNrw_7At5j2ccsDc_wQ",
      "topic_channel_title": "Temples - Topic",
      "topic_total_views": 9948939,
      "topic_video_count": 291
    },
    {
      "query": "Slow Magic",
      "official_channel_id": "UCd9ZxP1w4-8TGlbjdDEoh5A",
      "official_channel_title": "Slow Magic",
      "official_subscribers": 10500,
      "official_total_views": 3421241,
      "official_video_count": 407,
      "topic_channel_id": "UCmUORL97OeG5TxWhVAsB40Q",
      "topic_channel_title": "Slow Magic - Topic",
      "topic_total_views": 1936905,
      "topic_video_count": 198
    },
    "... 395 more items"
  ]
}
```

### 19hz.info — raw listing HTML (bronze is the untouched page)

`gs://data-architecture-498123-raw/nineteenhz/dt=2026-07-08/nineteenhz_bayarea_20260708T075234Z.html` (excerpt at first `<tr>`)

```html
<tr>
            <th class="table-date">Date/Time</th>
            <th>Event Title @ Venue</th>
            <th>Tags</th>
            <th>Price | Age</th>
            <th>Organizers</th>
            <th>Links</th>
            <th></th>
        </tr></thead>
	    <tbody>
			<tr><td>Mon: Jul 6-Wed: Jul 15 <br />(Mon: 3pm-Wed: 3pm)</td><td><a href='https://www.mutantfest.org/'>Autonomous Mutant Festival</a> @ TBA (Cascadia)<td>multigenre dance</td><td>free</td><td></td><td><a href='https://www.instagram.com/p/DZiZv3yjxIu/?img_index=1'>Instagram Page</a><br /></td><td><div class='shrink'>2026/07/06</div></td></tr><tr><td>Tue: Jul 7 <br />(8pm-12am)</td><td><a href='https://www.instagram.com/p/DaQ6ZShSLfs/'>WTF: Fundraiser for SFPD Victims w/ 2Dahlia, Lilotus, The Baptist, Del, Ms.Smith</a> @ The Stud (San Francisco)<td>techno, breaks, dub, dubstep, bass music, house</td><td>donations notaflof | 21+</td><td>Age of Sin</td><td></td><td><div class='shrink'>2026/07/07</div></td></tr><tr><td>Tue: Jul 7 <br />(9pm-2am)</td><td><a href='https://ra.co/events/2456309'>Interzone Darkwave Tuesdays</a> @ F8 1192 Folsom (San Francisco)<td>darkwave</td><td>free b4 1030 / $5 | 21+</td><td>Hex Embrace
```

Parsed row (committed `eda/output/nineteenhz_events.csv`):

```json
{
  "event_date": "2026-07-06",
  "datetime_text": "Mon: Jul 6-Wed: Jul 15  (Mon: 3pm-Wed: 3pm)",
  "title": "Autonomous Mutant Festival",
  "venue": "TBA",
  "city": "Cascadia",
  "genres": "multigenre dance",
  "price_text": "free",
  "age_restriction": "",
  "is_free": "True",
  "price_min": "0.0",
  "price_max": "0.0",
  "price_open_ended": "False",
  "organizers": "",
  "artists": "Autonomous Mutant Festival",
  "n_artists": "1",
  "ticket_url": "https://www.mutantfest.org/",
  "ticket_domain": "mutantfest.org",
  "extract_ts_utc": "2026-07-08T07:52:33+00:00"
}
```

### Resident Advisor — GraphQL eventListings response

`gs://data-architecture-498123-raw/ra/dt=2026-07-08/ra_bayarea_20260708T075250Z.json`

```json
{
  "request_variables": {
    "filters": {
      "areas": {
        "eq": 218
      },
      "listingDate": {
        "gte": "2026-07-08T00:00:00.000Z",
        "lte": "2026-09-06T23:59:59.999Z"
      }
    },
    "pageSize": 100,
    "page": 1
  },
  "extract_ts_utc": "2026-07-08T07:52:50+00:00",
  "response": {
    "data": {
      "eventListings": {
        "data": [
          {
            "event": {
              "id": "2480119",
              "title": "Run it Back presents Trevor's Birthday Jam Feat. starfari and Clayton Williams",
              "date": "2026-07-08T00:00:00.000",
              "startTime": "2026-07-08T21:00:00.000",
              "endTime": "2026-07-09T02:00:00.000",
              "attending": 12,
              "isTicketed": true,
              "cost": "0-10",
              "contentUrl": "/events/2480119",
              "venue": {
                "id": "91478",
                "name": "F8 1192 Folsom"
              },
              "artists": [
                {
                  "id": "161904",
                  "name": "starfari"
                },
                {
                  "id": "150949",
                  "name": "DJ Parrot"
                },
                "... 2 more items"
              ],
              "genres": [
                {
                  "name": "Techno"
                },
                {
                  "name": "Tech House"
                }
              ]
            }
          },
          {
            "event": {
              "id": "2476779",
              "title": "ITALO FRISCO",
              "date": "2026-07-08T00:00:00.000",
              "startTime": "2026-07-08T21:30:00.000",
              "endTime": "2026-07-09T01:30:00.000",
              "attending": 8,
              "isTicketed": false,
              "cost": "0",
              "contentUrl": "/events/2476779",
              "venue": {
                "id": "11134",
                "name": "Madrone Art Bar"
              },
              "artists": [
                {
                  "id": "96475",
                  "name": "Nino Msk"
                }
              ],
              "genres": [
                {
                  "name": "House"
                },
                {
                  "name": "Acid"
                }
              ]
            }
          },
          "... 98 more items"
        ],
        "totalResults": 176
      }
    }
  }
}
```

### Ticket pages — schema.org JSON-LD offers (first page payload)

`gs://data-architecture-498123-raw/ticketpages/dt=2026-07-08/ticketpages_jsonld_20260708T075528Z.json`

```json
{
  "ticket_url": "https://shotgun.live/en/events/ffdjuly26",
  "extract_ts_utc": "2026-07-08T07:53:06+00:00",
  "event_ld": [
    {
      "@context": "https://schema.org",
      "@type": "MusicEvent",
      "name": "Five Finger Disco: Fever",
      "url": "https://shotgun.live/en/events/ffdjuly26",
      "image": "https://res.cloudinary.com/shotgun/image/upload/c_limit,w_1200,h_630/f_jpg/q_auto/c_limit,f_auto,fl_lossy,q_auto,w_1920/v1781244224/production/artworks/FFD_July…",
      "startDate": "2026-07-12T04:00:00.000Z",
      "doorTime": "2026-07-12T04:00:00.000Z",
      "endDate": "2026-07-12T09:00:00.000Z",
      "eventStatus": "https://schema.org/EventScheduled",
      "eventAttendanceMode": "https://schema.org/OfflineEventAttendanceMode",
      "location": {
        "@type": "Place",
        "name": "White Horse Inn",
        "address": {
          "@type": "PostalAddress",
          "streetAddress": "6551 Telegraph Avenue, Oakland, CA 94609, USA",
          "addressLocality": "Oakland",
          "postalCode": "94609",
          "addressCountry": "US"
        },
        "geo": {
          "@type": "GeoCoordinates",
          "latitude": 37.8518352,
          "longitude": -122.2606209
        }
      },
      "description": "The Summer haze is setting in, and Five Finger Disco is here to make you SWEAT!\n\nWe're bringing back Oakland Royalty and one of our favs, Amal, for an extended …",
      "organizer": {
        "@type": "LocalBusiness",
        "name": "Charles Hawthorne",
        "url": "https://shotgun.live/en/venues/charles-hawthorne"
      },
      "performer": [
        {
          "@type": "MusicGroup",
          "name": "AMAL",
          "url": "https://shotgun.live/en/artists/djemelle"
        },
        {
          "@type": "MusicGroup",
          "name": "Charles Hawthorne",
          "url": "https://shotgun.live/en/artists/charles-hawthorne-4"
        }
      ],
      "offers": [
        {
          "@type": "Offer",
          "availability": "https://schema.org/InStock",
          "name": "General Admission",
          "price": 10,
          "priceCurrency": "USD",
          "validFrom": "2026-06-12T15:57:55.000Z",
          "url": "https://shotgun.live/en/events/ffdjuly26"
        },
        {
          "@type": "Offer",
          "availability": "https://schema.org/InStock",
          "name": "Pay-It-Forward Admission",
          "price": 15,
          "priceCurrency": "USD",
          "validFrom": "2026-06-12T15:57:55.000Z",
          "url": "https://shotgun.live/en/events/ffdjuly26"
        }
      ]
    }
  ]
}
```


## Silver/gold coverage

### Freshness (`MAX(snapshot_date)` per fact table)

| src | latest | n |
|---|---|---|
| fact_event_demand | 2026-07-07 | 948433 |
| fact_trends | 2026-07-06 | 344820 |
| fact_trends_daily | 2026-07-07 | 1620929 |
| fact_youtube | 2026-07-07 | 9493 |
| tm_observations | 2026-07-08 | 889248 |

### Ticketmaster pricing coverage (tm_observations, honest history)

| events | obs | pct_obs_priced | events_priced |
|---|---|---|---|
| 44296 | 889248 | 22.5 | 10760 |

Priced-from-first-observation split (why re-polling unpriced events is pointless):

| events | ever_priced | priced_from_day1 | gained_price_later |
|---|---|---|---|
| 44296 | 10760 | 10755 | 5 |

### Headliner resolution (caps every Trends/YouTube join)

| priced_upcoming | with_headliner | pct |
|---|---|---|
| 8143 | 3601 | 44.2 |

### Google Trends targeting (upcoming headliner×DMA pairs by tier)

| seg | pairs | artists | events |
|---|---|---|---|
| Bay Area (DMA 807) | 833 | 833 | 1021 |
| EDM nationwide | 379 | 206 | 417 |
| all upcoming | 23099 | 5585 | 30328 |
| next 90 days | 15775 | 4724 | 20415 |
| tier-1 (Bay 807 OR EDM) | 1197 | 1001 | 1420 |

## Bay Area cross-source overlap (19hz vs RA vs Ticketmaster)

Match key: (normalized venue name, event date). Shared date window: **2026-07-08 .. 2026-07-24** (RA's single daily request returns one 100-event page ≈ 2.5 weeks ahead, which bounds the window).

| metric | events |
|---|---|
| 19hz_in_window | 214 |
| ra_in_window | 94 |
| tm_in_window | 137 |
| only_19hz | 154 |
| only_ra | 41 |
| only_tm | 128 |
| hz_and_tm | 9 |
| ra_and_tm | 2 |
| hz_and_ra | 53 |
| all_three | 2 |
| union | 383 |

Venue-name matching is exact-after-normalization — venue aliases ('The Endup' vs 'EndUp') under-count matches slightly; treat the only-in-X numbers as upper bounds.

### Pricing fill per source

| source | events | with_price | note |
|---|---|---|---|
| 19hz (Bay Area listing) | 456 | 340 (74.6%) | +79 explicitly free |
| RA (area 218, 1 page) | 100 | 68 (68.0%) | cost_text, unparsed |
| TM (DMA 807 upcoming) | 1138 | 307 (27.0%) | priceRanges, primary only |
| ticket pages (JSON-LD) | 46 | 46 (100.0%) | offer rows incl. availability |

### Unique fields the new sources add

| source | field | fill | example/note |
|---|---|---|---|
| 19hz | genres | 100.0% | genre tags per event (TM genre is sparse/coarse) |
| 19hz | full lineup | 100% | 990 artist credits across 456 events (b2b split) |
| 19hz | age_restriction | 68.2% | 18+/21+ |
| 19hz | is_free | 17.3% | explicit free-event flag |
| RA | attending | 100% | total 2700 across 100 events — a per-event social demand signal no other source has |
| RA | genres | 87.0% | curated electronic subgenres |
| ticket pages | availability | 100.0% | InStock×35, LimitedAvailability×1, SoldOut×10 |

Top-5 RA events by `attending` (the demand-signal preview):

| attending | title | date |
|---|---|---|
| 893 | THROTTLE: Marie Vaunt (Low Ticket Warning) | 2026-07-10 |
| 354 | SQUISH [REDACTED] YEAR ANNIVERSARY | 2026-07-24 |
| 113 | Stephan Bodzin | 2026-07-17 |
| 96 | Louie Vega presented By Public Works & 15Utah | 2026-07-10 |
| 82 | Club Moniker: K Wata (live) + zi! | 2026-07-17 |
