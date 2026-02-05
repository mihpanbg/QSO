import requests
import os
import re
import folium
from folium import plugins
import html
from collections import defaultdict

# QRZ API credentials
API_KEY = os.environ['QRZ_API_KEY']
CALLSIGN = os.environ['QRZ_USERNAME']

def grid_to_latlon(grid):
    """Convert Maidenhead grid to lat/lon"""
    if not grid or len(grid) < 4:
        return None, None
    
    grid = grid.upper().strip()
    
    try:
        lon = (ord(grid[0]) - ord('A')) * 20 - 180
        lat = (ord(grid[1]) - ord('A')) * 10 - 90
        lon += int(grid[2]) * 2
        lat += int(grid[3]) * 1
        lon += 1
        lat += 0.5
        
        if len(grid) >= 6 and grid[4].isalpha() and grid[5].isalpha():
            lon += (ord(grid[4]) - ord('A')) * (2/24) + (1/24)
            lat += (ord(grid[5]) - ord('A')) * (1/24) + (1/48)
        
        return lat, lon
    except:
        return None, None

print("=" * 70)
print("TA1ZMP / LZ1MPN QSO Map Generator")
print("=" * 70)

# Download ADIF
url = f"https://logbook.qrz.com/api?KEY={API_KEY}&ACTION=FETCH&OPTION=TYPE:ADIF"
print(f"\n📥 Downloading logbook...")

response = requests.get(url)
if response.status_code != 200:
    print(f"❌ ERROR: HTTP {response.status_code}")
    exit(1)

# Decode HTML entities
adif_data = html.unescape(response.text)
print(f"✅ Downloaded {len(adif_data)} bytes")

# Parse ADIF
print(f"\n📋 Parsing ADIF...")
qsos = []

records = re.split(r'<eor>|<EOR>', adif_data, flags=re.IGNORECASE)

for record in records:
    if not record.strip() or len(record) < 20:
        continue
    
    qso = {}
    
    call_match = re.search(r'<call:(\d+)>([^<\n]+)', record, re.IGNORECASE)
    if call_match:
        length = int(call_match.group(1))
        qso['call'] = call_match.group(2).strip()[:length].upper()
    
    grid_match = re.search(r'<gridsquare:(\d+)>([^<\n]+)', record, re.IGNORECASE)
    if grid_match:
        length = int(grid_match.group(1))
        qso['grid'] = grid_match.group(2).strip()[:length].upper()
    
    date_match = re.search(r'<qso_date:(\d+)>(\d+)', record, re.IGNORECASE)
    if date_match:
        qso['date'] = date_match.group(2)[:8]
    
    band_match = re.search(r'<band:(\d+)>([^<\n]+)', record, re.IGNORECASE)
    if band_match:
        length = int(band_match.group(1))
        qso['band'] = band_match.group(2).strip()[:length]
    
    country_match = re.search(r'<country:(\d+)>([^<\n]+)', record, re.IGNORECASE)
    if country_match:
        length = int(country_match.group(1))
        qso['country'] = country_match.group(2).strip()[:length]
    
    mode_match = re.search(r'<mode:(\d+)>([^<\n]+)', record, re.IGNORECASE)
    if mode_match:
        length = int(mode_match.group(1))
        qso['mode'] = mode_match.group(2).strip()[:length]
    
    if 'call' in qso:
        qsos.append(qso)

print(f"✅ Parsed {len(qsos)} QSO records")

if len(qsos) == 0:
    print("❌ ERROR: No QSOs found!")
    exit(1)

# Convert grids to coordinates and group by location
print(f"\n🌍 Converting grid squares...")
location_groups = defaultdict(list)

for qso in qsos:
    if 'grid' in qso and len(qso['grid']) >= 4:
        lat, lon = grid_to_latlon(qso['grid'])
        if lat and lon:
            qso['lat'] = lat
            qso['lon'] = lon
            # Group by grid square to handle multiple QSOs from same location
            location_groups[qso['grid']].append(qso)

total_with_coords = sum(len(qsos) for qsos in location_groups.values())
print(f"✅ {total_with_coords} QSOs with coordinates in {len(location_groups)} unique locations")

if len(location_groups) == 0:
    print("❌ ERROR: No valid grid squares!")
    exit(1)

# Calculate statistics
countries = set(q.get('country', 'Unknown') for qsos in location_groups.values() for q in qsos)
bands = set(q.get('band', 'Unknown') for qsos in location_groups.values() for q in qsos)
modes = set(q.get('mode', 'Unknown') for qsos in location_groups.values() for q in qsos)

# Create map
print(f"\n🗺️  Creating map...")

# Calculate center
all_lats = [q['lat'] for qsos in location_groups.values() for q in qsos]
all_lons = [q['lon'] for qsos in location_groups.values() for q in qsos]
avg_lat = sum(all_lats) / len(all_lats)
avg_lon = sum(all_lons) / len(all_lons)

m = folium.Map(
    location=[avg_lat, avg_lon], 
    zoom_start=4, 
    tiles='OpenStreetMap'
)

# Add home marker
home_lat, home_lon = 41.03, 29.0

folium.Marker(
    [home_lat, home_lon],
    popup="<b style='font-size:16px'>🏠 TA1ZMP / LZ1MPN</b><br>Istanbul, Turkey<br>Grid: KM40",
    tooltip="Home QTH",
    icon=folium.Icon(color='green', icon='home', prefix='fa')
).add_to(m)

# Add markers for each unique location
for grid, qsos_at_location in location_groups.items():
    lat = qsos_at_location[0]['lat']
    lon = qsos_at_location[0]['lon']
    
    # Build popup with ALL QSOs at this location
    popup_html = f"<div style='width:250px'>"
    popup_html += f"<b style='font-size:14px'>📍 Grid: {grid}</b><br>"
    popup_html += f"<b>{len(qsos_at_location)} QSO(s) from this location:</b><br><br>"
    
    # Show up to 10 QSOs
    for i, qso in enumerate(qsos_at_location[:10]):
        popup_html += f"<b>{qso['call']}</b>"
        if 'country' in qso:
            popup_html += f" ({qso['country']})"
        popup_html += "<br>"
        if 'date' in qso:
            d = qso['date']
            if len(d) == 8:
                popup_html += f"📅 {d[0:4]}-{d[4:6]}-{d[6:8]} "
        if 'band' in qso:
            popup_html += f"📻 {qso['band']} "
        if 'mode' in qso:
            popup_html += f"🔊 {qso['mode']}"
        popup_html += "<br>"
    
    if len(qsos_at_location) > 10:
        popup_html += f"<i>... and {len(qsos_at_location) - 10} more</i><br>"
    
    popup_html += "</div>"
    
    # Marker color based on number of QSOs
    if len(qsos_at_location) >= 5:
        color = 'red'
        icon = 'star'
    elif len(qsos_at_location) >= 2:
        color = 'orange'
        icon = 'certificate'
    else:
        color = 'blue'
        icon = 'info-sign'
    
    folium.Marker(
        [lat, lon],
        popup=folium.Popup(popup_html, max_width=300),
        tooltip=f"{len(qsos_at_location)} QSO(s) from {grid}",
        icon=folium.Icon(color=color, icon=icon)
    ).add_to(m)
    
    # Draw line to home
    folium.PolyLine(
        [[home_lat, home_lon], [lat, lon]],
        color='blue', 
        weight=1, 
        opacity=0.4
    ).add_to(m)

# Add layer control
folium.LayerControl().add_to(m)

# Add fullscreen button
plugins.Fullscreen().add_to(m)

# Add statistics box
stats_html = f"""
<div style="position: fixed; 
     top: 10px; right: 10px; width: 200px; 
     background-color: white; z-index:9999; 
     padding: 10px; border: 2px solid grey; border-radius: 5px;
     font-family: Arial;">
<h4 style="margin-top:0">📊 Statistics</h4>
<b>Total QSOs:</b> {len(qsos)}<br>
<b>Unique Grids:</b> {len(location_groups)}<br>
<b>Countries:</b> {len(countries)}<br>
<b>Bands:</b> {len(bands)}<br>
<b>Modes:</b> {len(modes)}<br>
<hr style="margin: 5px 0">
<small>Last updated: {qsos[0].get('date', 'N/A') if qsos else 'N/A'}</small>
</div>
"""

m.get_root().html.add_child(folium.Element(stats_html))

# Save
os.makedirs('output', exist_ok=True)
m.save('output/index.html')

print(f"✅ Map saved!")
print(f"\n📊 Final Statistics:")
print(f"   Total QSOs: {len(qsos)}")
print(f"   Unique locations: {len(location_groups)}")
print(f"   Countries: {len(countries)}")
print(f"   Bands: {', '.join(sorted(bands))}")
print(f"   Modes: {', '.join(sorted(modes))}")
print("=" * 70)
