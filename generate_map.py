#!/usr/bin/env python3
"""
TA1ZMP QSO Map Generator - Using Direct ADIF Export
Fetches logbook data from QRZ.com using export URL
"""

import os
import sys
import requests
import pandas as pd
import json
from datetime import datetime

# Configuration
QRZ_API_KEY = os.environ.get('QRZ_API_KEY')
QRZ_USERNAME = os.environ.get('QRZ_USERNAME', 'LZ1MPN')

if not QRZ_API_KEY:
    print("ERROR: QRZ_API_KEY not found!")
    sys.exit(1)

BAND_COLORS = {
    '70cm': '#FF0000', '40M': '#FF6B35', '20M': '#FFD700',
    '17M': '#32CD32', '15m': '#4169E1', '30M': '#9370DB',
    '10M': '#FF69B4', '6M': '#00CED1', '2M': '#FF4500'
}

MY_LAT = 41.06555
MY_LON = 29.02910

def fetch_logbook_adif():
    """Fetch ADIF directly using QRZ export URL"""
    # QRZ Logbook export URL format
    url = f"https://logbook.qrz.com/api"

    params = {
        'KEY': QRZ_API_KEY,
        'ACTION': 'FETCH',
        'OPTION': 'TYPE:ADIF'
    }

    print("="*70)
    print(f"Fetching from QRZ Logbook for: {QRZ_USERNAME}")
    print(f"API Key (last 4): ...{QRZ_API_KEY[-4:]}")
    print(f"URL: {url}")
    print("="*70)

    try:
        response = requests.get(url, params=params, timeout=30)
        print(f"Response Status: {response.status_code}")
        print(f"Response Length: {len(response.text)} characters")

        if response.status_code != 200:
            print(f"ERROR: HTTP {response.status_code}")
            print(f"Response: {response.text[:500]}")
            return None

        content = response.text

        # Check for errors
        if 'RESULT=FAIL' in content:
            print(f"ERROR: QRZ API returned FAIL")
            print(f"Response: {content[:500]}")
            return None

        if 'invalid api key' in content.lower():
            print(f"ERROR: Invalid API key")
            return None

        # Check if we have ADIF data
        if '<EOR>' not in content and '<eor>' not in content.upper():
            print(f"WARNING: No QSO records found in response")
            print(f"First 500 chars: {content[:500]}")
            return None

        print(f"✅ ADIF data fetched successfully!")
        return content

    except requests.exceptions.Timeout:
        print("ERROR: Request timeout")
        return None
    except Exception as e:
        print(f"ERROR: {str(e)}")
        return None

def parse_adif(adif_data):
    """Parse ADIF format to DataFrame"""
    records = []
    current_record = {}

    # Split by < to process tags
    parts = adif_data.split('<')

    for part in parts:
        if not part.strip():
            continue

        # Find closing >
        if '>' not in part:
            continue

        tag_part, rest = part.split('>', 1)

        # Parse tag (format: FIELDNAME:length or FIELDNAME:length:type)
        tag_info = tag_part.split(':')
        if not tag_info:
            continue

        field_name = tag_info[0].upper().strip()

        # Check for EOR (end of record)
        if field_name == 'EOR':
            if current_record:
                records.append(current_record.copy())
                current_record = {}
            continue

        # Get field length if present
        field_length = None
        if len(tag_info) >= 2:
            try:
                field_length = int(tag_info[1])
            except:
                pass

        # Extract value
        if field_length and field_length > 0:
            field_value = rest[:field_length]
        else:
            # Take until next tag or end
            field_value = rest.split('<')[0] if '<' in rest else rest

        field_value = field_value.strip()

        if field_value:
            current_record[field_name] = field_value

    # Add last record if exists
    if current_record:
        records.append(current_record)

    df = pd.DataFrame(records)
    print(f"\n📊 Parsed {len(df)} QSO records")

    if len(df) > 0:
        print(f"Columns found: {', '.join(list(df.columns)[:15])}")

    return df

def maidenhead_to_latlon(grid):
    """Convert Maidenhead locator to lat/lon"""
    if not grid or len(grid) < 4:
        return None, None

    try:
        grid = str(grid).upper().strip()

        # First pair (field)
        lon = (ord(grid[0]) - ord('A')) * 20 - 180
        lat = (ord(grid[1]) - ord('A')) * 10 - 90

        # Second pair (square)
        if len(grid) >= 4:
            lon += int(grid[2]) * 2
            lat += int(grid[3]) * 1

        # Third pair (subsquare)
        if len(grid) >= 6:
            lon += (ord(grid[4]) - ord('A')) * 2/24
            lat += (ord(grid[5]) - ord('A')) * 1/24

        # Fourth pair (extended square)
        if len(grid) >= 8:
            lon += int(grid[6]) * 2/240
            lat += int(grid[7]) * 1/240

        # Center of square
        if len(grid) >= 4:
            lon += 1
            lat += 0.5

        return lat, lon
    except Exception as e:
        print(f"Error converting grid {grid}: {e}")
        return None, None

def normalize_band(band):
    """Normalize band names"""
    if not band:
        return band
    band = str(band).upper()
    if band == '15M':
        return '15m'
    if band == '70CM':
        return '70cm'
    return band

def add_jitter_to_duplicates(df):
    """Add small offset to duplicate coordinates"""
    import numpy as np
    np.random.seed(42)

    df['lat_adjusted'] = df['lat']
    df['lon_adjusted'] = df['lon']

    coord_counts = df.groupby(['lat', 'lon']).size()
    duplicates = coord_counts[coord_counts > 1]

    for (lat, lon), count in duplicates.items():
        mask = (df['lat'] == lat) & (df['lon'] == lon)
        indices = df[mask].index

        for i, idx in enumerate(indices):
            if i > 0:
                angle = (2 * 3.14159 * i) / count
                radius = 0.15
                df.at[idx, 'lat_adjusted'] = lat + radius * np.sin(angle)
                df.at[idx, 'lon_adjusted'] = lon + radius * np.cos(angle)

    return df

def generate_html_map(df):
    """Generate interactive HTML map"""

    df = add_jitter_to_duplicates(df)

    qso_list = []
    for _, row in df.iterrows():
        hover = (f"<b>{row.get('CALL', 'N/A')}</b><br>"
                f"Country: {row.get('COUNTRY', 'N/A')}<br>"
                f"Grid: {row.get('GRIDSQUARE', 'N/A')}<br>"
                f"Band: {row.get('BAND', 'N/A')}<br>"
                f"Mode: {row.get('MODE', 'N/A')}<br>"
                f"Date: {row.get('date_formatted', 'N/A')}")

        qso_list.append({
            'call': row.get('CALL', ''),
            'country': row.get('COUNTRY', ''),
            'band': row.get('BAND', ''),
            'mode': row.get('MODE', ''),
            'date': row.get('date_formatted', ''),
            'lat': float(row['lat_adjusted']),
            'lon': float(row['lon_adjusted']),
            'hover': hover
        })

    bands = ['All'] + sorted(df['BAND'].unique().tolist())
    modes = ['All'] + sorted(df['MODE'].unique().tolist())
    dates = ['All'] + sorted(df['date_formatted'].unique().tolist())

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>TA1ZMP / LZ1MPN QSO Interactive Map</title>
    <script src="https://cdn.plot.ly/plotly-2.26.0.min.js"></script>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background: #f0f2f5; }}
        .header {{ background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; padding: 20px; text-align: center; box-shadow: 0 4px 6px rgba(0,0,0,0.1); }}
        .header h1 {{ margin: 0; font-size: 22px; font-weight: 600; }}
        .header p {{ margin: 5px 0 0 0; font-size: 14px; opacity: 0.9; }}
        .controls {{ background: white; padding: 20px; display: flex; justify-content: center; align-items: flex-end; gap: 25px; box-shadow: 0 2px 8px rgba(0,0,0,0.08); flex-wrap: wrap; }}
        .filter-group {{ display: flex; flex-direction: column; gap: 8px; }}
        .filter-group label {{ font-weight: 600; color: #333; font-size: 13px; text-transform: uppercase; letter-spacing: 0.5px; }}
        .filter-group select {{ padding: 10px 16px; border: 2px solid #667eea; border-radius: 8px; font-size: 14px; cursor: pointer; background: white; min-width: 180px; transition: all 0.3s ease; font-weight: 500; }}
        .filter-group select:hover {{ border-color: #764ba2; box-shadow: 0 0 0 3px rgba(102, 126, 234, 0.1); }}
        .reset-btn {{ padding: 10px 24px; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; border: none; border-radius: 8px; cursor: pointer; font-weight: 600; font-size: 14px; transition: all 0.3s ease; }}
        .stats {{ background: linear-gradient(to right, #f8f9fa, #ffffff); padding: 12px 20px; text-align: center; font-size: 14px; color: #495057; border-top: 1px solid #dee2e6; font-weight: 500; }}
        .stats b {{ color: #667eea; font-size: 16px; }}
        #map {{ width: 100%; height: calc(100vh - 250px); }}
    </style>
</head>
<body>
    <div class="header">
        <h1>📡 TA1ZMP / LZ1MPN QSO Interactive Map</h1>
        <p>✅ Auto-updated from QRZ.com | Last update: {datetime.now().strftime("%Y-%m-%d %H:%M UTC")}</p>
    </div>

    <div class="controls">
        <div class="filter-group">
            <label>📻 BAND</label>
            <select id="bandFilter" onchange="updateMap()">
                {''.join(f'<option value="{b}">{b}</option>' for b in bands)}
            </select>
        </div>

        <div class="filter-group">
            <label>🎛️ MODE</label>
            <select id="modeFilter" onchange="updateMap()">
                {''.join(f'<option value="{m}">{m}</option>' for m in modes)}
            </select>
        </div>

        <div class="filter-group">
            <label>📅 DATE</label>
            <select id="dateFilter" onchange="updateMap()">
                {''.join(f'<option value="{d}">{d}</option>' for d in dates)}
            </select>
        </div>

        <button class="reset-btn" onclick="resetFilters()">🔄 Reset All</button>
    </div>

    <div class="stats" id="stats"></div>
    <div id="map"></div>

    <script>
        const qsoData = {json.dumps(qso_list)};
        const myLat = {MY_LAT};
        const myLon = {MY_LON};
        const bandColors = {json.dumps(BAND_COLORS)};

        function updateMap() {{
            const selectedBand = document.getElementById('bandFilter').value;
            const selectedMode = document.getElementById('modeFilter').value;
            const selectedDate = document.getElementById('dateFilter').value;

            let filteredData = qsoData.filter(d => {{
                const bandMatch = selectedBand === 'All' || d.band === selectedBand;
                const modeMatch = selectedMode === 'All' || d.mode === selectedMode;
                const dateMatch = selectedDate === 'All' || d.date === selectedDate;
                return bandMatch && modeMatch && dateMatch;
            }});

            const traces = [];

            filteredData.forEach(d => {{
                traces.push({{
                    type: 'scattergeo',
                    lon: [myLon, d.lon],
                    lat: [myLat, d.lat],
                    mode: 'lines',
                    line: {{ width: 1.5, color: bandColors[d.band] || '#808080' }},
                    showlegend: false,
                    hoverinfo: 'skip'
                }});
            }});

            const groupedByBand = {{}};
            filteredData.forEach(d => {{
                if (!groupedByBand[d.band]) groupedByBand[d.band] = [];
                groupedByBand[d.band].push(d);
            }});

            Object.keys(groupedByBand).sort().forEach(band => {{
                const bandData = groupedByBand[band];
                traces.push({{
                    type: 'scattergeo',
                    lon: bandData.map(d => d.lon),
                    lat: bandData.map(d => d.lat),
                    mode: 'markers',
                    marker: {{
                        size: 9,
                        color: bandColors[band] || '#808080',
                        line: {{width: 1.5, color: 'white'}},
                        opacity: 0.9
                    }},
                    text: bandData.map(d => d.hover),
                    name: `${{band}} (${{bandData.length}})`,
                    hovertemplate: '%{{text}}<extra></extra>'
                }});
            }});

            traces.push({{
                type: 'scattergeo',
                lon: [myLon],
                lat: [myLat],
                mode: 'markers',
                marker: {{
                    size: 18,
                    color: 'red',
                    symbol: 'star',
                    line: {{width: 2, color: 'white'}}
                }},
                text: '<b>TA1ZMP / LZ1MPN</b><br>Beşiktaş, İstanbul',
                name: 'TA1ZMP (Home)',
                hovertemplate: '%{{text}}<extra></extra>'
            }});

            const layout = {{
                geo: {{
                    projection: {{type: 'natural earth'}},
                    showcountries: true,
                    countrycolor: 'lightgray',
                    showcoastlines: true,
                    coastlinecolor: 'darkgray',
                    showland: true,
                    landcolor: 'rgb(243, 243, 238)',
                    showocean: true,
                    oceancolor: 'rgb(204, 229, 255)',
                    showlakes: true,
                    lakecolor: 'rgb(204, 229, 255)',
                    resolution: 50,
                    center: {{lat: 43, lon: 15}},
                    lataxis: {{range: [25, 62]}},
                    lonaxis: {{range: [-20, 50]}}
                }},
                showlegend: true,
                legend: {{
                    x: 0.01,
                    y: 0.98,
                    bgcolor: 'rgba(255, 255, 255, 0.95)',
                    bordercolor: 'gray',
                    borderwidth: 2,
                    font: {{size: 11}}
                }},
                margin: {{l: 0, r: 0, t: 0, b: 0}},
                height: window.innerHeight - 250
            }};

            Plotly.newPlot('map', traces, layout, {{responsive: true}});

            const uniqueCountries = [...new Set(filteredData.map(d => d.country))].length;
            const uniqueBands = [...new Set(filteredData.map(d => d.band))].length;
            const uniqueModes = [...new Set(filteredData.map(d => d.mode))].length;
            document.getElementById('stats').innerHTML = 
                `📊 Showing: <b>${{filteredData.length}} QSO</b> of <b>{len(df)}</b> total | ` +
                `🌍 Countries: <b>${{uniqueCountries}}</b> | ` +
                `📻 Bands: <b>${{uniqueBands}}</b> | ` +
                `🎛️ Modes: <b>${{uniqueModes}}</b>`;
        }}

        function resetFilters() {{
            document.getElementById('bandFilter').value = 'All';
            document.getElementById('modeFilter').value = 'All';
            document.getElementById('dateFilter').value = 'All';
            updateMap();
        }}

        updateMap();
    </script>
</body>
</html>"""

    return html

def main():
    print("\n" + "="*70)
    print("TA1ZMP / LZ1MPN QSO Map Generator")
    print("="*70)

    # Fetch ADIF data
    print("\n📥 Step 1: Fetching logbook data...")
    adif_data = fetch_logbook_adif()

    if not adif_data:
        print("\n❌ FAILED: Could not fetch logbook data")
        sys.exit(1)

    # Parse ADIF
    print("\n📊 Step 2: Parsing ADIF data...")
    df = parse_adif(adif_data)

    if df.empty:
        print("\n⚠️ No QSO records found")
        sys.exit(0)

    print(f"✅ Found {len(df)} QSO records")

    # Process data
    print("\n🔄 Step 3: Processing QSO data...")

    if 'BAND' in df.columns:
        df['BAND'] = df['BAND'].apply(normalize_band)

    if 'GRIDSQUARE' in df.columns:
        df['lat'], df['lon'] = zip(*df['GRIDSQUARE'].apply(maidenhead_to_latlon))
        df = df.dropna(subset=['lat', 'lon'])
        print(f"✅ {len(df)} records with valid coordinates")
    else:
        print("⚠️ No GRIDSQUARE field found")
        df['lat'] = None
        df['lon'] = None

    if 'QSO_DATE' in df.columns:
        df['date_formatted'] = pd.to_datetime(df['QSO_DATE'], format='%Y%m%d', errors='coerce').dt.strftime('%d.%m')
    else:
        df['date_formatted'] = 'N/A'

    if len(df) == 0:
        print("\n❌ No valid QSO records after processing")
        sys.exit(1)

    # Generate map
    print("\n🗺️ Step 4: Generating HTML map...")
    html_content = generate_html_map(df)

    # Save output
    os.makedirs('output', exist_ok=True)
    output_file = 'output/index.html'

    with open(output_file, 'w', encoding='utf-8') as f:
        f.write(html_content)

    print(f"✅ Map saved: {output_file}")

    # Statistics
    print("\n" + "="*70)
    print("📊 STATISTICS:")
    print("="*70)
    print(f"Total QSO: {len(df)}")
    if 'COUNTRY' in df.columns:
        print(f"Countries: {df['COUNTRY'].nunique()}")
    if 'BAND' in df.columns:
        print(f"Bands: {', '.join(sorted(df['BAND'].unique()))}")
    if 'MODE' in df.columns:
        print(f"Modes: {', '.join(sorted(df['MODE'].unique()))}")
    print("="*70)
    print("\n✅ SUCCESS! Map generated successfully!")

if __name__ == '__main__':
    main()
