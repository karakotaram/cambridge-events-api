"""Generate a clean HTML view of all scraped events"""
import json
from datetime import datetime

def generate_events_html():
    """Generate HTML file displaying all events"""

    # Load events
    with open('data/events.json', 'r') as f:
        events = json.load(f)

    # Sort events by date
    events.sort(key=lambda x: x['start_datetime'])

    # Group events by source
    by_source = {}
    for event in events:
        source = event['source_name']
        if source not in by_source:
            by_source[source] = []
        by_source[source].append(event)

    # Generate HTML
    html = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Cambridge-Somerville Events</title>
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }

        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, Cantarell, sans-serif;
            line-height: 1.6;
            color: #333;
            background: #f5f5f5;
            padding: 20px;
        }

        .container {
            max-width: 1200px;
            margin: 0 auto;
            background: white;
            padding: 40px;
            border-radius: 8px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }

        header {
            text-align: center;
            margin-bottom: 40px;
            padding-bottom: 20px;
            border-bottom: 3px solid #2c3e50;
        }

        h1 {
            color: #2c3e50;
            font-size: 2.5em;
            margin-bottom: 10px;
        }

        .summary {
            display: flex;
            justify-content: center;
            gap: 40px;
            margin: 20px 0;
            flex-wrap: wrap;
        }

        .stat {
            text-align: center;
        }

        .stat-number {
            font-size: 2em;
            font-weight: bold;
            color: #3498db;
        }

        .stat-label {
            color: #7f8c8d;
            font-size: 0.9em;
            text-transform: uppercase;
            letter-spacing: 1px;
        }

        .filters {
            margin: 30px 0;
            display: flex;
            gap: 15px;
            flex-wrap: wrap;
            justify-content: center;
        }

        .filter-btn {
            padding: 8px 20px;
            background: #ecf0f1;
            border: 2px solid #bdc3c7;
            border-radius: 20px;
            cursor: pointer;
            transition: all 0.3s;
            font-size: 0.9em;
        }

        .filter-btn:hover {
            background: #3498db;
            color: white;
            border-color: #3498db;
        }

        .filter-btn.active {
            background: #2c3e50;
            color: white;
            border-color: #2c3e50;
        }

        .source-section {
            margin: 40px 0;
        }

        .source-header {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            padding: 15px 20px;
            border-radius: 8px 8px 0 0;
            font-size: 1.3em;
            font-weight: 600;
        }

        .event-card {
            border: 1px solid #e0e0e0;
            padding: 20px;
            margin-bottom: 1px;
            background: white;
            transition: all 0.3s;
        }

        .event-card:last-child {
            border-radius: 0 0 8px 8px;
        }

        .event-card:hover {
            background: #f8f9fa;
            transform: translateX(5px);
            box-shadow: -3px 0 0 #3498db;
        }

        .event-title {
            font-size: 1.3em;
            color: #2c3e50;
            margin-bottom: 10px;
            font-weight: 600;
        }

        .event-title a {
            color: #2c3e50;
            text-decoration: none;
        }

        .event-title a:hover {
            color: #3498db;
        }

        .event-meta {
            display: flex;
            gap: 20px;
            margin-bottom: 15px;
            flex-wrap: wrap;
            color: #555;
            font-size: 0.95em;
        }

        .meta-item {
            display: flex;
            align-items: center;
            gap: 5px;
        }

        .meta-icon {
            color: #3498db;
        }

        .event-description {
            color: #555;
            line-height: 1.7;
            margin-top: 10px;
        }

        .category-badge {
            display: inline-block;
            padding: 4px 12px;
            border-radius: 12px;
            font-size: 0.8em;
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }

        .category-music { background: #e8f5e9; color: #2e7d32; }
        .category-arts { background: #fce4ec; color: #c2185b; }
        .category-food { background: #fff3e0; color: #e65100; }
        .category-sports { background: #e3f2fd; color: #1565c0; }
        .category-theater { background: #f3e5f5; color: #6a1b9a; }
        .category-community { background: #e0f2f1; color: #00695c; }
        .category-lectures { background: #fff9c4; color: #f57f17; }
        .category-other { background: #eceff1; color: #455a64; }

        footer {
            margin-top: 60px;
            text-align: center;
            color: #7f8c8d;
            padding-top: 20px;
            border-top: 1px solid #e0e0e0;
        }

        @media (max-width: 768px) {
            .container {
                padding: 20px;
            }

            h1 {
                font-size: 1.8em;
            }

            .summary {
                gap: 20px;
            }

            .event-meta {
                flex-direction: column;
                gap: 8px;
            }
        }
    </style>
</head>
<body>
    <div class="container">
        <header>
            <h1>Cambridge-Somerville Events</h1>
            <p style="color: #7f8c8d; font-size: 1.1em;">Discover local events in Cambridge and Somerville</p>

            <div class="summary">
"""

    # Add summary statistics
    html += f"""
                <div class="stat">
                    <div class="stat-number">{len(events)}</div>
                    <div class="stat-label">Total Events</div>
                </div>
                <div class="stat">
                    <div class="stat-number">{len(by_source)}</div>
                    <div class="stat-label">Sources</div>
                </div>
"""

    # Count categories
    categories = {}
    for event in events:
        cat = event.get('category', 'other')
        categories[cat] = categories.get(cat, 0) + 1

    html += f"""
                <div class="stat">
                    <div class="stat-number">{len(categories)}</div>
                    <div class="stat-label">Categories</div>
                </div>
            </div>
        </header>
"""

    # Add filter buttons
    html += """
        <div class="filters">
            <button class="filter-btn active" onclick="filterEvents('all')">All Events</button>
"""

    for source in sorted(by_source.keys()):
        html += f'            <button class="filter-btn" onclick="filterEvents(\'{source}\')">{source}</button>\n'

    html += """        </div>
"""

    # Add events grouped by source
    for source in sorted(by_source.keys()):
        source_events = by_source[source]
        html += f"""
        <div class="source-section" data-source="{source}">
            <div class="source-header">
                {source} ({len(source_events)} events)
            </div>
"""

        for event in source_events:
            # Parse datetime
            try:
                dt = datetime.fromisoformat(event['start_datetime'].replace('Z', '+00:00'))
                date_str = dt.strftime('%A, %B %d, %Y')
                time_str = dt.strftime('%I:%M %p')
            except:
                date_str = event['start_datetime']
                time_str = ''

            # Get category
            category = event.get('category', 'other').lower().replace('_', '-')

            # Build location string
            location_parts = []
            if event.get('venue_name'):
                location_parts.append(event['venue_name'])
            if event.get('street_address'):
                location_parts.append(event['street_address'])
            if event.get('city'):
                location_parts.append(event['city'])
            location = ', '.join(location_parts) if location_parts else 'Location TBA'

            html += f"""
            <div class="event-card">
                <div class="event-title">
                    <a href="{event['source_url']}" target="_blank">{event['title']}</a>
                </div>
                <div class="event-meta">
                    <div class="meta-item">
                        <span class="meta-icon">📅</span>
                        <span>{date_str}</span>
                    </div>
"""

            if time_str:
                html += f"""
                    <div class="meta-item">
                        <span class="meta-icon">🕐</span>
                        <span>{time_str}</span>
                    </div>
"""

            html += f"""
                    <div class="meta-item">
                        <span class="meta-icon">📍</span>
                        <span>{location}</span>
                    </div>
"""

            if event.get('cost'):
                html += f"""
                    <div class="meta-item">
                        <span class="meta-icon">💵</span>
                        <span>{event['cost']}</span>
                    </div>
"""

            html += f"""
                    <div class="meta-item">
                        <span class="category-badge category-{category}">{category.replace('-', ' ')}</span>
                    </div>
                </div>
                <div class="event-description">
                    {event['description']}
                </div>
            </div>
"""

        html += """
        </div>
"""

    # Add footer and JavaScript
    html += f"""
        <footer>
            <p>Generated on {datetime.now().strftime('%B %d, %Y at %I:%M %p')}</p>
            <p style="margin-top: 10px;">Data scraped from {len(by_source)} sources across Cambridge and Somerville</p>
        </footer>
    </div>

    <script>
        function filterEvents(source) {{
            const sections = document.querySelectorAll('.source-section');
            const buttons = document.querySelectorAll('.filter-btn');

            // Update button states
            buttons.forEach(btn => {{
                btn.classList.remove('active');
                if ((source === 'all' && btn.textContent === 'All Events') ||
                    btn.textContent === source) {{
                    btn.classList.add('active');
                }}
            }});

            // Show/hide sections
            sections.forEach(section => {{
                if (source === 'all' || section.dataset.source === source) {{
                    section.style.display = 'block';
                }} else {{
                    section.style.display = 'none';
                }}
            }});
        }}
    </script>
</body>
</html>
"""

    # Write HTML file
    with open('events.html', 'w', encoding='utf-8') as f:
        f.write(html)

    print(f"✓ Generated events.html with {len(events)} events")
    print(f"✓ Open events.html in your browser to view")

if __name__ == '__main__':
    generate_events_html()
