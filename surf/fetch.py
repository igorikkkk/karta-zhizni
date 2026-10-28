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
        hours.append({
            "t": t, "h": med(p[1] for p in per), "p": med(p[2] for p in per),
            "dir": med(col("wave_direction", m) for m, _ in MODELS),
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
