import requests
import os
import re
import folium

# QRZ API credentials
API_KEY = os.environ['QRZ_API_KEY']
CALLSIGN = os.environ['QRZ_USERNAME']

def grid_to_latlon(grid):
    """Convert Maidenhead grid square to lat/lon (center of grid)"""
    if not grid or len(grid) < 4:
        return None, None
    
    grid = grid.upper()
    
    try:
        # Field (first 2 chars) - 20° longitude, 10° latitude
        lon = (ord(grid[0]) - ord('A')) * 20 - 180
        lat = (ord(grid[1]) - ord('A')) * 10 - 90
        
        # Square (next 2 chars) - 2° longitude, 1° latitude  
        lon += int(grid[2]) * 2
        lat += int(grid[3]) * 1
        
        # Center of square
        lon += 1  # +1° to center
        lat += 0.5  # +0.5° to center
        
        # If 6-character grid (subsquare)
        if len(grid) >= 6:
            lon += (ord(grid[4]) - ord('A')) * (2/24) + (1/24)
            lat += (ord(grid[5]) - ord('A')) * (1/24) + (1/48)
        
        return lat, lon
    except:
        return None, None

print("=" * 70)
print(f"TA1ZMP / LZ1MPN QSO Map Generator")
print("=" * 70)

# Step 1: Download ADIF from QRZ Logbook
url = f"https://logbook.qrz.com/api?KEY={API_KEY}&ACTION=FETCH&OPTION=TYPE:ADIF"
print(f"\n📥 Downloading logbook for {CALLSIGN}...")

response = requests.get(url)
if response.status_code != 200:
    print(f"❌ ERROR: Failed to download! Status: {response.status_code}")
    exit(1)

adif_data = response.text
print(f"✅ Downloaded {len(adif_data)} bytes")

# Step 2: Parse ADIF
print(f"\n🔍 Parsing ADIF records...")
qsos = []

records = re.split(r'<eor>|<EOR>', adif_data, flags=re.IGNORECASE)

for record in records:
    if not record.strip() or len(record) < 20:
        continue
    
    qso = {}
    
    # Extract CALL
    call_match = re.search(r'<call:\d+>([^<\n]+)', record, re.IGNORECASE)
    if call_match:
        qso['call'] = call_match.group(1).strip().upper()
    
    # Extract GRIDSQUARE
    grid_match = re.search(r'<gridsquare:\d+>([^<\n]+)', record, re.IGNORECASE)
    if grid_match:
        qso['grid'] = grid_match.group(1).strip().upper()
    
    # Extract QSO_DATE
    date_match = re.search(r'<qso_date:\d+>(\d+)', record, re.IGNORECASE)
    if date_match:
        qso['date'] = date_match.group(1)[:8]
    
    # Extract BAND
    band_match = re.search(r'<band:\d+>([^<\n]+)', record, re.IGNORECASE)
    if band_match:
        qso['band'] = band_match.group(1).strip()
    
    # Extract COUNTRY
    country_match = re.search(r'<country:\d+>([^<\n]+)', record, re.IGNORECASE)
    if country_match:
        qso['country'] = country_match.group(1).strip()
    
    if 'call' in qso:
        qsos.append(qso)

print(f"✅ Parsed {len(qsos)} QSO records")

if len(qsos) == 0:
    print("❌ ERROR: No QSOs found!")
    exit(1)

# Step 3: Convert grid squares to coordinates
print(f"\n🌍 Converting grid squares to coordinates...")

enriched_qsos = []
for qso in qsos:
    if 'grid' in qso:
        lat, lon = grid_to_latlon(qso['grid'])
        if lat and lon:
            qso['lat'] = lat
            qso['lon'] = lon
            enriched_qsos.append(qso)

print(f"✅ {len(enriched_qsos)} QSOs have valid grid squares")

if len(enriched_qsos) == 0:
    print("❌ ERROR: No QSOs with grid squares!")
    exit(1)

# Step 4: Create map
print(f"\n🗺️  Generating interactive map...")

avg_lat = sum(q['lat'] for q in enriched_qsos) / len(enriched_qsos)
avg_lon = sum(q['lon'] for q in enriched_qsos) / len(enriched_qsos)

m = folium.Map(
    location=[avg_lat, avg_lon],
    zoom_start=4,
    tiles='OpenStreetMap'
)

# Home location (Istanbul - KM40)
home_lat = 41.03
home_lon = 29.0

# Add home marker
folium.Marker(
    location=[home_lat, home_lon],
    popup="<b>TA1ZMP / LZ1MPN</b><br>Home QTH<br>Istanbul, Turkey<br>Grid: KM40",
    icon=folium.Icon(color='green', icon='home')
).add_to(m)

# Add QSO markers and lines
for qso in enriched_qsos:
    popup_text = f"<b>{qso['call']}</b><br>"
    popup_text += f"Grid: {qso['grid']}<br>"
    if 'country' in qso:
        popup_text += f"{qso['country']}<br>"
    if 'date' in qso:
        date_str = qso['date']
        if len(date_str) == 8:
            popup_text += f"{date_str[0:4]}-{date_str[4:6]}-{date_str[6:8]}<br>"
    if 'band' in qso:
        popup_text += f"{qso['band']}"
    
    folium.Marker(
        location=[qso['lat'], qso['lon']],
        popup=popup_text,
        icon=folium.Icon(color='red', icon='info-sign')
    ).add_to(m)
    
    folium.PolyLine(
        locations=[[home_lat, home_lon], [qso['lat'], qso['lon']]],
        color='blue',
        weight=1,
        opacity=0.5
    ).add_to(m)

# Save
os.makedirs('output', exist_ok=True)
m.save('output/index.html')

print(f"✅ Map saved to output/index.html")
print(f"\n📊 Statistics:")
print(f"   Total QSOs: {len(qsos)}")
print(f"   With grid squares: {len(enriched_qsos)}")
print(f"   Coverage: {len(enriched_qsos)/len(qsos)*100:.1f}%")
print("=" * 70)
