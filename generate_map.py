import requests
import os
import re
import folium
from folium import plugins
import html
from collections import defaultdict
import xml.etree.ElementTree as ET
import time

# QRZ API credentials
API_KEY = os.environ['QRZ_API_KEY']
CALLSIGN = os.environ['QRZ_USERNAME']

def grid_to_latlon(grid):
    """
    Convert Maidenhead grid square to lat/lon (center coordinates)
    Based on official Maidenhead standard
    """
    if not grid or len(grid) < 4:
        return None, None
    
    grid = grid.upper().strip()
    
    try:
        # Field (characters 0,1) - 20° lon × 10° lat
        lon = (ord(grid[0]) - ord('A')) * 20 - 180
        lat = (ord(grid[1]) - ord('A')) * 10 - 90
        
        # Square (characters 2,3) - 2° lon × 1° lat
        lon += int(grid[2]) * 2
        lat += int(grid[3]) * 1
        
        if len(grid) >= 6:
            # Subsquare (characters 4,5) - 5' lon × 2.5' lat (minutes)
            lon += (ord(grid[4]) - ord('A')) * (5.0 / 60.0)
            lat += (ord(grid[5]) - ord('A')) * (2.5 / 60.0)
            
            # Center of subsquare
            lon += (5.0 / 60.0) / 2.0  # +2.5 minutes
            lat += (2.5 / 60.0) / 2.0  # +1.25 minutes
        else:
            # Center of 4-char square
            lon += 1.0  # +1° (center of 2° square)
            lat += 0.5  # +0.5° (center of 1° square)
        
        return lat, lon
    except Exception as e:
        return None, None

def approximate_6char_grid(grid_4char):
    """
    Approximate 6-char grid by adding center subsquare
    """
    if len(grid_4char) >= 6:
        return grid_4char
    
    if len(grid_4char) == 4:
        # Add "LL" for approximate center (L = 11, middle of 0-23 range)
        return grid_4char + "LL"
    
    return grid_4char

def enrich_grid_from_qrz(callsign, current_grid, session_key):
    """
    Lookup full grid square from QRZ.com
    Returns: (enriched_grid, success_flag, source)
    """
    if len(current_grid) >= 6:
        return current_grid, False, 'original'  # Already have full grid
    
    try:
        url = f"https://xmldata.qrz.com/xml/current/?s={session_key}&callsign={callsign}"
        response = requests.get(url, timeout=5)
        root = ET.fromstring(response.text)
        
        # Check for grid in profile
        grid_elem = root.find('.//grid')
        if grid_elem is not None and grid_elem.text:
            qrz_grid = grid_elem.text.strip().upper()
            
            # Validate: must be 4 or 6 chars
            if len(qrz_grid) >= 4:
                # Check if it's in the same area (first 4 chars match or close)
                if len(current_grid) == 4:
                    # Accept if starts with same 4 chars
                    if qrz_grid[:4] == current_grid[:4]:
                        return qrz_grid[:6] if len(qrz_grid) >= 6 else qrz_grid, True, 'qrz'
                    # Or if completely different, use QRZ version (more reliable)
                    else:
                        return qrz_grid[:6] if len(qrz_grid) >= 6 else qrz_grid, True, 'qrz_override'
        
        return current_grid, False, 'original'
    except Exception as e:
        return current_grid, False, 'error'

print("=" * 70)
print("TA1ZMP QSO Map Generator")
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

# Enrich grid squares
print(f"\n🔍 Enriching 4-char grid squares...")

# Login to QRZ XML API for grid lookups
qrz_session = None
try:
    login_url = f"https://xmldata.qrz.com/xml/current/?username={CALLSIGN}&password={API_KEY}"
    login_response = requests.get(login_url, timeout=10)
    root = ET.fromstring(login_response.text)
    session_elem = root.find('.//Key')
    if session_elem is not None:
        qrz_session = session_elem.text
        print(f"✅ QRZ XML session established for grid enrichment")
except Exception as e:
    print(f"⚠️  QRZ XML login failed, will use approximations only")

# Enrich grids
enriched_count = 0
approximated_count = 0
errors_count = 0

for i, qso in enumerate(qsos):
    if 'grid' not in qso:
        continue
    
    original_grid = qso['grid']
    
    if len(original_grid) >= 6:
        qso['grid_source'] = 'original_6char'
        continue  # Already good
    
    if len(original_grid) == 4:
        # Try QRZ lookup first
        if qrz_session:
            enriched_grid, success, source = enrich_grid_from_qrz(
                qso['call'], 
                original_grid, 
                qrz_session
            )
            
            if success:
                qso['grid'] = enriched_grid
                qso['grid_source'] = source
                qso['grid_original'] = original_grid
                enriched_count += 1
                if enriched_count <= 10:  # Show first 10
                    print(f"   ✓ {qso['call']}: {original_grid} → {enriched_grid} ({source})")
                time.sleep(0.15)  # Rate limiting
                continue
            else:
                errors_count += 1
        
        # Fallback: approximate center
        qso['grid'] = approximate_6char_grid(original_grid)
        qso['grid_source'] = 'approximated'
        qso['grid_original'] = original_grid
        approximated_count += 1

print(f"✅ Grid enrichment complete:")
print(f"   From QRZ: {enriched_count}")
print(f"   Approximated: {approximated_count}")
print(f"   Lookup errors: {errors_count}")

# Convert grids to coordinates and group by location
print(f"\n🌍 Converting grid squares to coordinates...")
location_groups = defaultdict(list)

for qso in qsos:
    if 'grid' in qso and len(qso['grid']) >= 4:
        lat, lon = grid_to_latlon(qso['grid'])
        if lat and lon:
            qso['lat'] = lat
            qso['lon'] = lon
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
print(f"\n🗺️  Creating interactive map...")

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
home_lat, home_lon = 41.0653, 29.0291

folium.Marker(
    [home_lat, home_lon],
    popup="<b style='font-size:16px'>🏠 TA1ZMP</b><br>Istanbul, Turkey<br>Grid: KM41mb",
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
            
        # Show grid source if enriched
        if 'grid_original' in qso:
            popup_html += f" <small>[{qso['grid_original']}→{qso['grid'][:6]}]</small>"
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
     top: 10px; right: 10px; width: 220px; 
     background-color: white; z-index:9999; 
     padding: 10px; border: 2px solid grey; border-radius: 5px;
     font-family: Arial; font-size: 12px;">
<h4 style="margin-top:0">📊 QSO Statistics</h4>
<b>Total QSOs:</b> {len(qsos)}<br>
<b>Unique Grids:</b> {len(location_groups)}<br>
<b>Countries:</b> {len(countries)}<br>
<b>Bands:</b> {len(bands)}<br>
<b>Modes:</b> {len(modes)}<br>
<hr style="margin: 5px 0">
<small>Grid enriched: {enriched_count} from QRZ<br>
Approximated: {approximated_count}</small>
</div>
"""

m.get_root().html.add_child(folium.Element(stats_html))

# Save
os.makedirs('output', exist_ok=True)
m.save('output/index.html')

print(f"✅ Map saved to output/index.html")
print(f"\n📊 Final Statistics:")
print(f"   Total QSOs: {len(qsos)}")
print(f"   Unique locations: {len(location_groups)}")
print(f"   Countries: {len(countries)}")
print(f"   Bands: {', '.join(sorted(bands))}")
print(f"   Modes: {', '.join(sorted(modes))}")
print("=" * 70)
