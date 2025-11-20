# API Deployment Guide

Your Cambridge Events API is ready to deploy! Here's how to get it online so your Lovable website can access it.

## Quick Start with Railway (Recommended)

Railway is a free hosting service that's perfect for this API. Here's how to deploy:

### 1. Create a Railway Account
1. Go to [railway.app](https://railway.app/)
2. Sign up with GitHub (recommended for easy deployment)

### 2. Deploy from GitHub

**Option A: If your code is on GitHub**
1. Push this project to GitHub if you haven't already
2. In Railway, click "New Project"
3. Select "Deploy from GitHub repo"
4. Choose your repository
5. Railway will automatically detect it's a Python project and deploy it

**Option B: Deploy from CLI**
1. Install Railway CLI: `npm install -g @railway/cli` or `curl -fsSL https://railway.app/install.sh | sh`
2. Login: `railway login`
3. Initialize: `railway init`
4. Deploy: `railway up`

### 3. Add the events.json file
Since the `data/events.json` file contains your event data, you need to make sure it's deployed:
1. In your Railway project dashboard, go to "Variables"
2. Or, commit the `data/events.json` file to your repository

### 4. Get Your API URL
Once deployed, Railway will give you a public URL like:
`https://your-project-name.up.railway.app`

### 5. Test Your Deployed API
```bash
curl https://your-project-name.up.railway.app/health
curl https://your-project-name.up.railway.app/events?limit=5
```

## Alternative: Deploy to Render

Render is another free option:

1. Go to [render.com](https://render.com/)
2. Sign up with GitHub
3. Click "New +" → "Web Service"
4. Connect your repository
5. Configure:
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command:** `uvicorn src.api.main:app --host 0.0.0.0 --port $PORT`
6. Click "Create Web Service"

## Connecting to Your Lovable Website

Once deployed, you'll have a public URL. Use it in your Lovable website:

### Example JavaScript Code for Lovable:

```javascript
// Replace with your actual Railway URL
const API_URL = "https://your-project-name.up.railway.app";

// Fetch all events
async function fetchEvents() {
  const response = await fetch(`${API_URL}/events?limit=100`);
  const events = await response.json();
  return events;
}

// Fetch events by category
async function fetchEventsByCategory(category) {
  const response = await fetch(`${API_URL}/events?category=${category}`);
  const events = await response.json();
  return events;
}

// Search events
async function searchEvents(query) {
  const response = await fetch(`${API_URL}/events/search?q=${query}`);
  const events = await response.json();
  return events;
}

// Fetch events by city
async function fetchEventsByCity(city) {
  const response = await fetch(`${API_URL}/events?city=${city}`);
  const events = await response.json();
  return events;
}

// Get event statistics
async function fetchStats() {
  const response = await fetch(`${API_URL}/stats`);
  const stats = await response.json();
  return stats;
}
```

## Available API Endpoints

Your API has these endpoints ready to use:

- `GET /` - API information
- `GET /health` - Health check (shows total events count)
- `GET /events` - Get all events with optional filters:
  - `?category=theater` - Filter by category
  - `?city=Cambridge` - Filter by city
  - `?limit=50` - Limit results (default: 100)
  - `?offset=0` - Pagination offset
  - `?start_date=2025-11-20` - Events after date
  - `?end_date=2025-12-31` - Events before date
- `GET /events/{event_id}` - Get specific event by ID
- `GET /events/search?q={query}` - Search events by keyword
- `GET /categories` - List all event categories
- `GET /sources` - List all event sources with counts
- `GET /stats` - Get event statistics

## Event Categories

Your events are categorized as:
- `theater`
- `music`
- `comedy`
- `arts and culture`
- `community`
- `food and drink`
- `sports and recreation`
- `education`
- `family`

## CORS Configuration

The API is already configured to accept requests from:
- `https://cambridge-event-compass.lovable.app` (your website)
- `http://localhost:5173` (local development)
- `http://localhost:3000` (local development)

## Updating Events Data

To update the events data after deploying:

1. Run your scraper locally to update `data/events.json`
2. Commit and push the changes to GitHub
3. Railway/Render will automatically redeploy with the new data

Or set up automatic scraping on the server (advanced):
- Use Railway Cron Jobs or GitHub Actions
- Schedule the scraper to run daily/weekly
- Auto-commit updated events.json

## Troubleshooting

**API not responding?**
- Check Railway logs in the dashboard
- Ensure `data/events.json` exists in the deployment
- Verify the start command is correct

**CORS errors?**
- Make sure your Lovable domain is in the allowed origins list in `src/api/main.py`

**No events returned?**
- Check that `data/events.json` has been deployed
- Test the `/health` endpoint to see total event count
