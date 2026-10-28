"""Fetch the Ly Surfing Beach forecast from Open-Meteo and write surf/data.json.

The «Карта жизни» page cannot fetch other sites itself (artifact CSP), so this
runs in GitHub Actions every hour; a Claude cloud routine copies the file into
the page's database. Output: {at, hours:[...], sun:{date:{rise,set}}}.
"""
import json, statistics, sys, urllib.request
from datetime import datetime, timezone, timedelta

LAT, LON = 16.035, 108.25
MODELS = [("ecmwf_wam025", "ECMWF"), ("meteofrance_wave", "Météo-France"), ("ncep_gfswave016", "NOAA"), ("gwam", "DWD")]
BASE = f"latitude={LAT}&longitude={LON}&forecast_days=7&timezone=Asia%2FHo_Chi_Minh"

def get(url):
    with urllib.request.urlopen(url, timeout=30) as r:
        return json.load(r)

# Weight by where each model's grid cell sits: DWD faces the beach, NOAA is offshore to the SE,
# ECMWF and Météo-France sit north of the Son Tra peninsula, which shelters the beach from north swell.
WEIGHT = {"DWD": 1.0, "NOAA": 0.8, "ECMWF": 0.4, "Météo-France": 0.4}
NORTH = {"ECMWF", "Météo-France"}

def wavg(per, idx, direction):
    north = direction is not None and (direction < 40 or direction > 330)
    pairs = [(WEIGHT[p[0]] * (0.5 if north and p[0] in NORTH else 1), p[idx]) for p in per if p[idx] is not None]
    total = sum(w for w, _ in pairs)
    return round(sum(w * v for w, v in pairs) / total, 2) if total else None

def med(xs):
    xs = [x for x in xs if x is not None]
    return round(statistics.median(xs), 2) if xs else None

def main(out):
    mm = get(f"https://marine-api.open-meteo.com/v1/marine?{BASE}&cell_selection=sea&models={','.join(m for m, _ in MODELS)}"
             "&hourly=wave_height,wave_period,wave_direction,swell_wave_height,swell_wave_period")
    try:
        sea = get(f"https://marine-api.open-meteo.com/v1/marine?{BASE}&cell_selection=sea&hourly=sea_level_height_msl,sea_surface_temperature")["hourly"]
    except Exception:
        sea = None
    wx = get(f"https://api.open-meteo.com/v1/forecast?{BASE}&wind_speed_unit=kmh"
             "&hourly=wind_speed_10m,wind_direction_10m,wind_gusts_10m,precipitation,weather_code,uv_index,temperature_2m&daily=sunrise,sunset")
    H, W = mm["hourly"], wx["hourly"]
    wi = {t: i for i, t in enumerate(W["time"])}
    si = {t: i for i, t in enumerate(sea["time"])} if sea else {}
    hours = []
    for i, t in enumerate(H["time"]):
        col = lambda k, m: (H.get(f"{k}_{m}") or [None] * len(H["time"]))[i]
        per = [[name, col("wave_height", m), col("wave_period", m)] for m, name in MODELS]
        j, s = wi.get(t), si.get(t)
        w = lambda k: W[k][j] if j is not None else None
        direction = med(col("wave_direction", m) for m, _ in MODELS)
        hours.append({
            "t": t, "h": wavg(per, 1, direction), "p": wavg(per, 2, direction), "dir": direction,
            "swell": med(col("swell_wave_height", m) for m, _ in MODELS), "swellP": med(col("swell_wave_period", m) for m, _ in MODELS),
            "per": per, "w": w("wind_speed_10m"), "wd": w("wind_direction_10m"), "gust": w("wind_gusts_10m"),
            "rain": w("precipitation"), "storm": (w("weather_code") or 0) >= 95, "uv": w("uv_index"), "air": w("temperature_2m"),
            "tide": sea["sea_level_height_msl"][s] if s is not None else None,
            "sst": sea["sea_surface_temperature"][s] if s is not None else None,
        })
    D = wx["daily"]
    sun = {d: {"rise": D["sunrise"][k][11:16], "set": D["sunset"][k][11:16]} for k, d in enumerate(D["time"])}
    if not any(h["h"] is not None for h in hours):
        sys.exit("no wave data")
    at = datetime.now(timezone(timedelta(hours=7))).isoformat(timespec="minutes")
    json.dump({"at": at, "hours": hours, "sun": sun}, open(out, "w"), ensure_ascii=False, separators=(",", ":"))

if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "surf/data.json")
