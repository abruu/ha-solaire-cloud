"""Turn a Solaire share-page overview into flat sensor values (pure Python, no Home Assistant imports)."""

from __future__ import annotations

import calendar
import math
from datetime import datetime, timedelta
from typing import Any

# Plane tilt/azimuth of the array are assumptions for the clear-sky model.
TILT_DEG = 10
PLANE_AZ_DEG = 180


def num(value: Any) -> float | None:
    """Return value as float if it is a real number, else None."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def today_point(curve: list[dict[str, Any]] | None, key: str) -> float | None:
    """Value of the energyList point whose x axis equals key (MM-DD)."""
    for point in curve or []:
        if point.get("xais") == key:
            return num(point.get("yais"))
    return None


def sun_pos(t: datetime, lat: float, lon: float, tz_h: float) -> tuple[float, float, int]:
    """Elevation, azimuth (deg from north) and day of year for a naive local datetime (NOAA approximation)."""
    n = t.timetuple().tm_yday
    g = 2 * math.pi / 365 * (n - 1 + (t.hour - 12) / 24)
    eqt = 229.18 * (
        0.000075
        + 0.001868 * math.cos(g)
        - 0.032077 * math.sin(g)
        - 0.014615 * math.cos(2 * g)
        - 0.040849 * math.sin(2 * g)
    )
    decl = (
        0.006918
        - 0.399912 * math.cos(g)
        + 0.070257 * math.sin(g)
        - 0.006758 * math.cos(2 * g)
        + 0.000907 * math.sin(2 * g)
        - 0.002697 * math.cos(3 * g)
        + 0.00148 * math.sin(3 * g)
    )
    ha = math.radians((t.hour * 60 + t.minute + eqt + 4 * lon - 60 * tz_h) / 4 - 180)
    la = math.radians(lat)
    cz = max(-1.0, min(1.0, math.sin(la) * math.sin(decl) + math.cos(la) * math.cos(decl) * math.cos(ha)))
    az = (
        math.atan2(math.sin(ha), math.cos(ha) * math.sin(la) - math.tan(decl) * math.cos(la)) + math.pi
    ) % (2 * math.pi)
    return 90 - math.degrees(math.acos(cz)), math.degrees(az), n


def poa_wm2(el: float, az: float, n: int) -> float:
    """Clear-sky plane-of-array irradiance in W/m2 (Haurwitz GHI, 15% diffuse, isotropic sky)."""
    if el <= 0:
        return 0.0
    cz = max(math.sin(math.radians(el)), 0.05)
    ghi = 1098 * cz * math.exp(-0.059 / cz) * (1 + 0.033 * math.cos(2 * math.pi * n / 365))
    dhi = 0.15 * ghi
    dni = (ghi - dhi) / cz
    b = math.radians(TILT_DEG)
    cth = math.sin(math.radians(el)) * math.cos(b) + math.cos(math.radians(el)) * math.sin(b) * math.cos(
        math.radians(az - PLANE_AZ_DEG)
    )
    return dni * max(cth, 0) + dhi * (1 + math.cos(b)) / 2 + ghi * 0.2 * (1 - math.cos(b)) / 2


def clear_curve(day: datetime, lat: float, lon: float, tz_h: float, kwp: float) -> list[int]:
    """288 five-minute ideal AC watts (clipped at the array rating) for the naive local date `day`."""
    out = []
    for i in range(288):
        el, az, n = sun_pos(day + timedelta(minutes=5 * i + 2.5), lat, lon, tz_h)
        out.append(int(round(min(kwp * poa_wm2(el, az, n), kwp * 1000))))
    return out


def stop_analysis(vals: list[float | None]) -> tuple[int, int, float]:
    """Zero readings between the first and last producing slot are inverter stops.

    Returns (stopped_minutes, stop_count, lost_kwh); lost_kwh is a conservative estimate from linear
    interpolation across each stop.
    """
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


def installed_kwp(res: dict[str, Any]) -> float | None:
    """Array rating in kWp as reported by the site."""
    stats = res.get("statisticsInfo") or {}
    return num((stats.get("userScreenInfo") or {}).get("sitePvCapacity"))


def _clock(now: datetime, hhmm: str | None) -> datetime | None:
    """Aware datetime today for an "HH:MM" string from the power curve."""
    if not isinstance(hhmm, str):
        return None
    try:
        hour, minute = (int(p) for p in hhmm.split(":")[:2])
        return now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    except ValueError:
        return None


def build_overview(
    res: dict[str, Any],
    now: datetime,
    model: list[int] | None,
) -> dict[str, Any]:
    """Flatten the getUserOverview `result` into the values the sensors read.

    `now` is aware and in the Home Assistant time zone; `model` is the clear-sky curve for today
    (see clear_curve), or None when the location is unknown.
    """
    stats = res.get("statisticsInfo") or {}
    ov = stats.get("screenOverview") or {}
    us = stats.get("userScreenInfo") or {}
    energy = res.get("energyList") or {}
    curve = (res.get("powerCurve") or {}).get("acPower") or []
    points = [(p.get("xais"), num(p.get("yais"))) for p in curve if num(p.get("yais")) is not None]
    day_key = now.strftime("%m-%d")

    live = [(t, v) for t, v in points if v > 0]
    peak = max(points, key=lambda p: p[1]) if points else (None, None)

    def band(lo: str, hi: str) -> float | None:
        if not points:
            return None
        return round(sum(v for t, v in points if lo <= t < hi) * 5 / 60 / 1000, 2)

    yields = {p.get("xais"): num(p.get("yais")) for p in energy.get("acYield") or []}
    producing = {k: v for k, v in yields.items() if v is not None and v > 0}
    complete = {k: v for k, v in producing.items() if k != day_key}  # today is still in progress
    best = max(producing.items(), key=lambda kv: kv[1]) if producing else (None, None)
    week = [yields.get((now - timedelta(days=i)).strftime("%m-%d")) for i in range(7)]

    def msum(key: str) -> float:
        return round(sum(p["yais"] for p in energy.get(key) or [] if num(p.get("yais")) is not None), 2)

    dim = calendar.monthrange(now.year, now.month)[1]
    month_kwh = num(ov.get("monthYield"))
    avg_day = round(sum(complete.values()) / len(complete), 2) if complete else None
    today_kwh = num(ov.get("todayYield")) or 0.0
    projection = (
        round(month_kwh + avg_day * (dim - now.day) + max(avg_day - today_kwh, 0), 1)
        if avg_day is not None and month_kwh is not None and len(complete) >= 3
        else None
    )
    month_load, month_import = msum("load"), msum("consumedEnergy")
    stop_min, stop_runs, lost_kwh = stop_analysis([num(p.get("yais")) for p in curve])

    return {
        "pv_power_w": num(us.get("realTimePower")),
        "pv_peak_today_w": peak[1],
        "pv_peak_time": _clock(now, peak[0]),
        "first_light_time": _clock(now, live[0][0]) if live else None,
        "last_light_time": _clock(now, live[-1][0]) if live else None,
        "production_hours": round(len(live) * 5 / 60, 2) if points else None,
        "energy_morning_kwh": band("00:00", "10:00"),
        "energy_midday_kwh": band("10:00", "14:00"),
        "energy_afternoon_kwh": band("14:00", "24:00"),
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
    }
