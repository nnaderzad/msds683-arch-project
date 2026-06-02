# Ticketmaster POC for Milestone 1

## Purpose

For Milestone 1, we need to prove that Ticketmaster can work as the event anchor source for our live music demand analytics project.

This POC should answer:

- Can we authenticate with a Ticketmaster API key?
- Can we retrieve upcoming music events in the Bay Area?
- Can we extract event, venue, artist, genre, sale-date, status, and price-range fields?
- Are enough fields populated to support our planned data architecture?
- What limitations should we mention before committing to this source?

Ticketmaster is the replacement candidate for SeatGeek because SeatGeek access may require account approval. Ticketmaster's Discovery API has current official documentation and uses a straightforward API key.

## Official API Documentation

We are using the official Ticketmaster Discovery API v2 documentation:

https://developer.ticketmaster.com/products-and-docs/apis/discovery-api/v2/

The official docs state:

- Authentication uses an `apikey` query parameter.
- The Discovery API root URL is `https://app.ticketmaster.com/discovery/v2/`.
- The API supports searching for events, attractions, and venues.
- The API includes endpoints for events, event details, attractions, classifications, and venues.
- Default quota is 5000 API calls per day and 5 requests per second.

## Why Ticketmaster Discovery API v2?

We are using Ticketmaster Discovery API v2 because it gives us structured event-discovery data that fits our project:

- Upcoming live events.
- Venues and locations.
- Artists/performers, called `attractions` in Ticketmaster.
- Event classifications such as segment, genre, and subgenre.
- Event status such as onsale, offsale, canceled, postponed, or rescheduled.
- Public onsale start and end dates.
- Price ranges when available.

This is not identical to SeatGeek. SeatGeek is stronger for live ticket-market listing stats. Ticketmaster is stronger for official event discovery and easier API access.

## Authentication

Ticketmaster uses an API key passed as the `apikey` query parameter.

The script expects the key in an environment variable:

```bash
export TICKETMASTER_API_KEY="your_real_key"
```

The script then adds it to every API request automatically.

Do not put the API key directly into the Python file and do not commit it to GitHub.

## Endpoint Used By The POC Script

For the first live test, the script only calls one endpoint:

```text
GET /discovery/v2/events.json
```

Full URL pattern:

```text
https://app.ticketmaster.com/discovery/v2/events.json?apikey=YOUR_KEY&city=San%20Francisco&stateCode=CA&classificationName=music
```

Why this endpoint:

- It is the main event search endpoint.
- It supports filtering by city, state, date range, keyword, venue ID, and classification.
- It returns nested event, venue, attraction, classification, date, sales, status, and price-range data.
- It is enough to prove whether Ticketmaster can be our event anchor source.

## Filters Used In The Script

The script supports these filters:

- `countryCode=US`: limits results to the United States.
- `stateCode=CA`: defaults to California.
- `city`: optional city filter, such as San Francisco or Oakland.
- `keyword`: optional text search, such as a venue, artist, or event name.
- `venueId`: optional Ticketmaster venue ID.
- `classificationName=music`: defaults to music events.
- `startDateTime`: beginning of the upcoming event window.
- `endDateTime`: end of the upcoming event window.
- `sort=date,asc`: returns events in date order.
- `includeTBA=no`: excludes events with dates to be announced.
- `includeTBD=no`: excludes events with dates to be determined.
- `size`: number of events per page.
- `page`: page number for pagination.

These filters let us test narrow scopes like one city, one venue keyword, or all music events in California.

## Ticketmaster Endpoints We May Use Later

The official Discovery API also includes:

- `GET /discovery/v2/events/{id}.json`: get full details for one event.
- `GET /discovery/v2/venues.json`: search venues.
- `GET /discovery/v2/venues/{id}.json`: get one venue's details.
- `GET /discovery/v2/attractions.json`: search attractions, which are artists/performers.
- `GET /discovery/v2/attractions/{id}.json`: get one attraction's details.
- `GET /discovery/v2/classifications.json`: search event classifications.

For Milestone 1, `events.json` is enough. Later, `venues.json` and `attractions.json` would help us build cleaner dimension tables.

## What Information Ticketmaster Gives Us

Ticketmaster returns structured JSON. The most useful fields for our project are below.

### Event fields

- `id`: Ticketmaster event ID.
- `name`: Event name.
- `type`: Entity type.
- `url`: Ticketmaster event URL.
- `dates.start.localDate`: Event local date.
- `dates.start.localTime`: Event local time.
- `dates.start.dateTime`: Event UTC datetime.
- `dates.timezone`: Event timezone.
- `dates.status.code`: Event status, such as onsale, offsale, canceled, postponed, or rescheduled.

These fields support an event dimension and event-level snapshots.

### Sales fields

Nested under `sales.public`:

- `startDateTime`: Public onsale start timestamp.
- `endDateTime`: Public onsale end timestamp.
- `startTBD`: Whether public onsale start is still to be determined.

These fields let us analyze timing around when tickets go on sale.

### Price range fields

Nested under `priceRanges`:

- `type`: Price type, usually standard.
- `currency`: Currency code.
- `min`: Minimum listed price range.
- `max`: Maximum listed price range.

Important limitation: Ticketmaster price ranges are not the same as SeatGeek's resale listing stats. Ticketmaster does not give us fields like `listing_count` or average resale price in this POC.

### Venue fields

Nested under `_embedded.venues`:

- `id`: Venue ID.
- `name`: Venue name.
- `city.name`: Venue city.
- `state.stateCode`: Venue state code.
- `country.countryCode`: Venue country code.
- `postalCode`: Venue postal code.
- `address.line1`: Street address.
- `location.latitude`: Latitude.
- `location.longitude`: Longitude.

These fields can become a venue dimension table.

### Artist / attraction fields

Nested under `_embedded.attractions`:

- `id`: Attraction ID.
- `name`: Artist or performer name.
- `url`: Ticketmaster attraction URL.
- `classifications`: Classification metadata for the attraction.

Ticketmaster calls artists and performers `attractions`. These fields can become an artist/performer dimension table and can later be joined to Spotify.

### Classification fields

Nested under `classifications`:

- `segment.name`: Broad segment, such as Music.
- `genre.name`: Genre, such as Rock, Pop, Dance/Electronic, or Hip-Hop/Rap.
- `subGenre.name`: More specific subgenre.

These fields support genre-level analytics.

## Fields Extracted By The Script

The current script flattens each event into one row with:

- `extract_ts_utc`
- `event_id`
- `event_name`
- `event_type`
- `event_url`
- `local_date`
- `local_time`
- `date_time_utc`
- `timezone`
- `status_code`
- `public_sale_start_utc`
- `public_sale_end_utc`
- `public_sale_start_tbd`
- `price_type`
- `price_currency`
- `price_min`
- `price_max`
- `venue_id`
- `venue_name`
- `venue_city`
- `venue_state_code`
- `venue_country_code`
- `venue_postal_code`
- `venue_address`
- `venue_latitude`
- `venue_longitude`
- `attraction_ids`
- `attraction_names`
- `segment`
- `genre`
- `subgenre`

Why these fields matter:

- `extract_ts_utc` lets repeated runs become historical snapshots.
- Event fields identify the event and when it happens.
- `status_code` can show whether events are onsale, offsale, canceled, postponed, or rescheduled.
- Sale-date fields support demand analysis around ticket release timing.
- Price fields support price-range analytics when populated.
- Venue fields support venue and geography analysis.
- Attraction fields connect events to artists and later Spotify data.
- Genre fields support genre-level comparisons.

## How To Run The POC

Set your API key:

```bash
export TICKETMASTER_API_KEY="your_real_key"
```

Fetch upcoming music events in San Francisco:

```bash
python3 ticketmaster_api/ticketmaster_poc.py --city "San Francisco" --state-code CA --output sf_events.csv
```

Fetch upcoming music events in Oakland:

```bash
python3 ticketmaster_api/ticketmaster_poc.py --city Oakland --state-code CA --output oakland_events.csv
```

Search by keyword:

```bash
python3 ticketmaster_api/ticketmaster_poc.py --keyword "Greek Theatre" --state-code CA --output greek_theatre_ticketmaster.csv
```

Expand the future window:

```bash
python3 ticketmaster_api/ticketmaster_poc.py --city "San Francisco" --state-code CA --days-ahead 365 --output sf_events_365.csv
```

## How To Verify Data Richness

The script prints a data richness summary after each run:

- Number of events returned.
- Number of events with `venue_id`.
- Number of events with `attraction_names`.
- Number of events with `genre`.
- Number of events with `price_min`.
- Event status counts.

For Milestone 1, Ticketmaster is probably viable if:

- We can retrieve at least 50 upcoming music events across the Bay Area.
- Most events have venue IDs and venue names.
- Most events have attraction names.
- Most events have genre or subgenre values.
- Some meaningful share of events have price ranges.
- Event status and sale date fields are populated often enough to discuss availability/ticket lifecycle.

Price range coverage may be incomplete. That does not automatically make Ticketmaster unusable. It means our architecture should frame Ticketmaster as an event-discovery and official price-range source, not a resale marketplace source.

## Live POC Results

We tested Ticketmaster Discovery API v2 on June 1, 2026 using four Bay Area city-level music-event pulls:

```bash
python3 ticketmaster_api/ticketmaster_poc.py --city "San Francisco" --state-code CA --output sf_events.csv
python3 ticketmaster_api/ticketmaster_poc.py --city Oakland --state-code CA --output oakland_events.csv
python3 ticketmaster_api/ticketmaster_poc.py --city Berkeley --state-code CA --output berkeley_events.csv
python3 ticketmaster_api/ticketmaster_poc.py --city "San Jose" --state-code CA --output san_jose_events.csv
```

### Event counts by city

| City | Output file | Event rows |
|---|---:|---:|
| San Francisco | `sf_events.csv` | 150 |
| Oakland | `oakland_events.csv` | 77 |
| Berkeley | `berkeley_events.csv` | 46 |
| San Jose | `san_jose_events.csv` | 84 |
| **Combined** | all four files | **357** |

The combined dataset had:

- 357 unique event IDs.
- 51 unique venue IDs.
- 41 unique venue names.
- 386 unique attraction/artist names.
- 0 duplicate event IDs across the four city files.

### Combined field coverage

| Field | Populated rows | Coverage |
|---|---:|---:|
| `event_id` | 357/357 | 100.0% |
| `event_name` | 357/357 | 100.0% |
| `local_date` | 357/357 | 100.0% |
| `local_time` | 354/357 | 99.2% |
| `date_time_utc` | 354/357 | 99.2% |
| `status_code` | 357/357 | 100.0% |
| `public_sale_start_utc` | 357/357 | 100.0% |
| `public_sale_end_utc` | 357/357 | 100.0% |
| `venue_id` | 357/357 | 100.0% |
| `venue_name` | 357/357 | 100.0% |
| `venue_city` | 357/357 | 100.0% |
| `venue_latitude` | 357/357 | 100.0% |
| `venue_longitude` | 357/357 | 100.0% |
| `attraction_names` | 283/357 | 79.3% |
| `segment` | 357/357 | 100.0% |
| `genre` | 355/357 | 99.4% |
| `subgenre` | 331/357 | 92.7% |
| `price_min` | 120/357 | 33.6% |
| `price_max` | 120/357 | 33.6% |

### Event status coverage

| Status | Count |
|---|---:|
| `onsale` | 346 |
| `rescheduled` | 5 |
| `cancelled` | 4 |
| `offsale` | 2 |

### Top genres

The combined output included a useful spread of music genres:

| Genre | Count |
|---|---:|
| Rock | 85 |
| Other | 54 |
| Pop | 47 |
| Alternative | 37 |
| Hip-Hop/Rap | 31 |
| Latin | 23 |
| R&B | 19 |
| World | 12 |
| Dance/Electronic | 12 |
| Metal | 9 |
| Country | 8 |
| Classical | 4 |

### Top venues

| Venue | Count |
|---|---:|
| The Ritz | 31 |
| Oakland Arena | 25 |
| Greek Theatre-U.C. Berkeley | 25 |
| San Jose Civic | 25 |
| SAP Center at San Jose | 25 |
| Fox Theater - Oakland | 24 |
| Crybaby | 20 |
| The Independent | 19 |
| Neck of the Woods | 17 |
| Brick and Mortar Music Hall | 15 |
| The UC Theatre | 13 |
| August Hall | 10 |
| Cafe Du Nord | 10 |
| The Chapel | 9 |
| The Regency Ballroom | 8 |

### POC conclusion

Ticketmaster Discovery API v2 is viable as our event anchor source. The live test returned 357 upcoming Bay Area music events across San Francisco, Oakland, Berkeley, and San Jose. Core event, venue, status, sale-window, genre, and geography fields were nearly complete. Attraction/artist names were populated for 79.3% of events, which is strong enough for joining to Spotify or Google Trends in a later step.

The main limitation is price coverage. Price ranges were populated for 33.6% of events, so pricing should be treated as a partially available feature, not the core measure of demand. The project should frame Ticketmaster as an official event-discovery, venue, artist, genre, status, sale-window, and partial price-range source.

## Important Limitations

Ticketmaster does not provide the same live ticket-market fields that SeatGeek may provide. In this POC, we should not claim we have:

- Live resale listing count.
- Current average resale price.
- Remaining ticket inventory.
- Historical price movement from Ticketmaster itself.

Instead, we can create time-series data by snapshotting Ticketmaster events repeatedly. Repeated runs can track changes in:

- Event status.
- Public sale windows.
- Price ranges when available.
- Event visibility in search results.

## Suggested Milestone 1 Summary

Ticketmaster Discovery API v2 is a viable event anchor candidate for our live music demand analytics architecture. We use the official `/events.json` endpoint with filters for California, city, music classification, date range, keyword, and venue ID. In live tests across San Francisco, Oakland, Berkeley, and San Jose, the API returned 357 unique upcoming music events across 51 unique venue IDs and 386 unique attraction/artists. Core event, date/time, status, sale-window, venue, location, and genre fields were nearly complete. Price ranges were available for 33.6% of events, so our project should treat Ticketmaster as an official event-discovery and partial price-range source, not a live resale marketplace source. We can create temporal data by snapshotting API results over time.
