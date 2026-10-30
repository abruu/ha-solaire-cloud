#!/usr/bin/env python3
"""Fetch Solaire Cloud public-share overview and print one flat JSON object.

Used by the command_line sensor in packages/solaire.yaml. Exits non-zero (and
prints a short reason to stderr, never the share code) on any failure; with
--soft it prints {"ok": false, "error": ...} and exits 0 instead.

Usage: solaire_fetch.py [--soft] [share-link-or-code]
The share code comes from the argument (the HA dashboard field), else
SOLAIRE_SHARE_CODE, else `solaire_share_code` in <config>/secrets.yaml. Needs pycryptodome or cryptography (both ship with
HA Core).

The share page encrypts its requests with AES-128-CBC. The key and IV are NOT included in this project: see
README.md ("Getting the AES key and IV"). Provide them as SOLAIRE_AES_KEY / SOLAIRE_AES_IV or as `solaire_aes_key` /
`solaire_aes_iv` in <config>/secrets.yaml (16 characters each).

Units in the output: *_w = W, *_kwh = kWh, *_pct = %, *_hours = h, installed_kwp = kWp,
*_time = local HH:MM, power_curve_w = 288 five-minute slots from 00:00 (null = no report).
"""
import base64
import calendar
import math
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timedelta

CONFIG_DIR = os.environ.get("SOLAIRE_CONFIG_DIR", "/config")
BASE = "https://www.solairecloud.com"
PATH = "/mesh/web/v1/public/share/display/houseHold/screen/getUserOverview"

_KEYS = None


def _keys():
    """(key, iv) bytes from the environment or secrets.yaml; set once, never printed."""
    global _KEYS
    if _KEYS is None:
        key, iv = secret("SOLAIRE_AES_KEY", "solaire_aes_key"), secret("SOLAIRE_AES_IV", "solaire_aes_iv")
        if not key or not iv or len(key.encode()) != 16 or len(iv.encode()) != 16:
            fail("AES key/IV missing or not 16 characters (see README: Getting the AES key and IV)")
        _KEYS = (key.encode(), iv.encode())
    return _KEYS


try:
    from Crypto.Cipher import AES
    from Crypto.Util.Padding import pad, unpad

    def _enc(b):
        return AES.new(_keys()[0], AES.MODE_CBC, _keys()[1]).encrypt(pad(b, 16))

    def _dec(b):
        return unpad(AES.new(_keys()[0], AES.MODE_CBC, _keys()[1]).decrypt(b), 16)
except ImportError:
    from cryptography.hazmat.primitives import padding
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    def _enc(b):
        p = padding.PKCS7(128).padder()
        e = Cipher(algorithms.AES(_keys()[0]), modes.CBC(_keys()[1])).encryptor()
        return e.update(p.update(b) + p.finalize()) + e.finalize()

    def _dec(b):
        d = Cipher(algorithms.AES(_keys()[0]), modes.CBC(_keys()[1])).decryptor()
        u = padding.PKCS7(128).unpadder()
        return u.update(d.update(b) + d.finalize()) + u.finalize()


SOFT = False


def fail(msg):
    """Exit non-zero, or in --soft mode print {"ok": false} and exit 0.

    --soft is for the HA command_line sensor: HA logs the whole command line on
    a non-zero exit, and the command carries the share code."""
    if SOFT:
        print(json.dumps({"ok": False, "error": msg}))
        sys.exit(0)
    print(msg, file=sys.stderr)
    sys.exit(1)


def secret(env_name, yaml_key):
    """Value from the environment, else `yaml_key: value` in <config>/secrets.yaml; None if absent."""
    val = os.environ.get(env_name)
    if val:
        return val
    try:
        with open(os.path.join(CONFIG_DIR, "secrets.yaml"), encoding="utf-8") as f:
            for line in f:
                m = re.match(r"^" + re.escape(yaml_key) + r"""\s*:\s*(?:"([^"]*)"|'([^']*)'|([^#\s]+))""", line)
                if m:
                    return next(g for g in m.groups() if g is not None)
    except OSError:
        pass
    return None


# --- back-off: after a failed request wait 1, 2, 4 ... max 10 minutes before the next one -------------
BACKOFF_FILE = os.environ.get("SOLAIRE_BACKOFF_FILE", "/tmp/solaire_backoff.json")


def backoff_wait():
    """Seconds still to wait before a request may be sent (0 = go ahead)."""
    try:
        with open(BACKOFF_FILE, encoding="utf-8") as f:
            return max(0, int(json.load(f)["until"] - time.time()))
    except Exception:
        return 0


def backoff_register(failed):
    """Record a failed request (grow the wait) or a success (clear it)."""
    try:
        if not failed:
            os.remove(BACKOFF_FILE)
            return
        try:
            with open(BACKOFF_FILE, encoding="utf-8") as f:
                fails = int(json.load(f).get("fails", 0))
        except Exception:
            fails = 0
        fails += 1
        with open(BACKOFF_FILE, "w", encoding="utf-8") as f:
            json.dump({"fails": fails, "until": time.time() + min(60 * 2 ** (fails - 1), 600)}, f)
    except OSError:
        pass


def request_fail(msg):
    backoff_register(True)
    fail(msg)


def encrypt(text):
    return base64.b64encode(_enc(text.encode())).decode()


def decrypt(b64):
    return _dec(base64.b64decode(b64)).decode()


def parse_share(value):
    """Share code from a full share link or a bare code; None if neither."""
    value = (value or "").strip()
    m = re.search(r"shareCode=([A-Za-z0-9]{8,64})", value) or re.fullmatch(r"([A-Za-z0-9]{8,64})", value)
    return m.group(1) if m else None


def share_code(arg):
    """Order: command-line arg (link/code pasted on the dashboard), env, secrets.yaml."""
    if arg:
        code = parse_share(arg)
        if not code:
            fail("share link not recognised")
        return code
    code = secret("SOLAIRE_SHARE_CODE", "solaire_share_code")
    if code:
        return code
    fail("solaire_share_code not found in secrets.yaml")


def local_now():
    """Now in the HA-configured time zone (the site's 'today' is local)."""
    try:
        from zoneinfo import ZoneInfo
        with open(os.path.join(CONFIG_DIR, ".storage", "core.config"), encoding="utf-8") as f:
            return datetime.now(ZoneInfo(json.load(f)["data"]["time_zone"]))
    except Exception:
        return datetime.now().astimezone()


def num(v):
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def today_point(curve, key):
    for p in curve or []:
        if p.get("xais") == key:
            return num(p.get("yais"))
    return None


# --- clear-sky model of the array (ideal kWp, performance ratio 1.0) ---------------------------------
# Plane tilt/azimuth are assumptions (adjust to your roof); latitude/longitude come from Home Assistant's
# core.config, and without them the clear-sky fields are null.
TILT_DEG, PLANE_AZ_DEG = 10, 180


def site_coords():
    try:
        with open(os.path.join(CONFIG_DIR, ".storage", "core.config"), encoding="utf-8") as f:
            d = json.load(f)["data"]
        return float(d["latitude"]), float(d["longitude"])
    except Exception:
        return None


def sun_pos(t, lat, lon, tz_h):
    """(elevation deg, azimuth deg from north, day of year) for a naive local datetime (NOAA approximation)."""
    n = t.timetuple().tm_yday
    g = 2 * math.pi / 365 * (n - 1 + (t.hour - 12) / 24)
    eqt = 229.18 * (0.000075 + 0.001868 * math.cos(g) - 0.032077 * math.sin(g)
                    - 0.014615 * math.cos(2 * g) - 0.040849 * math.sin(2 * g))
    decl = (0.006918 - 0.399912 * math.cos(g) + 0.070257 * math.sin(g) - 0.006758 * math.cos(2 * g)
            + 0.000907 * math.sin(2 * g) - 0.002697 * math.cos(3 * g) + 0.00148 * math.sin(3 * g))
    ha = math.radians((t.hour * 60 + t.minute + eqt + 4 * lon - 60 * tz_h) / 4 - 180)
    la = math.radians(lat)
    cz = max(-1.0, min(1.0, math.sin(la) * math.sin(decl) + math.cos(la) * math.cos(decl) * math.cos(ha)))
    az = (math.atan2(math.sin(ha), math.cos(ha) * math.sin(la) - math.tan(decl) * math.cos(la)) + math.pi) % (2 * math.pi)
    return 90 - math.degrees(math.acos(cz)), math.degrees(az), n


def poa_wm2(el, az, n):
    """Clear-sky plane-of-array irradiance, W/m2 (Haurwitz GHI, 15% diffuse, isotropic sky)."""
    if el <= 0:
        return 0.0
    cz = max(math.sin(math.radians(el)), 0.05)
    ghi = 1098 * cz * math.exp(-0.059 / cz) * (1 + 0.033 * math.cos(2 * math.pi * n / 365))
    dhi = 0.15 * ghi
    dni = (ghi - dhi) / cz
    b = math.radians(TILT_DEG)
    cth = (math.sin(math.radians(el)) * math.cos(b)
           + math.cos(math.radians(el)) * math.sin(b) * math.cos(math.radians(az - PLANE_AZ_DEG)))
    return dni * max(cth, 0) + dhi * (1 + math.cos(b)) / 2 + ghi * 0.2 * (1 - math.cos(b)) / 2


def clear_curve(day, lat, lon, tz_h, kwp):
    """288 five-minute ideal AC watts (clipped at the inverter rating) for the local date `day`."""
    out = []
    for i in range(288):
        el, az, n = sun_pos(day + timedelta(minutes=5 * i + 2.5), lat, lon, tz_h)
        out.append(int(round(min(kwp * poa_wm2(el, az, n), kwp * 1000))))
    return out


def stop_analysis(vals):
    """Zero readings between the first and last producing slot are inverter stops (grid voltage cut-offs).

    Returns (stopped_minutes, stop_count, lost_kwh) where lost_kwh is a conservative estimate from linear
    interpolation across each stop."""
    idx = [i for i, v in enumerate(vals) if v is not None and v > 0]
    if len(idx) < 2:
        return 0, 0, 0.0
    a, b = idx[0], idx[-1]
    zeros = [i for i in range(a, b + 1) if vals[i] == 0]
    runs, prev = 0, None
    for i in zeros:
        runs += 0 if prev is not None and i == prev + 1 else 1
        prev = i
    lost = 0.0
    for i in zeros:
        lo = next(j for j in range(i, a - 1, -1) if vals[j])
        hi = next(j for j in range(i, b + 1) if vals[j])
        lost += vals[lo] + (vals[hi] - vals[lo]) * (i - lo) / (hi - lo)
    return len(zeros) * 5, runs, round(lost * 5 / 60 / 1000, 2)


def main():
    global SOFT
    args = sys.argv[1:]
    SOFT = "--soft" in args
    args = [a for a in args if a != "--soft"]
    code = share_code(args[0] if args else None)
    now = local_now()
    body = {
        "curDate": now.strftime("%Y-%m-%d"),
        "year": now.strftime("%Y"),
        "month": now.strftime("%m"),
        "day": now.strftime("%d"),
    }
    query = urllib.parse.urlencode({
        "data": encrypt(urllib.parse.urlencode(
            {"timeStamp": int(time.time() * 1000), "requestId": uuid.uuid4().hex}))
    })
    req = urllib.request.Request(
        f"{BASE}{PATH}?{query}",
        data=json.dumps({"data": encrypt(json.dumps(body, separators=(",", ":")))}).encode(),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json, text/plain, */*",
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) HomeAssistant-Solaire",
            "shareCode": code,
            "sharePageCode": "1000",
            "crytoVer": "1",
            "deviceType": "3",
            "source": "0",
            "Lang": "en_US",
            "queryTime": now.strftime("%Y-%m-%d %H:%M:%S"),
            "Origin": BASE,
            "Referer": BASE + "/",
        },
    )
    wait = backoff_wait()
    if wait:
        fail(f"backing off after errors, next try in {wait}s")
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            outer = json.loads(r.read().decode())
        if isinstance(outer, dict) and isinstance(outer.get("data"), str):
            outer = json.loads(decrypt(outer["data"]))
    except urllib.error.HTTPError as e:
        request_fail(f"HTTP {e.code} from Solaire Cloud")
    except urllib.error.URLError as e:
        request_fail(f"network error: {e.reason}")
    except Exception as e:  # bad JSON / decrypt failure => endpoint changed
        request_fail(f"unexpected response ({type(e).__name__})")

    if not isinstance(outer, dict) or not outer.get("success") or not isinstance(outer.get("result"), dict):
        request_fail(f"API error code={outer.get('code') if isinstance(outer, dict) else '?'}")
    backoff_register(False)
    res = outer["result"]
    stats = res.get("statisticsInfo") or {}
    ov = stats.get("screenOverview") or {}
    us = stats.get("userScreenInfo") or {}
    energy = res.get("energyList") or {}
    curve = (res.get("powerCurve") or {}).get("acPower") or []
    points = [(p.get("xais"), num(p.get("yais"))) for p in curve if num(p.get("yais")) is not None]
    day_key = now.strftime("%m-%d")

    # --- today's 5-minute curve (W): timing and energy split ---
    live = [(t, v) for t, v in points if v > 0]
    peak = max(points, key=lambda p: p[1]) if points else (None, None)
    band = lambda lo, hi: round(sum(v for t, v in points if lo <= t < hi) * 5 / 60 / 1000, 2) if points else None
    # --- month history from energyList (daily kWh, null = no data) ---
    yields = {p.get("xais"): num(p.get("yais")) for p in energy.get("acYield") or []}
    producing = {k: v for k, v in yields.items() if v is not None and v > 0}
    complete = {k: v for k, v in producing.items() if k != day_key}  # today is still in progress
    best = max(producing.items(), key=lambda kv: kv[1]) if producing else (None, None)
    days = [(now - timedelta(days=i)).strftime("%m-%d") for i in range(7)]
    week = [yields.get(k) for k in days]
    msum = lambda key: round(sum(p["yais"] for p in energy.get(key) or [] if num(p.get("yais")) is not None), 2)
    dim = calendar.monthrange(now.year, now.month)[1]
    month_kwh = num(ov.get("monthYield"))
    # averages and projections use completed days only: today's partial total would drag them down
    avg_day = round(sum(complete.values()) / len(complete), 2) if complete else None
    today_kwh = num(ov.get("todayYield")) or 0.0
    # projection only once there are enough completed days to mean something
    projection = (round(month_kwh + avg_day * (dim - now.day) + max(avg_day - today_kwh, 0), 1)
                  if avg_day is not None and month_kwh is not None and len(complete) >= 3 else None)
    month_load, month_import = msum("load"), msum("consumedEnergy")
    coords = site_coords()
    kwp = num(us.get("sitePvCapacity")) or 0.0
    day0 = now.replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=None)
    tz_h = now.utcoffset().total_seconds() / 3600 if now.utcoffset() is not None else 0.0
    model = clear_curve(day0, coords[0], coords[1], tz_h, kwp) if coords else None
    raw_vals = [num(p.get("yais")) for p in curve]
    stop_min, stop_runs, lost_kwh = stop_analysis(raw_vals)

    print(json.dumps({
        "ok": True,
        "fetched_at": now.isoformat(timespec="seconds"),
        "pv_power_w": num(us.get("realTimePower")),
        "pv_curve_last_w": points[-1][1] if points else None,
        "pv_curve_last_time": points[-1][0] if points else None,
        "pv_peak_today_w": peak[1],
        "pv_peak_time": peak[0],
        "first_light_time": live[0][0] if live else None,
        "last_light_time": live[-1][0] if live else None,
        "production_hours": round(len(live) * 5 / 60, 2) if points else None,
        "energy_morning_kwh": band("00:00", "10:00"),
        "energy_midday_kwh": band("10:00", "14:00"),
        "energy_afternoon_kwh": band("14:00", "24:00"),
        "curve_energy_kwh": band("00:00", "24:00"),
        "power_curve_w": [None if num(p.get("yais")) is None else int(round(p["yais"])) for p in curve],
        "clear_curve_w": model,
        "clear_energy_kwh": round(sum(model) * 5 / 60 / 1000, 2) if model else None,
        "stopped_minutes": stop_min,
        "stop_count": stop_runs,
        "lost_kwh_est": lost_kwh,
        "yield_today_kwh": num(ov.get("todayYield")),
        "yield_yesterday_kwh": yields.get((now - timedelta(days=1)).strftime("%m-%d")),
        "yield_7d_kwh": round(sum(v for v in week if v is not None), 1),
        "yield_month_kwh": month_kwh,
        "yield_year_kwh": num(ov.get("yearYield")),
        "yield_total_kwh": num(ov.get("totalYield")),
        "best_day_kwh": best[1],
        "best_day_date": best[0],
        "avg_day_kwh": avg_day,
        "days_producing": len(complete),
        "month_projection_kwh": projection,
        "load_today_kwh": today_point(energy.get("load"), day_key),
        "grid_import_today_kwh": today_point(energy.get("consumedEnergy"), day_key),
        "grid_export_today_kwh": today_point(energy.get("feedInEnergy"), day_key),
        "load_month_kwh": month_load,
        "grid_import_month_kwh": month_import,
        "grid_export_month_kwh": msum("feedInEnergy"),
        "self_sufficiency_month_pct": round(100 * (1 - month_import / month_load), 1) if month_load else None,
        "self_use_today_pct": num(ov.get("selfUseTodayPercent")),
        "equivalent_hours": num(ov.get("equivalentHour")),
        "installed_kwp": num(us.get("sitePvCapacity")),
    }, separators=(",", ":")))

if __name__ == "__main__":
    main()
