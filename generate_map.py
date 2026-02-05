import requests
import os
import re
import folium

# QRZ API credentials
API_KEY = os.environ['QRZ_API_KEY']
CALLSIGN = os.environ['QRZ_USERNAME']

def grid_to_latlon(grid):
    """Convert Maidenhead grid square to lat/lon"""
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

adif_data = response.text
print(f"✅ Downloaded {len(adif_data)} bytes")

# Debug: Show first 1000 chars
print(f"\n🔍 First 1000 chars of ADIF:")
print(adif_data[:1000])
print("...")

# Parse ADIF - flexible approach
print(f"\n📋 Parsing ADIF...")
qsos = []

# Try different split methods
for separator in ['<eor>', '<EOR>', '<eor', '<EOR']:
    parts = adif_data.lower().split(separator.lower())
    if len(parts) > 1:
        print(f"   Found {len(parts)-1} records using separator '{separator}'")
        
        for i, record in enumerate(parts):
            if i == 0 or not record.strip():
                continue
            
            qso = {}
            
            # Extract fields with flexible regex
            for field, pattern in [
                ('call', r'<call:(\d+)>([^<\n]+)'),
                ('gridsquare', r'<gridsquare:(\d+)>([^<\n]+)'),
                ('qso_date', r'<qso_date:(\d+)>(\d+)'),
                ('band', r'<band:(\d+)>([^<\n]+)'),
                ('country', r'<country:(\d+)>([^<\n]+)'),
            ]:
                match = re.search(pattern, record, re.IGNORECASE)
                if match:
                    length = int(match.group(1))
                    value = match.group(2).strip()[:length]
                    if field == 'gridsquare':
                        qso['grid'] = value.upper()
                    elif field == 'qso_date':
                        qso['date'] = value[:8]
                    else:
                        qso[field] = value
            
            if 'call' in qso:
                qsos.append(qso)
                if len(qsos) <= 3:
                    print(f"   QSO #{len(qsos)}: {qso}")
        
        if len(qsos) > 0:
            break

print(f"\n✅ Parsed {len(qsos)} QSO records")

if len(qsos) == 0:
    print("❌ ERROR: No QSOs found! Check ADIF format above.")
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
    print("❌ ERROR: No valid grid squares!")
    print(f"   Total QSOs: {len(qsos)}")
    print(f"   With 'grid' field: {sum(1 for q in qsos if 'grid' in q)}")
    if qsos:
        print(f"   Sample QSO: {qsos[0]}")
    exit(1)

# Create map
print(f"\n🗺️  Creating map...")

avg_lat = sum(q['lat'] for q in enriched_qsos) / len(enriched_qsos)
avg_lon = sum(q['lon'] for q in enriched_qsos) / len(enriched_qsos)

m = folium.Map(location=[avg_lat, avg_lon], zoom_start=4, tiles='OpenStreetMap')

home_lat, home_lon = 41.03, 29.0

folium.Marker(
    [home_lat, home_lon],
    popup="<b>TA1ZMP/LZ1MPN</b><br>Istanbul",
    icon=folium.Icon(color='green', icon='home')
).add_to(m)

for qso in enriched_qsos:
    popup = f"<b>{qso['call']}</b><br>{qso.get('grid', '')}<br>"
    popup += f"{qso.get('country', '')}<br>{qso.get('date', '')}"
    
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
print(f"\n📊 Stats: {len(qsos)} QSOs, {len(enriched_qsos)} on map ({len(enriched_qsos)/len(qsos)*100:.0f}%)")
print("=" * 70)
