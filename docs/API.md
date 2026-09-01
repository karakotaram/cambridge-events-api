# Cambridge Calendar — Public API

REST API for accessing aggregated Cambridge & Somerville, MA event data. Events
are scraped daily from 20+ local venues, deduplicated, categorized, and geocoded.

- **Base URL:** `https://web-production-00281.up.railway.app`
- **Format:** JSON (all responses)
- **Auth:** None required for read endpoints (see [Authentication](#authentication))
- **CORS:** Open to all origins — safe to call directly from a browser
- **Compression:** gzip enabled for responses over 1 KB
- **Interactive docs:** `https://web-production-00281.up.railway.app/docs` (Swagger UI, auto-generated)
- **OpenAPI schema:** `https://web-production-00281.up.railway.app/openapi.json`

## Quick start

```bash
# Upcoming events, ranked by relevance (lightweight payload)
curl "https://web-production-00281.up.railway.app/events/slim?limit=20"

# Search
curl "https://web-production-00281.up.railway.app/events/search?q=jazz"

# Full detail for one event
curl "https://web-production-00281.up.railway.app/events/{event_id}"
```

## Rate limits

There is no hard rate limit today, but please be reasonable: responses are cached
server-side for 5 minutes, so polling more often than that returns identical data.
For bulk/list use, prefer `/events/slim` over `/events` — it omits descriptions and
is significantly smaller. If you need a high-volume integration, contact the
maintainer so we can plan capacity.

---

## Endpoints

### `GET /events/slim`
Lightweight event list optimized for browsing, list, and map views. **Recommended
default for most integrations.** Omits descriptions; returns only essential fields.

**Query parameters**

| Param | Type | Default | Description |
|---|---|---|---|
| `category` | string | — | Filter by category (see [Categories](#categories)) |
| `city` | string | — | Filter by city (e.g. `Cambridge`, `Somerville`) |
| `source` | string | — | Filter by source/venue name |
| `free_only` | bool | — | Only free events |
| `family_friendly` | bool | — | Only family-friendly events |
| `upcoming_only` | bool | `true` | Only events from today forward |
| `ranked` | bool | `true` | Sort by relevance score; if `false`, sort by date ascending |
| `limit` | int | `1000` | Max results (1–5000) |
| `offset` | int | `0` | Pagination offset |

**Response** — array of slim event objects:

```json
[
  {
    "id": "a1b2c3d4e5f6",
    "title": "Friday Night Jazz",
    "start_datetime": "2026-06-05T19:00:00",
    "end_datetime": "2026-06-05T21:00:00",
    "venue_name": "Club Passim",
    "city": "Cambridge",
    "latitude": 42.3736,
    "longitude": -71.1211,
    "category": "music",
    "family_friendly": false,
    "image_url": "https://...",
    "source_url": "https://passim.org/event/123",
    "source_name": "Club Passim",
    "cost": "$20",
    "score": 0.842,
    "featured": false
  }
]
```

### `GET /events`
Full event list, including `description` and all metadata. Heavier payload — use
`/events/slim` unless you need descriptions.

**Query parameters**

| Param | Type | Default | Description |
|---|---|---|---|
| `category` | string | — | Filter by category |
| `city` | string | — | Filter by city |
| `source` | string | — | Filter by source/venue name |
| `start_date` | ISO 8601 | — | Events starting on/after this datetime |
| `end_date` | ISO 8601 | — | Events starting on/before this datetime |
| `upcoming_only` | bool | `false` | Only events from today forward |
| `family_friendly` | bool | — | Only family-friendly events |
| `ranked` | bool | `false` | Sort by relevance score |
| `sort_order` | `asc`\|`desc` | `asc` | Date sort order (ignored when `ranked=true`) |
| `limit` | int | `1000` | Max results (1–5000) |
| `offset` | int | `0` | Pagination offset |

### `GET /events/{event_id}`
Full detail for a single event. Returns `404` if not found.

### `GET /events/search`
Keyword search across event titles and descriptions, ranked by relevance.

| Param | Type | Default | Description |
|---|---|---|---|
| `q` | string | **required** | Search query (min 2 chars) |
| `limit` | int | `50` | Max results (1–500) |

### `GET /events/{event_id}/calendar.ics`
Download an `.ics` calendar file for an event. Works with Google Calendar, Outlook,
and Apple Calendar. Returns `text/calendar` with a `Content-Disposition` attachment.

### `GET /sources`
All event sources with event counts: `{"sources": {"Club Passim": 42, ...}}`

### `GET /categories`
All valid category values: `{"categories": ["music", "arts and culture", ...]}`

### `GET /stats`
Aggregate counts by category, source, and city, plus the overall date range.

### `GET /health`
Health check: `{"status": "healthy", "total_events": 1234, "last_updated": "..."}`

### `GET /featured`
The current Editor's Picks list (title + source_name pairs).

---

## Categories

`music`, `arts and culture`, `food and drink`, `theater`, `lectures`, `sports`,
`community`, `other`

## Event object (full)

| Field | Type | Notes |
|---|---|---|
| `id` | string | Stable unique ID |
| `title` | string | Required |
| `description` | string | Required (full endpoints only) |
| `start_datetime` | ISO 8601 | Required; Eastern Time |
| `end_datetime` | ISO 8601 \| null | Optional |
| `venue_name` | string \| null | |
| `street_address` | string \| null | |
| `city` / `state` | string \| null | |
| `latitude` / `longitude` | float \| null | When geocoded |
| `category` | string \| null | See [Categories](#categories) |
| `family_friendly` | bool | |
| `cost` | string \| null | e.g. `"Free"`, `"$20"` |
| `image_url` | string \| null | |
| `source_url` | string | Required; link back to the venue's page |
| `source_name` | string | Required; venue/source name |
| `featured` | bool | Editor's Pick flag |

---

## Authentication

Read endpoints above are public and need no key. A small set of admin/analytics
endpoints (e.g. `/analytics/interactions`) require an `api_key` query parameter
validated against a server-side `ADMIN_API_KEY` and are not intended for third-party use.

## Attribution & usage

Each event includes `source_url` and `source_name`. When displaying events, please
link back to the original `source_url` and credit the source venue. Data is
aggregated from public venue listings and is provided as-is, with no guarantee of
accuracy or completeness.

## Contact

For higher-volume access, questions, or to report a problem, contact the maintainer
at karan.arakotaram@gmail.com.
```