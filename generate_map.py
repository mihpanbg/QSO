import requests
import os
import re
import folium
import time
import xml.etree.ElementTree as ET

# QRZ API credentials
API_KEY = os.environ['QRZ_API_KEY']
CALLSIGN = os.environ['QRZ_USERNAME']

print("=" * 70)
print(f"TA1ZMP / LZ1MPN QSO Map Generator")
print("=" * 70)

# Step 1: Download ADIF from QRZ Logbook
url = f"https://logbook.qrz.com/api?KEY={API_KEY}&ACTION=FETCH&OPTION=TYPE:ADIF"
print(f"\n📥 Step 1: Downloading logbook for {CALLSIGN}...")

response = requests.get(url)
if response.status_code != 200:
    print(f"❌ ERROR: Failed to download logbook! Status: {response.status_code}")
    exit(1)

adif_data = response.text
print(f"✅ Downloaded {len(adif_data)} bytes")

# Step 2: Parse ADIF
print(f"\n🔍 Step 2: Parsing ADIF records...")
qsos = []
records = adif_data.split('<eor>')

for record in records:
    if not record.strip():
        continue
    
    qso = {}
    
    # Extract CALL
    call_match = re.search(r'<call:\d+>([^\<]+)', record, re.IGNORECASE)
    if call_match:
        qso['call'] = call_match.group(1).strip().upper()
    
    # Extract DATE
    date_match = re.search(r'<qso_date:\d+>(\d{8})', record, re.IGNORECASE)
    if date_match:
        qso['date'] = date_match.group(1)
    
    if 'call' in qso:
        qsos.append(qso)

print(f"✅ Parsed {len(qsos)} QSO records")

if len(qsos) == 0:
    print("❌ ERROR: No QSOs found!")
    exit(1)

# Step 3: Get coordinates from QRZ.com
print(f"\n🌍 Step 3: Enriching with coordinates from QRZ.com...")

# Login to QRZ XML API
session_key = None
login_url = f"https://xmldata.qrz.com/xml/current/?username={CALLSIGN}&password={API_KEY}"
login_response = requests.get(login_url)
root = ET.fromstring(login_response.text)
session_elem = root.find('.//Key')
if session_elem is not None:
    session_key = session_elem.text
    print(f"✅ QRZ XML API session established")
else:
    print("❌ ERROR: Failed to login to QRZ XML API")
    exit(1)

# Lookup each callsign
enriched_qsos = []
for i, qso in enumerate(qsos):
    if i > 0 and i % 10 == 0:
        print(f"   Processed {i}/{len(qsos)} callsigns...")
    
    call = qso['call']
    lookup_url = f"https://xmldata.qrz.com/xml/current/?s={session_key}&callsign={call}"
    
    try:
        lookup_response = requests.get(lookup_url)
        lookup_root = ET.fromstring(lookup_response.text)
        
        lat_elem = lookup_root.find('.//lat')
        lon_elem = lookup_root.find('.//lon')
        country_elem = lookup_root.find('.//country')
        
        if lat_elem is not None and lon_elem is not None:
            try:
                qso['lat'] = float(lat_elem.text)
                qso['lon'] = float(lon_elem.text)
                if country_elem is not None:
                    qso['country'] = country_elem.text
                enriched_qsos.append(qso)
            except:
                pass
        
        time.sleep(0.1)  # Rate limiting
    except:
        pass

print(f"✅ Enriched {len(enriched_qsos)} QSOs with coordinates")

if len(enriched_qsos) == 0:
    print("❌ ERROR: No QSOs with coordinates!")
    exit(1)

# Step 4: Create map
print(f"\n🗺️  Step 4: Generating interactive map...")

avg_lat = sum(q['lat'] for q in enriched_qsos) / len(enriched_qsos)
avg_lon = sum(q['lon'] for q in enriched_qsos) / len(enriched_qsos)

m = folium.Map(
    location=[avg_lat, avg_lon],
    zoom_start=4,
    tiles='OpenStreetMap'
)

# Home location (Istanbul)
home_lat = 41.03
home_lon = 29.0

# Add home marker
folium.Marker(
    location=[home_lat, home_lon],
    popup="<b>TA1ZMP / LZ1MPN</b><br>Home QTH<br>Istanbul, Turkey",
    icon=folium.Icon(color='green', icon='home', prefix='fa')
).add_to(m)

# Add QSO markers and lines
for qso in enriched_qsos:
    folium.Marker(
        location=[qso['lat'], qso['lon']],
        popup=f"<b>{qso['call']}</b><br>{qso.get('country', 'N/A')}<br>{qso.get('date', 'N/A')}",
        icon=folium.Icon(color='red', icon='radio', prefix='fa')
    ).add_to(m)
    
    folium.PolyLine(
        locations=[[home_lat, home_lon], [qso['lat'], qso['lon']]],
        color='blue',
        weight=1,
        opacity=0.4
    ).add_to(m)

# Save
os.makedirs('output', exist_ok=True)
m.save('output/index.html')

print(f"✅ Map saved to output/index.html")
print(f"\n📊 Statistics:")
print(f"   Total QSOs: {len(qsos)}")
print(f"   With coordinates: {len(enriched_qsos)}")
print(f"   Success rate: {len(enriched_qsos)/len(qsos)*100:.1f}%")
print("=" * 70)
