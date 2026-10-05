import argparse
import html
import json
import os
import re
import time
import urllib.parse
import xml.etree.ElementTree as ET

import folium
import requests
from branca.element import MacroElement
from folium import plugins
from jinja2 import Template

HOME_LAT = 41.0653
HOME_LON = 29.0291
HOME_LABEL = "TA1ZMP"
HOME_GRID = "KM41mb"

ADIF_TAG = re.compile(r"<([A-Za-z_][A-Za-z0-9_]*):(\d+)(?::[^>]*)?>")


def grid_to_latlon(grid):
    """Convert a Maidenhead grid square to the latitude/longitude of its center."""
    if not grid or len(grid) < 4:
        return None, None

    grid = grid.upper().strip()

    try:
        lon = (ord(grid[0]) - ord("A")) * 20 - 180
        lat = (ord(grid[1]) - ord("A")) * 10 - 90
        lon += int(grid[2]) * 2
        lat += int(grid[3]) * 1

        if len(grid) >= 6:
            lon += (ord(grid[4]) - ord("A")) * (5.0 / 60.0)
            lat += (ord(grid[5]) - ord("A")) * (2.5 / 60.0)
            lon += (5.0 / 60.0) / 2.0
            lat += (2.5 / 60.0) / 2.0
        else:
            lon += 1.0
            lat += 0.5

        return lat, lon
    except Exception:
        return None, None


def approximate_6char_grid(grid_4char):
    """Approximate a 6-character grid by adding the center subsquare."""
    if not grid_4char:
        return grid_4char
    if len(grid_4char) >= 6:
        return grid_4char
    if len(grid_4char) == 4:
        return grid_4char + "LL"
    return grid_4char


def parse_adif(adif_data):
    """Parse ADIF text into QSO dicts. Values are sliced by the declared length."""
    qsos = []
    records = re.split(r"<eor>", adif_data, flags=re.IGNORECASE)

    for record in records:
        if not record.strip():
            continue

        fields = {}
        position = 0
        while True:
            match = ADIF_TAG.search(record, position)
            if not match:
                break
            length = int(match.group(2))
            start = match.end()
            fields[match.group(1).lower()] = record[start:start + length].strip()
            position = start + length

        call = fields.get("call", "").upper()
        if not call:
            continue

        qso = {"call": call}
        grid = fields.get("gridsquare", "").upper()
        if grid:
            qso["grid"] = grid
        date = fields.get("qso_date", "")
        if len(date) >= 8 and date[:8].isdigit():
            qso["date"] = f"{date[0:4]}-{date[4:6]}-{date[6:8]}"
        time_on = re.sub(r"\D", "", fields.get("time_on", ""))
        if time_on:
            qso["time"] = time_on[:6]
        for source, target in (
            ("band", "band"),
            ("mode", "mode"),
            ("country", "country"),
        ):
            if fields.get(source):
                qso[target] = fields[source]
        name = fields.get("name") or fields.get("name_intl")
        if name:
            qso["name"] = name
        qsos.append(qso)

    return qsos


def join_name(fname, lname):
    fname = (fname or "").strip()
    lname = (lname or "").strip()
    if fname and lname:
        if fname.lower() in lname.lower():
            return lname
        return f"{fname} {lname}"
    return fname or lname or None


def qrz_lookup(callsign, session_key):
    """Look up a callsign on the QRZ XML API. Returns grid, name, and error flags."""
    try:
        quoted = urllib.parse.quote(callsign)
        url = f"https://xmldata.qrz.com/xml/current/?s={session_key}&callsign={quoted}"
        response = requests.get(url, timeout=8)
        root = ET.fromstring(response.text)
        error = root.find(".//Error")
        if error is not None and error.text:
            text = error.text.lower()
            if any(word in text for word in ("session", "timeout", "password", "subscription", "key")):
                return {"fatal": True, "error": error.text}
            return {"missing": True, "error": error.text}

        grid_elem = root.find(".//grid")
        grid = grid_elem.text.strip().upper() if grid_elem is not None and grid_elem.text else None
        name = join_name(root.findtext(".//fname"), root.findtext(".//name"))
        return {"grid": grid, "name": name}
    except Exception as exc:
        return {"network": True, "error": str(exc)}


def login_qrz(callsign, api_key):
    try:
        login_url = (
            "https://xmldata.qrz.com/xml/current/"
            f"?username={urllib.parse.quote(callsign)}&password={urllib.parse.quote(api_key)}"
        )
        login_response = requests.get(login_url, timeout=10)
        root = ET.fromstring(login_response.text)
        session_elem = root.find(".//Key")
        if session_elem is not None and session_elem.text:
            return session_elem.text
    except Exception:
        return None
    return None


def enrich_qsos(qsos, session_key):
    """Fill short grids and missing names from QRZ. One lookup per distinct callsign."""
    enriched_count = 0
    approximated_count = 0
    names_filled = 0
    cache = {}
    lookups_stopped = False
    network_errors = 0

    for qso in qsos:
        original_grid = qso.get("grid", "")
        needs_grid = len(original_grid) < 6
        needs_name = not qso.get("name")

        if not needs_grid and not needs_name:
            qso["grid_source"] = "original_6char"
            continue

        info = None
        if session_key and not lookups_stopped:
            call = qso["call"]
            if call not in cache:
                if cache:
                    time.sleep(0.15)
                cache[call] = qrz_lookup(call, session_key)
            info = cache[call]
            if info.get("fatal"):
                lookups_stopped = True
                print(f"   QRZ lookups stopped: {info.get('error')}")
                info = None
            elif info.get("network"):
                network_errors += 1
                if network_errors >= 5:
                    lookups_stopped = True
                    print("   QRZ lookups stopped after repeated network errors")
                info = None

        if info and needs_name and info.get("name"):
            qso["name"] = info["name"]
            names_filled += 1

        if needs_grid:
            qrz_grid = (info or {}).get("grid") or ""
            if len(qrz_grid) >= 4:
                taken = qrz_grid[:6] if len(qrz_grid) >= 6 else qrz_grid
                qso["grid"] = taken
                qso["grid_source"] = "qrz" if taken[:4] == original_grid[:4] or not original_grid else "qrz_override"
                if original_grid:
                    qso["grid_original"] = original_grid
                enriched_count += 1
                if enriched_count <= 10:
                    print(f"   {qso['call']}: {original_grid or '—'} -> {taken} ({qso['grid_source']})")
            elif len(original_grid) == 4:
                qso["grid"] = approximate_6char_grid(original_grid)
                qso["grid_source"] = "approximated"
                qso["grid_original"] = original_grid
                approximated_count += 1
        elif original_grid:
            qso["grid_source"] = qso.get("grid_source") or "original_6char"

    return enriched_count, approximated_count, names_filled


def attach_coordinates(qsos):
    located = 0
    for qso in qsos:
        grid = qso.get("grid") or ""
        if len(grid) < 4:
            continue
        lat, lon = grid_to_latlon(grid)
        if lat is None or lon is None:
            continue
        qso["lat"] = round(lat, 5)
        qso["lon"] = round(lon, 5)
        located += 1
    return located


def public_records(qsos):
    """Fields the browser needs for filtering, tables, and markers."""
    records = []
    for qso in qsos:
        record = {
            "call": qso.get("call", ""),
            "name": qso.get("name", ""),
            "country": qso.get("country", ""),
            "date": qso.get("date", ""),
            "time": qso.get("time", ""),
            "band": qso.get("band", ""),
            "mode": qso.get("mode", ""),
            "grid": qso.get("grid", ""),
        }
        if "lat" in qso and "lon" in qso:
            record["lat"] = qso["lat"]
            record["lon"] = qso["lon"]
        records.append(record)
    return records


PANEL_HTML = """
<div id="qso-panel">
  <div class="panel-head">
    <h4>QSO Statistics</h4>
    <button type="button" id="qso-panel-toggle" aria-expanded="true" aria-controls="qso-panel-body" title="Toggle statistics">▾</button>
  </div>
  <div id="qso-panel-body">
    <div class="periods" role="group" aria-label="Period">
      <button type="button" class="period active" data-period="all">All</button>
      <button type="button" class="period" data-period="year">Last year</button>
      <button type="button" class="period" data-period="month">Last month</button>
      <button type="button" class="period" data-period="day">Day</button>
    </div>
    <input id="qso-day" type="date" aria-label="Day">
    <p id="qso-range"></p>
    <button type="button" class="stat" data-kind="qsos"><span>Total QSOs</span><b id="stat-qsos">0</b></button>
    <button type="button" class="stat" data-kind="grids"><span>Unique Grids</span><b id="stat-grids">0</b></button>
    <button type="button" class="stat" data-kind="countries"><span>Countries</span><b id="stat-countries">0</b></button>
    <button type="button" class="stat" data-kind="bands"><span>Bands</span><b id="stat-bands">0</b></button>
    <button type="button" class="stat" data-kind="modes"><span>Modes</span><b id="stat-modes">0</b></button>
    <p class="hint">Click a row to open the table.</p>
    <hr>
    <small id="qso-grid-note"></small>
  </div>
</div>
<div id="qso-modal" role="dialog" aria-modal="true" aria-labelledby="qso-modal-title">
  <div id="qso-sheet">
    <div class="sheet-head">
      <button type="button" id="qso-back">Back</button>
      <h3 id="qso-modal-title"></h3>
      <button type="button" id="qso-close" aria-label="Close">×</button>
    </div>
    <div class="sheet-scroll" id="qso-modal-body"></div>
  </div>
</div>
"""

PANEL_CSS = """
<style>
#qso-panel {
  position: fixed;
  top: 10px;
  right: 10px;
  box-sizing: border-box;
  width: min(250px, calc(100vw - 20px));
  max-height: calc(100vh - 20px);
  max-height: calc(100dvh - 20px);
  overflow: auto;
  background: #fff;
  z-index: 9999;
  padding: 10px 12px;
  border: 2px solid #8d97a5;
  border-radius: 6px;
  font-family: Arial, sans-serif;
  font-size: 13px;
  box-shadow: 0 2px 10px rgba(0,0,0,.12);
}
#qso-panel .panel-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 8px;
}
#qso-panel h4 { margin: 0; font-size: 15px; }
#qso-panel-toggle {
  display: none;
  flex: 0 0 auto;
  width: 32px;
  height: 28px;
  border: 1px solid #c5cdd8;
  border-radius: 4px;
  background: #fff;
  cursor: pointer;
  font: inherit;
  line-height: 1;
}
#qso-panel .periods { display: flex; flex-wrap: wrap; gap: 4px; }
#qso-panel .period {
  flex: 1 1 calc(50% - 4px);
  min-width: 0;
  border: 1px solid #c5cdd8;
  background: #fff;
  border-radius: 4px;
  padding: 4px 6px;
  cursor: pointer;
  font: inherit;
}
#qso-panel .period.active { background: #1d4f91; color: #fff; border-color: #1d4f91; }
#qso-day { display: none; width: 100%; margin-top: 6px; box-sizing: border-box; font: inherit; }
#qso-day.visible { display: block; }
#qso-range { margin: 8px 0 4px; color: #526070; font-size: 12px; }
#qso-panel .stat {
  display: flex;
  justify-content: space-between;
  gap: 8px;
  width: 100%;
  margin: 0;
  padding: 5px 4px;
  border: 0;
  border-radius: 4px;
  background: transparent;
  font: inherit;
  cursor: pointer;
  text-align: left;
  box-sizing: border-box;
}
#qso-panel .stat:hover, #qso-panel .stat:focus { background: #eef3ff; outline: none; }
#qso-panel .hint { margin: 6px 0 0; color: #68788c; font-size: 11px; }
#qso-panel hr { margin: 6px 0; border: 0; border-top: 1px solid #e1e6ee; }
#qso-panel small { color: #526070; }
#qso-modal {
  display: none;
  position: fixed;
  inset: 0;
  z-index: 10001;
  background: rgba(16, 24, 40, .45);
  align-items: flex-start;
  justify-content: center;
  padding: 32px 12px;
  box-sizing: border-box;
}
#qso-modal.open { display: flex; }
#qso-sheet {
  background: #fff;
  width: min(980px, 100%);
  max-height: min(82vh, 760px);
  max-height: min(82dvh, 760px);
  display: flex;
  flex-direction: column;
  border-radius: 8px;
  overflow: hidden;
  box-shadow: 0 12px 40px rgba(0,0,0,.28);
  font-family: Arial, sans-serif;
}
.sheet-head {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 12px;
  border-bottom: 1px solid #e6ebf2;
}
.sheet-head h3 { flex: 1; margin: 0; font-size: 16px; min-width: 0; overflow-wrap: anywhere; }
.sheet-head button {
  border: 1px solid #c5cdd8;
  background: #fff;
  border-radius: 4px;
  padding: 4px 8px;
  cursor: pointer;
  font: inherit;
}
#qso-back[hidden] { display: none; }
#qso-close { font-size: 18px; line-height: 1; }
.sheet-scroll {
  overflow: auto;
  -webkit-overflow-scrolling: touch;
  max-width: 100%;
}
#qso-modal table { width: 100%; border-collapse: collapse; font-size: 13px; }
#qso-modal th, #qso-modal td {
  padding: 6px 8px;
  border-bottom: 1px solid #eef1f6;
  text-align: left;
  vertical-align: top;
  white-space: nowrap;
}
#qso-modal th {
  position: sticky;
  top: 0;
  background: #f4f7fb;
  z-index: 1;
}
#qso-modal tr.clickable { cursor: pointer; }
#qso-modal tr.clickable:hover { background: #eef3ff; }
#qso-modal a { color: #1d4f91; }
.qso-empty { margin: 16px; color: #526070; }
@media (max-width: 700px) {
  #qso-panel {
    top: 8px;
    left: 8px;
    right: 8px;
    width: auto;
    max-width: none;
    max-height: min(42vh, calc(100dvh - 16px));
    padding: 8px 10px;
    font-size: 12px;
  }
  #qso-panel.collapsed {
    max-height: none;
    overflow: hidden;
  }
  #qso-panel.collapsed #qso-panel-body { display: none; }
  #qso-panel.collapsed .panel-head { margin-bottom: 0; }
  #qso-panel-toggle { display: inline-flex; align-items: center; justify-content: center; }
  #qso-panel.collapsed #qso-panel-toggle { transform: rotate(-90deg); }
  #qso-panel h4 { font-size: 14px; }
  #qso-panel .period { padding: 6px 4px; font-size: 12px; }
  #qso-panel .stat { padding: 6px 2px; }
  #qso-modal {
    padding: 0;
    align-items: stretch;
  }
  #qso-sheet {
    width: 100%;
    max-height: none;
    height: 100%;
    border-radius: 0;
  }
  #qso-modal table { font-size: 12px; }
}
</style>
"""

ANALYSIS_TEMPLATE = r"""
{% macro script(this, kwargs) %}
(function () {
  var map = {{ this._parent.get_name() }};
  var home = [{{ this.home_lat }}, {{ this.home_lon }}];

  function start() {
    if (!document.getElementById("qso-panel") || !document.getElementById("qso-data")) {
      document.addEventListener("DOMContentLoaded", start, { once: true });
      return;
    }
    boot();
  }

  function boot() {
    var qsos = JSON.parse(document.getElementById("qso-data").textContent || "[]");
    var layer = L.layerGroup().addTo(map);
    var period = "all";
    var dayValue = latestDate(qsos) || utcToday();
    var viewState = null;
    var modal = document.getElementById("qso-modal");
    var modalTitle = document.getElementById("qso-modal-title");
    var modalBody = document.getElementById("qso-modal-body");
    var backBtn = document.getElementById("qso-back");
    var dayInput = document.getElementById("qso-day");
    var panel = document.getElementById("qso-panel");
    var panelToggle = document.getElementById("qso-panel-toggle");
    var mobileQuery = window.matchMedia("(max-width: 700px)");

    function setPanelCollapsed(collapsed) {
      panel.classList.toggle("collapsed", collapsed);
      panelToggle.setAttribute("aria-expanded", collapsed ? "false" : "true");
    }

    function syncPanelForViewport() {
      setPanelCollapsed(mobileQuery.matches);
    }

    panelToggle.addEventListener("click", function () {
      setPanelCollapsed(!panel.classList.contains("collapsed"));
    });
    if (mobileQuery.addEventListener) {
      mobileQuery.addEventListener("change", syncPanelForViewport);
    } else if (mobileQuery.addListener) {
      mobileQuery.addListener(syncPanelForViewport);
    }
    syncPanelForViewport();

    document.getElementById("qso-grid-note").innerHTML =
      "Grid enriched: {{ this.enriched_count }} from QRZ<br>Approximated: {{ this.approximated_count }}";

    function utcToday() {
      return new Date().toISOString().slice(0, 10);
    }

    function shift(iso, years, months) {
      var parts = iso.split("-").map(Number);
      var year = parts[0] + years;
      var month = parts[1] - 1 + months;
      var day = parts[2];
      var dim = new Date(Date.UTC(year, month + 1, 0)).getUTCDate();
      if (day > dim) day = dim;
      return new Date(Date.UTC(year, month, day)).toISOString().slice(0, 10);
    }

    function latestDate(list) {
      var best = "";
      list.forEach(function (qso) {
        if (qso.date && qso.date > best) best = qso.date;
      });
      return best;
    }

    function periodStart() {
      var today = utcToday();
      if (period === "year") return shift(today, -1, 0);
      if (period === "month") return shift(today, 0, -1);
      return today;
    }

    function inPeriod(qso) {
      if (period === "all") return true;
      if (!qso.date) return false;
      if (period === "day") return qso.date === dayValue;
      var today = utcToday();
      return qso.date >= periodStart() && qso.date <= today;
    }

    function filtered() {
      return qsos.filter(inPeriod);
    }

    function esc(value) {
      return String(value == null ? "" : value)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;");
    }

    function fmtTime(value) {
      var digits = String(value || "").replace(/\D/g, "");
      if (digits.length >= 4) return digits.slice(0, 2) + ":" + digits.slice(2, 4);
      return "";
    }

    function periodLabel() {
      if (period === "all") return "All logged QSOs";
      if (period === "day") return dayValue || "Day";
      return periodStart() + " – " + utcToday();
    }

    function groupBy(list, keyFn) {
      var groups = {};
      list.forEach(function (qso) {
        var key = keyFn(qso) || "Unknown";
        if (!groups[key]) groups[key] = [];
        groups[key].push(qso);
      });
      return Object.keys(groups).map(function (key) {
        return { label: key, count: groups[key].length, items: groups[key] };
      }).sort(function (a, b) {
        return b.count - a.count || a.label.localeCompare(b.label);
      });
    }

    function uniqueValues(items, field) {
      var seen = {};
      var values = [];
      items.forEach(function (qso) {
        var value = qso[field];
        if (!value || seen[value]) return;
        seen[value] = true;
        values.push(value);
      });
      values.sort();
      return values;
    }

    function preview(values) {
      if (values.length <= 6) return values.join(", ");
      return values.slice(0, 6).join(", ") + " +" + (values.length - 6);
    }

    function renderStats(list) {
      var grids = {};
      list.forEach(function (qso) {
        if (qso.grid) grids[qso.grid] = true;
      });
      document.getElementById("stat-qsos").textContent = String(list.length);
      document.getElementById("stat-grids").textContent = String(Object.keys(grids).length);
      document.getElementById("stat-countries").textContent = String(groupBy(list, function (qso) { return qso.country; }).length);
      document.getElementById("stat-bands").textContent = String(groupBy(list, function (qso) { return qso.band; }).length);
      document.getElementById("stat-modes").textContent = String(groupBy(list, function (qso) { return qso.mode; }).length);
      document.getElementById("qso-range").textContent = periodLabel();
    }

    function markerIcon(count) {
      var color = count >= 5 ? "red" : count >= 2 ? "orange" : "blue";
      var glyph = count >= 5 ? "star" : count >= 2 ? "certificate" : "info-sign";
      try {
        if (L.AwesomeMarkers) {
          return L.AwesomeMarkers.icon({ icon: glyph, markerColor: color, prefix: "glyphicon" });
        }
      } catch (error) {}
      return L.divIcon({ className: "", iconSize: [12, 12] });
    }

    function popupHtml(grid, items) {
      var html = "<div style='min-width:180px'><b>Grid: " + esc(grid) + "</b><br><b>" +
        items.length + " QSO(s)</b><br><br>";
      items.slice(0, 8).forEach(function (qso) {
        html += "<b>" + esc(qso.call) + "</b>";
        if (qso.country) html += " (" + esc(qso.country) + ")";
        if (qso.name) html += "<br>" + esc(qso.name);
        html += "<br>" + esc(qso.date || "") + " " + esc(qso.band || "") + " " + esc(qso.mode || "") + "<br>";
      });
      if (items.length > 8) html += "<i>... and " + (items.length - 8) + " more</i>";
      return html + "</div>";
    }

    function renderMap(list) {
      layer.clearLayers();
      var groups = {};
      list.forEach(function (qso) {
        if (qso.lat == null || qso.lon == null || !qso.grid) return;
        if (!groups[qso.grid]) groups[qso.grid] = [];
        groups[qso.grid].push(qso);
      });
      Object.keys(groups).forEach(function (grid) {
        var items = groups[grid];
        var latlng = [items[0].lat, items[0].lon];
        L.marker(latlng, { icon: markerIcon(items.length) })
          .bindPopup(popupHtml(grid, items))
          .bindTooltip(items.length + " QSO(s) from " + grid)
          .addTo(layer);
        L.polyline([home, latlng], { color: "#2b6cb0", weight: 1, opacity: 0.45 }).addTo(layer);
      });
    }

    function sortQsos(list) {
      return list.slice().sort(function (a, b) {
        var byDate = (b.date || "").localeCompare(a.date || "");
        if (byDate) return byDate;
        return (b.time || "").localeCompare(a.time || "");
      });
    }

    function qsoTable(list) {
      var rows = sortQsos(list).map(function (qso, index) {
        var call = esc(qso.call || "");
        var link = call
          ? "<a href=\"https://www.qrz.com/db/" + encodeURIComponent(qso.call) + "\" target=\"_blank\" rel=\"noopener\">" + call + "</a>"
          : "";
        return "<tr><td>" + (index + 1) + "</td><td>" + esc(qso.date || "") + "</td><td>" +
          esc(fmtTime(qso.time)) + "</td><td>" + link + "</td><td>" + esc(qso.country || "") +
          "</td><td>" + esc(qso.name || "") + "</td><td>" + esc(qso.band || "") + "</td><td>" +
          esc(qso.mode || "") + "</td><td>" + esc(qso.grid || "") + "</td></tr>";
      }).join("");
      if (!rows) return "<p class='qso-empty'>No QSOs in this period.</p>";
      return "<table><thead><tr><th>N</th><th>Date</th><th>Time</th><th>Callsign</th><th>Country</th><th>Name</th><th>Band</th><th>Mode</th><th>Grid</th></tr></thead><tbody>" +
        rows + "</tbody></table>";
    }

    function summaryTable(groups, kind) {
      var head = "<th>" + (kind === "grid" ? "Grid" : kind === "country" ? "Country" : kind === "band" ? "Band" : "Mode") +
        "</th><th>QSOs</th>";
      if (kind === "grid" || kind === "country") head += "<th>Callsigns</th>";
      if (kind === "grid") head += "<th>Countries</th>";
      var body = groups.map(function (group) {
        var extra = "";
        if (kind === "grid" || kind === "country") {
          extra += "<td>" + esc(preview(uniqueValues(group.items, "call"))) + "</td>";
        }
        if (kind === "grid") extra += "<td>" + esc(preview(uniqueValues(group.items, "country"))) + "</td>";
        return "<tr class='clickable' data-kind='" + kind + "' data-key=\"" + esc(group.label) + "\"><td>" +
          esc(group.label) + "</td><td>" + group.count + "</td>" + extra + "</tr>";
      }).join("");
      if (!body) return "<p class='qso-empty'>No QSOs in this period.</p>";
      return "<table><thead><tr>" + head + "</tr></thead><tbody>" + body + "</tbody></table>";
    }

    function viewFor(kind, list) {
      if (kind === "qsos") {
        return { title: "Total QSOs — " + list.length, html: qsoTable(list) };
      }
      if (kind === "grids") {
        var grids = groupBy(list.filter(function (qso) { return qso.grid; }), function (qso) { return qso.grid; });
        return { title: "Unique grids — " + grids.length, html: summaryTable(grids, "grid") };
      }
      if (kind === "countries") {
        var countries = groupBy(list, function (qso) { return qso.country; });
        return { title: "Countries — " + countries.length, html: summaryTable(countries, "country") };
      }
      if (kind === "bands") {
        var bands = groupBy(list, function (qso) { return qso.band; });
        return { title: "Bands — " + bands.length, html: summaryTable(bands, "band") };
      }
      var modes = groupBy(list, function (qso) { return qso.mode; });
      return { title: "Modes — " + modes.length, html: summaryTable(modes, "mode") };
    }

    function drillView(kind, key, list) {
      var items = list.filter(function (qso) {
        var value = kind === "grid" ? qso.grid : kind === "country" ? qso.country : kind === "band" ? qso.band : qso.mode;
        return (value || "Unknown") === key;
      });
      var title = (kind === "grid" ? "Grid " + key : key) + " — " + items.length + " QSO" + (items.length === 1 ? "" : "s");
      return { title: title, html: qsoTable(items) };
    }

    function renderModal() {
      if (!viewState) {
        modal.classList.remove("open");
        return;
      }
      var list = filtered();
      var view = viewState.drillKind
        ? drillView(viewState.drillKind, viewState.drillKey, list)
        : viewFor(viewState.kind, list);
      modalTitle.textContent = view.title + " · " + periodLabel();
      modalBody.innerHTML = view.html;
      backBtn.hidden = !viewState.drillKind;
      modal.classList.add("open");
    }

    function refresh() {
      var list = filtered();
      renderStats(list);
      renderMap(list);
      dayInput.classList.toggle("visible", period === "day");
      if (viewState) renderModal();
    }

    document.querySelectorAll("#qso-panel .stat").forEach(function (button) {
      button.addEventListener("click", function () {
        viewState = { kind: button.getAttribute("data-kind") };
        renderModal();
      });
    });

    modalBody.addEventListener("click", function (event) {
      var row = event.target.closest("tr.clickable");
      if (!row || !viewState) return;
      viewState.drillKind = row.getAttribute("data-kind");
      viewState.drillKey = row.getAttribute("data-key");
      renderModal();
    });

    backBtn.addEventListener("click", function () {
      if (!viewState) return;
      viewState.drillKind = null;
      viewState.drillKey = null;
      renderModal();
    });

    document.getElementById("qso-close").addEventListener("click", function () {
      viewState = null;
      renderModal();
    });

    modal.addEventListener("click", function (event) {
      if (event.target === modal) {
        viewState = null;
        renderModal();
      }
    });

    document.addEventListener("keydown", function (event) {
      if (event.key === "Escape" && viewState) {
        viewState = null;
        renderModal();
      }
    });

    document.querySelectorAll("#qso-panel .period").forEach(function (button) {
      button.addEventListener("click", function () {
        period = button.getAttribute("data-period");
        document.querySelectorAll("#qso-panel .period").forEach(function (item) {
          item.classList.toggle("active", item === button);
        });
        refresh();
      });
    });

    dayInput.value = dayValue;
    dayInput.addEventListener("change", function (event) {
      dayValue = event.target.value;
      if (period === "day") refresh();
    });

    refresh();
  }

  start();
})();
{% endmacro %}
"""


class QsoAnalysis(MacroElement):
    _template = Template(ANALYSIS_TEMPLATE)

    def __init__(self, home_lat, home_lon, enriched_count, approximated_count):
        super().__init__()
        self._name = "QsoAnalysis"
        self.home_lat = home_lat
        self.home_lon = home_lon
        self.enriched_count = enriched_count
        self.approximated_count = approximated_count


def build_map(qsos, enriched_count, approximated_count):
    located = [qso for qso in qsos if "lat" in qso and "lon" in qso]
    if located:
        center_lat = sum(qso["lat"] for qso in located) / len(located)
        center_lon = sum(qso["lon"] for qso in located) / len(located)
    else:
        center_lat, center_lon = HOME_LAT, HOME_LON

    fmap = folium.Map(location=[center_lat, center_lon], zoom_start=4, tiles="OpenStreetMap")
    fmap.get_root().title = f"{HOME_LABEL} QSO Map"

    folium.Marker(
        [HOME_LAT, HOME_LON],
        popup=f"<b>{HOME_LABEL}</b><br>Istanbul, Turkey<br>Grid: {HOME_GRID}",
        tooltip="Home QTH",
        icon=folium.Icon(color="green", icon="home", prefix="fa"),
    ).add_to(fmap)

    folium.LayerControl().add_to(fmap)
    plugins.Fullscreen().add_to(fmap)

    payload = json.dumps(public_records(qsos), ensure_ascii=False).replace("<", "\\u003c")
    root = fmap.get_root()
    root.header.add_child(folium.Element(PANEL_CSS))
    root.html.add_child(folium.Element(PANEL_HTML))
    root.html.add_child(folium.Element(
        f'<script id="qso-data" type="application/json">{payload}</script>'
    ))
    fmap.add_child(QsoAnalysis(HOME_LAT, HOME_LON, enriched_count, approximated_count))
    return fmap


def download_log(api_key):
    url = f"https://logbook.qrz.com/api?KEY={urllib.parse.quote(api_key)}&ACTION=FETCH&OPTION=TYPE:ADIF"
    response = requests.get(url, timeout=60)
    if response.status_code != 200:
        raise SystemExit(f"ERROR: logbook download failed with HTTP {response.status_code}")
    return html.unescape(response.text)


def sample_adif():
    """A few QSOs spread across today, last month, last year, and older."""
    rows = [
        ("LZ1AAA", "20261003", "1200", "20m", "FT8", "KN22AA", "Bulgaria", "Ivan Petrov"),
        ("LZ1AAB", "20261003", "1530", "40m", "SSB", "KN22AA", "Bulgaria", ""),
        ("DL1ABC", "20260915", "0900", "20m", "FT8", "JN49AA", "Germany", "Hans Meier"),
        ("G0ABC", "20260801", "1800", "40m", "CW", "IO91AA", "England", "Ann Smith"),
        ("JA1ABC", "20240101", "0001", "15m", "FT8", "PM95AA", "Japan", "Kenji Sato"),
        ("TA1ABC", "20261002", "1015", "20m", "FT8", "KM41AA", "Turkey", "Ayse Yilmaz"),
        ("XX0XXX", "", "", "20m", "FT8", "", "", ""),
    ]
    chunks = []
    for call, date, time_on, band, mode, grid, country, name in rows:
        fields = {"call": call, "band": band, "mode": mode}
        if date:
            fields["qso_date"] = date
        if time_on:
            fields["time_on"] = time_on
        if grid:
            fields["gridsquare"] = grid
        if country:
            fields["country"] = country
        if name:
            fields["name"] = name
        parts = [f"<{key}:{len(value)}>{value}" for key, value in fields.items()]
        parts.append("<eor>")
        chunks.append("".join(parts))
    return "\n".join(chunks)


def assert_parser():
    parsed = parse_adif(sample_adif())
    by_call = {qso["call"]: qso for qso in parsed}
    if len(parsed) != 7:
        raise SystemExit(f"Parser self-check failed: expected 7 QSOs, got {len(parsed)}")
    ivan = by_call["LZ1AAA"]
    if ivan.get("name") != "Ivan Petrov" or ivan.get("date") != "2026-10-03":
        raise SystemExit(f"Parser self-check failed: {ivan}")
    if ivan.get("grid") != "KN22AA" or ivan.get("time") != "1200":
        raise SystemExit(f"Parser self-check failed: {ivan}")
    if by_call["XX0XXX"].get("date") or by_call["XX0XXX"].get("grid"):
        raise SystemExit("Parser self-check failed: empty fields leaked through")


def print_summary(qsos, enriched_count, approximated_count, names_filled):
    countries = sorted({qso.get("country") or "Unknown" for qso in qsos})
    bands = sorted({qso.get("band") or "Unknown" for qso in qsos})
    modes = sorted({qso.get("mode") or "Unknown" for qso in qsos})
    grids = {qso.get("grid") for qso in qsos if qso.get("grid")}
    named = sum(1 for qso in qsos if qso.get("name"))
    print("Final statistics:")
    print(f"   Total QSOs: {len(qsos)}")
    print(f"   Unique grids: {len(grids)}")
    print(f"   Countries: {', '.join(countries)}")
    print(f"   Bands: {', '.join(bands)}")
    print(f"   Modes: {', '.join(modes)}")
    print(f"   Names present: {named} (filled from QRZ this run: {names_filled})")
    print(f"   Grids from QRZ: {enriched_count}; approximated: {approximated_count}")


def main():
    parser = argparse.ArgumentParser(description="Build the TA1ZMP interactive QSO map")
    parser.add_argument("--sample", action="store_true", help="Write a fixture map and skip QRZ")
    args = parser.parse_args()

    print("=" * 70)
    print("TA1ZMP QSO Map Generator")
    print("=" * 70)
    assert_parser()

    enriched_count = 0
    approximated_count = 0
    names_filled = 0

    if args.sample:
        print("Using the built-in sample log.")
        qsos = parse_adif(sample_adif())
    else:
        api_key = os.environ.get("QRZ_API_KEY")
        callsign = os.environ.get("QRZ_USERNAME")
        if not api_key or not callsign:
            raise SystemExit("QRZ_API_KEY and QRZ_USERNAME are required")

        print("Downloading logbook...")
        adif_data = download_log(api_key)
        print(f"Downloaded {len(adif_data)} bytes")
        qsos = parse_adif(adif_data)
        print(f"Parsed {len(qsos)} QSO records")
        if not qsos:
            raise SystemExit("ERROR: No QSOs found")

        print("Enriching grids and names from QRZ...")
        session_key = login_qrz(callsign, api_key)
        if session_key:
            print("QRZ XML session established")
        else:
            print("QRZ XML login failed; 4-character grids will be approximated")
        enriched_count, approximated_count, names_filled = enrich_qsos(qsos, session_key)

    located = attach_coordinates(qsos)
    print(f"{located} QSOs have coordinates")
    if located == 0:
        raise SystemExit("ERROR: No valid grid squares")

    print("Creating interactive map...")
    fmap = build_map(qsos, enriched_count, approximated_count)
    os.makedirs("output", exist_ok=True)
    target = "output/sample.html" if args.sample else "output/index.html"
    fmap.save(target)
    print(f"Map saved to {target}")
    print_summary(qsos, enriched_count, approximated_count, names_filled)
    print("=" * 70)


if __name__ == "__main__":
    main()
