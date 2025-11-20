# Lovable Website Integration Guide

## Your API URL
Once deployed on Railway, you'll have a URL like:
```
https://cambridge-events-api-production.up.railway.app
```

Replace `YOUR_API_URL` below with your actual Railway URL.

---

## Quick Start - Fetch Events

### Basic Fetch (All Events)
```javascript
const API_URL = "YOUR_API_URL";

async function fetchAllEvents() {
  const response = await fetch(`${API_URL}/events?limit=100`);
  const events = await response.json();
  return events;
}

// Usage
const events = await fetchAllEvents();
console.log(`Loaded ${events.length} events`);
```

### Fetch Events by Category
```javascript
async function fetchEventsByCategory(category) {
  // Categories: theater, music, comedy, arts and culture, community,
  //             food and drink, sports and recreation, education, family
  const response = await fetch(`${API_URL}/events?category=${category}&limit=50`);
  const events = await response.json();
  return events;
}

// Usage
const theaterEvents = await fetchEventsByCategory('theater');
const musicEvents = await fetchEventsByCategory('music');
```

### Fetch Events by City
```javascript
async function fetchEventsByCity(city) {
  // Cities: Cambridge, Somerville
  const response = await fetch(`${API_URL}/events?city=${city}&limit=50`);
  const events = await response.json();
  return events;
}

// Usage
const cambridgeEvents = await fetchEventsByCity('Cambridge');
const somervilleEvents = await fetchEventsByCity('Somerville');
```

### Search Events
```javascript
async function searchEvents(query) {
  const response = await fetch(`${API_URL}/events/search?q=${encodeURIComponent(query)}&limit=50`);
  const events = await response.json();
  return events;
}

// Usage
const jazzEvents = await searchEvents('jazz');
const comedyEvents = await searchEvents('comedy');
```

### Fetch Events by Date Range
```javascript
async function fetchEventsByDateRange(startDate, endDate) {
  // Dates in ISO format: "2025-11-20"
  const params = new URLSearchParams({
    start_date: startDate,
    end_date: endDate,
    limit: 100
  });

  const response = await fetch(`${API_URL}/events?${params}`);
  const events = await response.json();
  return events;
}

// Usage - Get this week's events
const today = new Date().toISOString().split('T')[0];
const nextWeek = new Date(Date.now() + 7*24*60*60*1000).toISOString().split('T')[0];
const thisWeek = await fetchEventsByDateRange(today, nextWeek);
```

### Get Event Statistics
```javascript
async function fetchStats() {
  const response = await fetch(`${API_URL}/stats`);
  const stats = await response.json();
  return stats;
}

// Usage
const stats = await fetchStats();
console.log(`Total events: ${stats.total_events}`);
console.log('Events by category:', stats.categories);
console.log('Events by source:', stats.sources);
```

---

## Event Data Structure

Each event has this structure:

```javascript
{
  "id": "uuid-string",
  "title": "Event Title",
  "description": "Full event description...",
  "start_datetime": "2025-11-20T19:00:00",
  "end_datetime": null,
  "all_day": false,
  "venue_name": "Venue Name",
  "street_address": "123 Main St",
  "city": "Cambridge",
  "state": "MA",
  "zip_code": "02139",
  "category": "theater",
  "cost": null,
  "source_url": "https://...",
  "source_name": "Source Name",
  "image_url": null,
  "contact_email": null,
  "contact_phone": null,
  "website_url": null
}
```

---

## Advanced Usage

### Filter with Multiple Parameters
```javascript
async function fetchFilteredEvents(filters) {
  const params = new URLSearchParams();

  if (filters.category) params.append('category', filters.category);
  if (filters.city) params.append('city', filters.city);
  if (filters.startDate) params.append('start_date', filters.startDate);
  if (filters.endDate) params.append('end_date', filters.endDate);
  params.append('limit', filters.limit || 100);

  const response = await fetch(`${API_URL}/events?${params}`);
  return await response.json();
}

// Usage - Theater events in Cambridge this week
const events = await fetchFilteredEvents({
  category: 'theater',
  city: 'Cambridge',
  startDate: '2025-11-20',
  endDate: '2025-11-27',
  limit: 50
});
```

### Pagination
```javascript
async function fetchEventsWithPagination(page = 0, pageSize = 20) {
  const offset = page * pageSize;
  const response = await fetch(
    `${API_URL}/events?limit=${pageSize}&offset=${offset}`
  );
  return await response.json();
}

// Usage - Get page 2 (events 20-39)
const page2 = await fetchEventsWithPagination(1, 20);
```

### Error Handling
```javascript
async function fetchEventsWithErrorHandling() {
  try {
    const response = await fetch(`${API_URL}/events?limit=50`);

    if (!response.ok) {
      throw new Error(`API error: ${response.status}`);
    }

    const events = await response.json();
    return events;
  } catch (error) {
    console.error('Failed to fetch events:', error);
    return [];
  }
}
```

---

## Complete Example - Event List Component

```javascript
// Example React component for Lovable
import { useState, useEffect } from 'react';

const API_URL = "YOUR_API_URL";

function EventList() {
  const [events, setEvents] = useState([]);
  const [loading, setLoading] = useState(true);
  const [category, setCategory] = useState('');

  useEffect(() => {
    async function loadEvents() {
      setLoading(true);
      try {
        const url = category
          ? `${API_URL}/events?category=${category}&limit=50`
          : `${API_URL}/events?limit=50`;

        const response = await fetch(url);
        const data = await response.json();
        setEvents(data);
      } catch (error) {
        console.error('Failed to load events:', error);
      } finally {
        setLoading(false);
      }
    }

    loadEvents();
  }, [category]);

  if (loading) return <div>Loading events...</div>;

  return (
    <div>
      <select onChange={(e) => setCategory(e.target.value)} value={category}>
        <option value="">All Categories</option>
        <option value="theater">Theater</option>
        <option value="music">Music</option>
        <option value="comedy">Comedy</option>
        <option value="arts and culture">Arts & Culture</option>
        <option value="community">Community</option>
      </select>

      <div className="events-grid">
        {events.map(event => (
          <div key={event.id} className="event-card">
            <h3>{event.title}</h3>
            <p>{new Date(event.start_datetime).toLocaleDateString()}</p>
            <p>{event.venue_name}</p>
            <p>{event.city}</p>
            <p>{event.description.substring(0, 150)}...</p>
            <a href={event.source_url} target="_blank" rel="noopener">
              More Info
            </a>
          </div>
        ))}
      </div>
    </div>
  );
}

export default EventList;
```

---

## Testing Your Deployment

After deploying, test these endpoints in your browser or with curl:

```bash
# Health check
curl YOUR_API_URL/health

# Get events
curl YOUR_API_URL/events?limit=5

# Search
curl YOUR_API_URL/events/search?q=theater

# Stats
curl YOUR_API_URL/stats

# Categories
curl YOUR_API_URL/categories
```

---

## Available Categories

- `theater`
- `music`
- `comedy`
- `arts and culture`
- `community`
- `food and drink`
- `sports and recreation`
- `education`
- `family`

---

## Event Sources (668 total events)

Your API aggregates events from:
- Harvard Radcliffe Dramatic Club (HRDC)
- Theatre at First
- Arts at the Armory
- Lilypad Inman
- The Middle East
- Lamplighter Brewing
- Portico Brewing
- Porter Square Books
- Harvard Box Office
- Cambridge.gov Events

---

## Need Help?

- API Documentation: `YOUR_API_URL/` (root endpoint shows all available endpoints)
- GitHub Repo: https://github.com/karakotaram/cambridge-events-api
- Check deployment logs in Railway dashboard
