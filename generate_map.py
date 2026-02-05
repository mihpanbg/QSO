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

# Step 2: Parse ADIF (improved regex)
print(f"\n🔍 Step 2: Parsing ADIF records...")
qsos = []

# Split by <eor> or <EOR>
records = re.split(r'<eor>|<EOR>', adif_data, flags=re.IGNORECASE)

for record in records:
    if not record.strip() or len(record) < 20:
        continue
    
    qso = {}
    
    # Extract CALL (case insensitive, flexible format)
    call_match = re.search(r'<call:(\d+)>([^<\n]+)', record, re.IGNORECASE)
    if call_match:
        call_len = int(call_match.group(1))
        call_value = call_match.group(2).strip()[:call_len]
        qso['call'] = call_value.upper()
    
    # Extract QSO_DATE
    date_match = re.search(r'<qso_date:(\d+)>(\d+)', record, re.IGNORECASE)
    if date_match:
        qso['date'] = date_match.group(2)[:8]
    
    # Extract BAND
    band_match = re.search(r'<band:(\d+)>([^<\n]+)', record, re.IGNORECASE)
    if band_match:
        band_len = int(band_match.group(1))
        qso['band'] = band_match.group(2).strip()[:band_len]
    
    if 'call' in qso:
        qsos.append(qso)
        if len(qsos) <= 5:  # Debug first 5
            print(f"   Found: {qso['call']} on {qso.get('date', 'N/A')}")

print(f"✅ Parsed {len(qsos)} QSO records")

if len(qsos) == 0:
    print("❌ ERROR: No QSOs found!")
    print("First 500 chars of ADIF data:")
    print(adif_data[:500])
    exit(1)

# Step 3: Get coordinates from QRZ.com
print(f"\n🌍 Step 3: Enriching with coordinates from QRZ.com...")

# Login to QRZ XML API
session_key = None
login_url = f"https://xmldata.qrz.com/xml/current/?username={CALLSIGN}&password={API_KEY}"

try:
    login_response = requests.get(login_url, timeout=10)
    root = ET.fromstring(login_response.text)
    session_elem = root.find('.//Key')
    if session_elem is not None:
        session_key = session_elem.text
        print(f"✅ QRZ XML API session established")
    else:
        error_elem = root.find('.//Error')
        if error_elem is not None:
            print(f"❌ ERROR: QRZ XML API login failed: {error_elem.text}")
        else:
            print("❌ ERROR: Failed to login to QRZ XML API")
        exit(1)
except Exception as e:
    print(f"❌ ERROR: QRZ XML API connection failed: {e}")
    exit(1)

# Lookup each callsign
enriched_qsos = []
processed = set()  # Avoid duplicates

for i, qso in enumerate(qsos):
    call = qso['call']
    
    # Skip duplicates
    if call in processed:
        continue
    processed.add(call)
    
    if i > 0 and i % 10 == 0:
        print(f"   Processed {i}/{len(qsos)} callsigns... ({len(enriched_qsos)} with coords)")
    
    lookup_url = f"https://xmldata.qrz.com/xml/current/?s={session_key}&callsign={call}"
    
    try:
        lookup_response = requests.get(lookup_url, timeout=5)
        lookup_root = ET.fromstring(lookup_response.text)
        
        lat_elem = lookup_root.find('.//lat')
        lon_elem = lookup_root.find('.//lon')
        country_elem = lookup_root.find('.//country')
        
        if lat_elem is not None and lon_elem is not None:
            try:
                lat = float(lat_elem.text)
                lon = float(lon_elem.text)
                
                enriched_qso = qso.copy()
                enriched_qso['lat'] = lat
                enriched_qso['lon'] = lon
                if country_elem is not None:
                    enriched_qso['country'] = country_elem.text
                
                enriched_qsos.append(enriched_qso)
            except ValueError:
                pass
        
        time.sleep(0.15)  # Rate limiting (max 6-7 req/sec)
    except Exception as e:
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
    icon=folium.Icon(color='green', icon='home')
).add_to(m)

# Add QSO markers and lines
for qso in enriched_qsos:
    popup_text = f"<b>{qso['call']}</b><br>"
    if 'country' in qso:
        popup_text += f"{qso['country']}<br>"
    if 'date' in qso:
        popup_text += f"{qso['date']}<br>"
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
        opacity=0.4
    ).add_to(m)

# Save
os.makedirs('output', exist_ok=True)
m.save('output/index.html')

print(f"✅ Map saved to output/index.html")
print(f"\n📊 Statistics:")
print(f"   Total QSOs: {len(qsos)}")
print(f"   Unique callsigns: {len(processed)}")
print(f"   With coordinates: {len(enriched_qsos)}")
print(f"   Success rate: {len(enriched_qsos)/len(processed)*100:.1f}%")
print("=" * 70)
