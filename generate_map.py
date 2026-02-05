import requests
import os
import re
import folium
from datetime import datetime

# QRZ API credentials
API_KEY = os.environ['QRZ_API_KEY']
CALLSIGN = os.environ['QRZ_USERNAME']

# Download ADIF from QRZ Logbook
url = f"https://logbook.qrz.com/api?KEY={API_KEY}&ACTION=FETCH&OPTION=TYPE:ADIF"
print(f"Downloading logbook for {CALLSIGN}...")

response = requests.get(url)
if response.status_code != 200:
    print(f"ERROR: Failed to download logbook! Status: {response.status_code}")
    exit(1)

adif_data = response.text
print(f"✅ Downloaded {len(adif_data)} bytes")

# Parse ADIF
qsos = []
records = adif_data.split('<eor>')

for record in records:
    if not record.strip():
        continue
    
    qso = {}
    # Extract CALL
    call_match = re.search(r'<call:\d+>([^\<]+)', record, re.IGNORECASE)
    if call_match:
        qso['call'] = call_match.group(1).strip()
    
    # Extract LAT/LON
    lat_match = re.search(r'<lat:\d+>([^\<]+)', record, re.IGNORECASE)
    lon_match = re.search(r'<lon:\d+>([^\<]+)', record, re.IGNORECASE)
    
    if lat_match and lon_match:
        try:
            qso['lat'] = float(lat_match.group(1))
            qso['lon'] = float(lon_match.group(1))
        except:
            continue
    
    # Extract DATE
    date_match = re.search(r'<qso_date:\d+>(\d{8})', record, re.IGNORECASE)
    if date_match:
        qso['date'] = date_match.group(1)
    
    # Extract COUNTRY
    country_match = re.search(r'<country:\d+>([^\<]+)', record, re.IGNORECASE)
    if country_match:
        qso['country'] = country_match.group(1).strip()
    
    if 'call' in qso and 'lat' in qso and 'lon' in qso:
        qsos.append(qso)

print(f"✅ Parsed {len(qsos)} QSOs with coordinates")

if len(qsos) == 0:
    print("ERROR: No QSOs found with coordinates!")
    exit(1)

# Create map
avg_lat = sum(q['lat'] for q in qsos) / len(qsos)
avg_lon = sum(q['lon'] for q in qsos) / len(qsos)

m = folium.Map(
    location=[avg_lat, avg_lon],
    zoom_start=5,
    tiles='OpenStreetMap'
)

# Home location (Istanbul)
home_lat = 41.03
home_lon = 29.0

# Add markers and lines
for qso in qsos:
    folium.Marker(
        location=[qso['lat'], qso['lon']],
        popup=f"{qso['call']}<br>{qso.get('country', 'N/A')}<br>{qso.get('date', 'N/A')}",
        icon=folium.Icon(color='red', icon='info-sign')
    ).add_to(m)
    
    folium.PolyLine(
        locations=[[home_lat, home_lon], [qso['lat'], qso['lon']]],
        color='blue',
        weight=2,
        opacity=0.5
    ).add_to(m)

# Save
os.makedirs('output', exist_ok=True)
m.save('output/index.html')
print(f"✅ Map saved! {len(qsos)} QSOs visualized")
