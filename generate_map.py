import requests
import os
import re
import folium
import html

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

# CRITICAL: Decode HTML entities!
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
    
    # Extract fields
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
    
    if 'call' in qso:
        qsos.append(qso)

print(f"✅ Parsed {len(qsos)} QSO records")

if len(qsos) == 0:
    print("❌ ERROR: No QSOs found!")
    exit(1)

# Convert grids to coordinates
print(f"\n🌍 Converting grid squares...")
enriched_qsos = []

for qso in qsos:
    if 'grid' in qso and len(qso['grid']) >= 4:
        lat, lon = grid_to_latlon(qso['grid'])
        if lat and lon:
            qso['lat'] = lat
            qso['lon'] = lon
            enriched_qsos.append(qso)

print(f"✅ {len(enriched_qsos)} QSOs with coordinates")

if len(enriched_qsos) == 0:
    print(f"❌ ERROR: No valid grid squares!")
    print(f"   QSOs with grid field: {sum(1 for q in qsos if 'grid' in q)}")
    if qsos:
        print(f"   Sample: {qsos[0]}")
    exit(1)

# Create map
print(f"\n🗺️  Creating map...")

avg_lat = sum(q['lat'] for q in enriched_qsos) / len(enriched_qsos)
avg_lon = sum(q['lon'] for q in enriched_qsos) / len(enriched_qsos)

m = folium.Map(location=[avg_lat, avg_lon], zoom_start=4, tiles='OpenStreetMap')

home_lat, home_lon = 41.03, 29.0

folium.Marker(
    [home_lat, home_lon],
    popup="<b>TA1ZMP/LZ1MPN</b><br>Istanbul<br>Grid: KM40",
    icon=folium.Icon(color='green', icon='home')
).add_to(m)

for qso in enriched_qsos:
    popup = f"<b>{qso['call']}</b><br>Grid: {qso['grid']}<br>"
    if 'country' in qso:
        popup += f"{qso['country']}<br>"
    if 'date' in qso:
        d = qso['date']
        if len(d) == 8:
            popup += f"{d[0:4]}-{d[4:6]}-{d[6:8]}<br>"
    if 'band' in qso:
        popup += f"{qso['band']}"
    
    folium.Marker(
        [qso['lat'], qso['lon']],
        popup=popup,
        icon=folium.Icon(color='red', icon='info-sign')
    ).add_to(m)
    
    folium.PolyLine(
        [[home_lat, home_lon], [qso['lat'], qso['lon']]],
        color='blue', weight=1, opacity=0.5
    ).add_to(m)

os.makedirs('output', exist_ok=True)
m.save('output/index.html')

print(f"✅ Map saved!")
print(f"\n📊 Stats:")
print(f"   Total QSOs: {len(qsos)}")
print(f"   On map: {len(enriched_qsos)} ({len(enriched_qsos)/len(qsos)*100:.0f}%)")
print("=" * 70)
