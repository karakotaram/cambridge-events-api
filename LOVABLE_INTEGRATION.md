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
const API_URL = "https://web-production-00281.up.railway.app/";

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

## Chat Component - AI Event Assistant

Replace the search box with this chat component for a natural language event discovery experience:

```javascript
import { useState, useRef, useEffect } from 'react';

const API_URL = "https://web-production-00281.up.railway.app";

function EventChat() {
  const [messages, setMessages] = useState([
    {
      role: 'assistant',
      content: "Hi! I'm your Cambridge & Somerville event guide. Ask me anything like:\n\n• \"What's happening this weekend?\"\n• \"Find live music next Saturday\"\n• \"Something fun for kids this Sunday\"\n\nWhat are you in the mood for?"
    }
  ]);
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);
  const messagesEndRef = useRef(null);

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  };

  useEffect(() => {
    scrollToBottom();
  }, [messages]);

  const sendMessage = async () => {
    if (!input.trim() || loading) return;

    const userMessage = input.trim();
    setInput('');

    // Add user message to chat
    const newMessages = [...messages, { role: 'user', content: userMessage }];
    setMessages(newMessages);
    setLoading(true);

    try {
      // Build conversation history (exclude the welcome message)
      const conversationHistory = newMessages
        .slice(1) // Skip welcome message
        .slice(-10) // Keep last 10 messages for context
        .map(m => ({ role: m.role, content: m.content }));

      const response = await fetch(`${API_URL}/chat`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          message: userMessage,
          conversation_history: conversationHistory.slice(0, -1) // Exclude current message
        })
      });

      if (!response.ok) {
        throw new Error('Failed to get response');
      }

      const data = await response.json();
      setMessages([...newMessages, { role: 'assistant', content: data.response }]);
    } catch (error) {
      console.error('Chat error:', error);
      setMessages([
        ...newMessages,
        { role: 'assistant', content: "Oops! I'm having trouble connecting right now. Try again in a moment?" }
      ]);
    } finally {
      setLoading(false);
    }
  };

  const handleKeyPress = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  };

  return (
    <div className="event-chat">
      <div className="chat-messages">
        {messages.map((msg, idx) => (
          <div key={idx} className={`message ${msg.role}`}>
            <div className="message-content">
              {msg.content.split('\n').map((line, i) => (
                <p key={i}>{line}</p>
              ))}
            </div>
          </div>
        ))}
        {loading && (
          <div className="message assistant">
            <div className="message-content typing">
              <span>●</span><span>●</span><span>●</span>
            </div>
          </div>
        )}
        <div ref={messagesEndRef} />
      </div>

      <div className="chat-input-container">
        <input
          type="text"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyPress={handleKeyPress}
          placeholder="Ask about events..."
          disabled={loading}
        />
        <button onClick={sendMessage} disabled={loading || !input.trim()}>
          Send
        </button>
      </div>
    </div>
  );
}

export default EventChat;
```

### Chat Component Styles

```css
.event-chat {
  display: flex;
  flex-direction: column;
  height: 500px;
  max-width: 600px;
  margin: 0 auto;
  border: 1px solid #e0e0e0;
  border-radius: 12px;
  overflow: hidden;
  background: #fff;
}

.chat-messages {
  flex: 1;
  overflow-y: auto;
  padding: 16px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.message {
  max-width: 85%;
  padding: 12px 16px;
  border-radius: 16px;
  line-height: 1.5;
}

.message.user {
  align-self: flex-end;
  background: #007AFF;
  color: white;
  border-bottom-right-radius: 4px;
}

.message.assistant {
  align-self: flex-start;
  background: #f0f0f0;
  color: #333;
  border-bottom-left-radius: 4px;
}

.message-content p {
  margin: 0 0 8px 0;
}

.message-content p:last-child {
  margin-bottom: 0;
}

.typing span {
  animation: bounce 1.4s infinite ease-in-out;
  display: inline-block;
  margin: 0 2px;
}

.typing span:nth-child(1) { animation-delay: -0.32s; }
.typing span:nth-child(2) { animation-delay: -0.16s; }

@keyframes bounce {
  0%, 80%, 100% { transform: translateY(0); }
  40% { transform: translateY(-6px); }
}

.chat-input-container {
  display: flex;
  padding: 12px;
  border-top: 1px solid #e0e0e0;
  background: #fafafa;
  gap: 8px;
}

.chat-input-container input {
  flex: 1;
  padding: 12px 16px;
  border: 1px solid #ddd;
  border-radius: 24px;
  font-size: 16px;
  outline: none;
}

.chat-input-container input:focus {
  border-color: #007AFF;
}

.chat-input-container button {
  padding: 12px 24px;
  background: #007AFF;
  color: white;
  border: none;
  border-radius: 24px;
  font-size: 16px;
  cursor: pointer;
  transition: background 0.2s;
}

.chat-input-container button:hover:not(:disabled) {
  background: #0056b3;
}

.chat-input-container button:disabled {
  background: #ccc;
  cursor: not-allowed;
}
```

---

## Need Help?

- API Documentation: `YOUR_API_URL/` (root endpoint shows all available endpoints)
- GitHub Repo: https://github.com/karakotaram/cambridge-events-api
- Check deployment logs in Railway dashboard
