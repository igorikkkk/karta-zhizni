"""Google Health API: login and data probe for the Fitbit Air.

  python3 gh.py auth    one-time Google login, saves token.json
  python3 gh.py probe   newest data points for each type -> probe.json
"""
import json, os, sys, time, subprocess, urllib.parse, urllib.request, urllib.error
from http.server import BaseHTTPRequestHandler, HTTPServer

DIR = os.path.dirname(os.path.abspath(__file__))
SECRET = os.path.join(DIR, "client_secret.json")
TOKEN = os.path.join(DIR, "token.json")
PORT = 8765
REDIRECT = f"http://localhost:{PORT}"
API = "https://health.googleapis.com/v4/users/me"
SCOPES = " ".join("https://www.googleapis.com/auth/googlehealth." + s for s in (
    "activity_and_fitness.readonly",
    "health_metrics_and_measurements.readonly",
    "sleep.readonly",
))
TYPES = [
    "sleep", "steps", "heart-rate", "daily-resting-heart-rate",
    "daily-heart-rate-variability", "daily-oxygen-saturation",
    "daily-respiratory-rate", "exercise", "active-zone-minutes",
    "active-energy-burned", "distance", "daily-vo2-max",
]


def client():
    c = json.load(open(SECRET))
    return c.get("installed") or c["web"]


def post_form(url, data):
    req = urllib.request.Request(url, urllib.parse.urlencode(data).encode())
    return json.load(urllib.request.urlopen(req))


def save_token(tok):
    tok["expires_at"] = time.time() + tok.get("expires_in", 3600) - 60
    with open(TOKEN, "w") as f:
        json.dump(tok, f, indent=1)
    os.chmod(TOKEN, 0o600)


def auth():
    c = client()
    url = "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode({
        "client_id": c["client_id"], "redirect_uri": REDIRECT, "response_type": "code",
        "access_type": "offline", "prompt": "consent", "scope": SCOPES,
    })
    got = {}

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            got.update({k: v[0] for k, v in q.items()})
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write("Готово, окно можно закрыть.".encode())

        def log_message(self, *a):
            pass

    srv = HTTPServer(("0.0.0.0", PORT), H)
    print("Открываю браузер. Если не открылся, скопируй ссылку:\n" + url)
    subprocess.run(["powershell.exe", "-NoProfile", "-Command", f"Start-Process '{url}'"],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    while "code" not in got and "error" not in got:
        srv.handle_request()
    if "error" in got:
        sys.exit("Google вернул ошибку: " + got["error"])
    tok = post_form("https://oauth2.googleapis.com/token", {
        "code": got["code"], "client_id": c["client_id"], "client_secret": c["client_secret"],
        "redirect_uri": REDIRECT, "grant_type": "authorization_code",
    })
    save_token(tok)
    days = tok.get("refresh_token_expires_in", 0) / 86400
    print(f"Вход выполнен. Права: {tok.get('scope')}")
    if days:
        print(f"Refresh token живёт {days:.0f} дн. (тестовый режим Google)")


def access_token():
    tok = json.load(open(TOKEN))
    if time.time() < tok["expires_at"]:
        return tok["access_token"]
    c = client()
    new = post_form("https://oauth2.googleapis.com/token", {
        "refresh_token": tok["refresh_token"], "client_id": c["client_id"],
        "client_secret": c["client_secret"], "grant_type": "refresh_token",
    })
    tok.update(new)
    save_token(tok)
    return tok["access_token"]


def get(path, **params):
    url = f"{API}/{path}" + ("?" + urllib.parse.urlencode(params) if params else "")
    req = urllib.request.Request(url, headers={"Authorization": "Bearer " + access_token()})
    try:
        return json.load(urllib.request.urlopen(req))
    except urllib.error.HTTPError as e:
        return {"error": e.code, "body": e.read().decode()[:500]}


def probe():
    out = {"pairedDevices": get("pairedDevices")}
    for t in TYPES:
        r = get(f"dataTypes/{t}/dataPoints", pageSize=3 if t in ("sleep", "exercise") else 5)
        out[t] = r
        n = len(r.get("dataPoints", []))
        print(f"{t:30} {'ОШИБКА ' + str(r['error']) if 'error' in r else str(n) + ' записей'}")
    path = os.path.join(DIR, "probe.json")
    with open(path, "w") as f:
        json.dump(out, f, indent=1, ensure_ascii=False)
    print("Сырые ответы:", path)


def post(path, body):
    req = urllib.request.Request(f"{API}/{path}", json.dumps(body).encode(), headers={
        "Authorization": "Bearer " + access_token(), "Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req))


def list_all(t, flt):
    out, tok = [], ""
    while True:
        p = {"filter": flt}
        if tok:
            p["pageToken"] = tok
        r = get(f"dataTypes/{t}/dataPoints", **p)
        if "error" in r:
            raise RuntimeError(f"{t}: {r['error']} {r['body']}")
        out += r.get("dataPoints", [])
        tok = r.get("nextPageToken")
        if not tok:
            return out


def local(ts, off):
    """UTC timestamp + '25200s' offset -> local datetime."""
    from datetime import datetime, timedelta
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).replace(tzinfo=None) + timedelta(seconds=int(off.rstrip("s")))


def ymd(d):
    return f"{d['year']:04d}-{d['month']:02d}-{d['day']:02d}"


def sync(days=14):
    """Daily summary for the last `days` days -> health.json (what the Health tab shows)."""
    from datetime import date, timedelta
    end = date.today() + timedelta(days=1)
    start = end - timedelta(days=days)
    cd = lambda d: {"date": {"year": d.year, "month": d.month, "day": d.day}, "time": {}}
    D = {}
    day = lambda k: D.setdefault(k, {})

    def roll(t, fn):
        s = start  # heart-rate allows 14 days per request
        while s < end:
            e = min(s + timedelta(days=14), end)
            for p in post(f"dataTypes/{t}/dataPoints:dailyRollUp", {"range": {"start": cd(s), "end": cd(e)}}).get("rollupDataPoints", []):
                fn(day(ymd(p["civilStartTime"]["date"])), p)
            s = e

    roll("steps", lambda d, p: d.update(steps=int(p["steps"]["countSum"])))
    roll("distance", lambda d, p: d.update(km=round(int(p["distance"]["millimetersSum"]) / 1e6, 2)))
    roll("active-energy-burned", lambda d, p: d.update(kcalAct=round(p["activeEnergyBurned"]["kcalSum"])))
    roll("total-calories", lambda d, p: d.update(kcal=round(p["totalCalories"]["kcalSum"])))
    roll("active-zone-minutes", lambda d, p: d.update(azm={
        k: int(p["activeZoneMinutes"].get(f"sumIn{n}HeartZone", 0)) for k, n in (("fat", "FatBurn"), ("cardio", "Cardio"), ("peak", "Peak"))}))
    roll("heart-rate", lambda d, p: d.update(hr={
        "avg": round(p["heartRate"]["beatsPerMinuteAvg"]), "min": p["heartRate"]["beatsPerMinuteMin"], "max": p["heartRate"]["beatsPerMinuteMax"]}))

    since = start.isoformat()
    for p in list_all("daily-resting-heart-rate", f'daily_resting_heart_rate.date >= "{since}"'):
        x = p["dailyRestingHeartRate"]; day(ymd(x["date"]))["rhr"] = int(x["beatsPerMinute"])
    for p in list_all("daily-heart-rate-variability", f'daily_heart_rate_variability.date >= "{since}"'):
        x = p["dailyHeartRateVariability"]; day(ymd(x["date"]))["hrv"] = round(x["averageHeartRateVariabilityMilliseconds"], 1)
    for p in list_all("daily-oxygen-saturation", f'daily_oxygen_saturation.date >= "{since}"'):
        x = p["dailyOxygenSaturation"]; day(ymd(x["date"]))["spo2"] = {
            "avg": x["averagePercentage"], "lo": x.get("lowerBoundPercentage"), "hi": x.get("upperBoundPercentage")}
    for p in list_all("daily-respiratory-rate", f'daily_respiratory_rate.date >= "{since}"'):
        x = p["dailyRespiratoryRate"]; day(ymd(x["date"]))["resp"] = x["breathsPerMinute"]

    # Sleep is filed under the morning you woke up. Naps add to the total but the main sleep drives the chart.
    for p in list_all("sleep", f'sleep.interval.end_time >= "{since}T00:00:00Z"'):
        s, iv = p["sleep"], p["sleep"]["interval"]
        a, b = local(iv["startTime"], iv["startUtcOffset"]), local(iv["endTime"], iv["endUtcOffset"])
        sm = s.get("summary", {})
        st = {x["type"].lower(): int(x["minutes"]) for x in sm.get("stagesSummary", [])}
        rec = {"start": a.strftime("%H:%M"), "end": b.strftime("%H:%M"), "asleep": int(sm.get("minutesAsleep", 0)),
               "awake": int(sm.get("minutesAwake", 0)), "stages": st,
               "seq": [[int((local(x["startTime"], x["startUtcOffset"]) - a).total_seconds() // 60),
                        int((local(x["endTime"], x["endUtcOffset"]) - a).total_seconds() // 60), x["type"][0]]
                       for x in s.get("stages", [])]}
        d = day(b.date().isoformat())
        if s.get("metadata", {}).get("mainSleep") or "sleep" not in d:
            d["sleep"] = rec
        else:
            d["nap"] = d.get("nap", 0) + rec["asleep"]

    for p in list_all("exercise", f'exercise.interval.civil_start_time >= "{since}T00:00:00"'):
        e, iv = p["exercise"], p["exercise"]["interval"]
        a = local(iv["startTime"], iv["startUtcOffset"]); m = e.get("metricsSummary", {})
        day(a.date().isoformat()).setdefault("ex", []).append({
            "t": a.strftime("%H:%M"), "name": e.get("displayName") or e.get("exerciseType", ""),
            "min": round(int(e.get("activeDuration", "0s").rstrip("s")) / 60), "kcal": m.get("caloriesKcal"),
            "hr": int(m["averageHeartRateBeatsPerMinute"]) if m.get("averageHeartRateBeatsPerMinute") else None,
            "km": round(int(m["distanceMillimeters"]) / 1e6, 2) if m.get("distanceMillimeters") else None})
    for d in D.values():
        d.get("ex", []).sort(key=lambda x: x["t"])

    from datetime import datetime
    out = {"updated": datetime.now().strftime("%Y-%m-%dT%H:%M"), "device": "Fitbit Air",
           "days": {k: D[k] for k in sorted(D) if D[k]}}
    path = os.path.join(DIR, "health.json")
    with open(path, "w") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    od = os.path.join(DIR, "out"); os.makedirs(od, exist_ok=True)
    for k, v in out["days"].items():  # one file per day doc health/<date> in the site DB
        with open(os.path.join(od, k + ".json"), "w") as f:
            json.dump(v, f, ensure_ascii=False)
    with open(os.path.join(od, "meta.json"), "w") as f:
        json.dump({"updated": out["updated"], "device": out["device"]}, f)
    print(f"{len(out['days'])} дн. -> {path}, файлы для базы в {od}")
    return out


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "probe"
    if cmd == "sync":
        sync(int(sys.argv[2]) if len(sys.argv) > 2 else 14)
    else:
        {"auth": auth, "probe": probe}[cmd]()
