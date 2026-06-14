"""
Garmin Connect — fetches recent activities and GPS tracks.
Uses the unofficial garminconnect library (pip install garminconnect).
Credentials: GARMIN_EMAIL + GARMIN_PASSWORD in Streamlit secrets.
"""
import xml.etree.ElementTree as ET


def _get_client(email: str, password: str):
    import garminconnect
    client = garminconnect.Garmin(email, password)
    client.login()
    return client


def get_recent_activities(email: str, password: str, limit: int = 10) -> list[dict]:
    """Return recent activities. Empty list on error."""
    try:
        client = _get_client(email, password)
        raw = client.get_activities(0, limit)
        result = []
        for a in raw:
            dist = (a.get("distance") or 0) / 1000
            dur  = (a.get("duration")  or 0) / 60
            result.append({
                "activity_id":   a.get("activityId"),
                "name":          a.get("activityName", "Entrenamiento"),
                "date":          (a.get("startTimeLocal") or "")[:10],
                "type":          (a.get("activityType") or {}).get("typeKey", "running"),
                "distance_km":   round(dist, 2),
                "duration_min":  round(dur,  1),
                "avg_hr":        a.get("averageHR"),
                "max_hr":        a.get("maxHR"),
                "calories":      a.get("calories"),
                "elevation_gain":a.get("elevationGain"),
                "avg_speed":     a.get("averageSpeed"),
            })
        return result
    except Exception:
        return []


def get_activity_coords(email: str, password: str, activity_id) -> list[tuple[float, float]]:
    """Return [(lat, lon), ...] for a given activity. Empty list on error."""
    try:
        import garminconnect
        client = _get_client(email, password)
        gpx_bytes = client.download_activity(
            activity_id,
            dl_fmt=garminconnect.Garmin.ActivityDownloadFormat.GPX,
        )
        return _parse_gpx(gpx_bytes)
    except Exception:
        return []


def _parse_gpx(gpx_bytes: bytes) -> list[tuple[float, float]]:
    ns_list = [
        "{http://www.topografix.com/GPX/1/1}",
        "{http://www.topografix.com/GPX/1/0}",
        "",
    ]
    try:
        root = ET.fromstring(gpx_bytes)
        for ns in ns_list:
            pts = list(root.iter(f"{ns}trkpt"))
            if pts:
                return [(float(p.get("lat", 0)), float(p.get("lon", 0))) for p in pts]
        return []
    except Exception:
        return []


def pace_str(distance_km: float, duration_min: float) -> str:
    """Format pace as 'M:SS /km'."""
    if not distance_km or not duration_min:
        return "—"
    total_sec = (duration_min * 60) / distance_km
    return f"{int(total_sec // 60)}:{int(total_sec % 60):02d} /km"
