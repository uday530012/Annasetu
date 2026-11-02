"""
FoodLoop AI - Phase 4 Admin & Impact Analytics
Read-only analytics layer. No food/request/notification mutations are performed here.
"""
import os
import re
import sqlite3
from datetime import datetime, timezone, timedelta
from pathlib import Path


def _db_path():
    configured = os.getenv("FOODLOOP_DB_PATH", "").strip()
    if configured:
        return Path(configured)
    try:
        import database
        for attr in ("DB_PATH", "DATABASE_PATH", "DATABASE_FILE"):
            value = getattr(database, attr, None)
            if value:
                return Path(value)
    except Exception:
        pass
    return Path(__file__).resolve().parent / "foodloop.db"


def _connect():
    conn = sqlite3.connect(str(_db_path()))
    conn.row_factory = sqlite3.Row
    return conn


def _tables(conn):
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    return {r[0] for r in rows}


def _columns(conn, table):
    if table not in _tables(conn):
        return set()
    return {r[1] for r in conn.execute(f'PRAGMA table_info("{table}")').fetchall()}


def _pick(cols, *names):
    lower = {c.lower(): c for c in cols}
    for name in names:
        if name.lower() in lower:
            return lower[name.lower()]
    return None


def _rows(conn, table, limit=1000):
    cols = _columns(conn, table)
    if not cols:
        return []
    safe = '"' + table.replace('"', '""') + '"'
    return [dict(r) for r in conn.execute(f"SELECT * FROM {safe} LIMIT ?", (limit,)).fetchall()]


def _num(value):
    m = re.match(r"^\s*(\d+(?:\.\d+)?)", str(value or ""))
    return float(m.group(1)) if m else 0.0


def _parse_dt(value):
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    text = text.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
            try:
                return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
            except Exception:
                pass
    return None


def _status(value):
    return str(value or "").strip().lower()


def _table_stats(conn):
    tables = _tables(conn)
    result = {}
    for name in ("users", "food", "food_requests", "receiver_locations", "notifications"):
        result[name] = {
            "exists": name in tables,
            "count": int(conn.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]) if name in tables else 0,
        }
    return result


def _food_analytics(conn):
    if "food" not in _tables(conn):
        return {"total": 0, "available": 0, "collected": 0, "expired": 0, "rescue": 0, "critical": 0,
                "meals_estimated": 0, "by_type": [], "monthly": [], "rescue_queue": []}
    rows = _rows(conn, "food", 10000)
    cols = _columns(conn, "food")
    status_col = _pick(cols, "status", "food_status")
    expiry_col = _pick(cols, "available_until", "expires_at", "expiry_time")
    name_col = _pick(cols, "food_name", "name")
    qty_col = _pick(cols, "quantity", "qty")
    type_col = _pick(cols, "food_type", "type")
    created_col = _pick(cols, "created_at", "posted_at", "created")

    now = datetime.now(timezone.utc)
    available = collected = expired = rescue = critical = 0
    meals = 0.0
    types = {}
    months = {}
    queue = []

    for row in rows:
        st = _status(row.get(status_col)) if status_col else ""
        expiry = _parse_dt(row.get(expiry_col)) if expiry_col else None
        remaining = None if not expiry else (expiry - now).total_seconds() / 3600
        is_collected = st in {"collected", "completed", "rescued"}
        is_expired = bool(expiry and remaining <= 0) or st == "expired"
        if is_collected:
            collected += 1
            meals += _num(row.get(qty_col)) if qty_col else 0
        elif is_expired:
            expired += 1
        else:
            available += 1
        if remaining is not None and 0 < remaining <= 24 and not is_collected:
            rescue += 1
            if remaining <= 3:
                critical += 1
            queue.append({
                "food_name": str(row.get(name_col) or "Food"),
                "quantity": row.get(qty_col),
                "hours_remaining": round(remaining, 1),
                "urgency": "critical" if remaining <= 3 else ("urgent" if remaining <= 6 else "rescue"),
            })
        ft = str(row.get(type_col) or "Unknown").strip() or "Unknown"
        types[ft] = types.get(ft, 0) + 1
        if created_col:
            dt = _parse_dt(row.get(created_col))
            if dt:
                key = dt.strftime("%Y-%m")
                months[key] = months.get(key, 0) + 1

    queue.sort(key=lambda x: x["hours_remaining"])
    return {
        "total": len(rows), "available": available, "collected": collected, "expired": expired,
        "rescue": rescue, "critical": critical, "meals_estimated": int(round(meals)),
        "by_type": [{"label": k, "value": v} for k, v in sorted(types.items(), key=lambda x: (-x[1], x[0]))],
        "monthly": [{"month": k, "value": months[k]} for k in sorted(months)[-12:]],
        "rescue_queue": queue[:20],
    }


def _user_analytics(conn):
    if "users" not in _tables(conn):
        return {"total": 0, "providers": 0, "receivers": 0, "admins": 0, "recent": []}
    rows = _rows(conn, "users", 10000)
    cols = _columns(conn, "users")
    role_col = _pick(cols, "role", "user_role")
    name_col = _pick(cols, "name", "full_name", "username")
    email_col = _pick(cols, "email")
    created_col = _pick(cols, "created_at", "registered_at")
    providers = sum(_status(r.get(role_col)) == "provider" for r in rows) if role_col else 0
    receivers = sum(_status(r.get(role_col)) == "receiver" for r in rows) if role_col else 0
    admins = 0
    try:
        if "admin_users" in _tables(conn):
            admins = int(conn.execute('SELECT COUNT(*) FROM "admin_users"').fetchone()[0])
    except Exception:
        admins = 0
    recent = []
    for r in rows:
        recent.append({"name": r.get(name_col) if name_col else "User", "email": r.get(email_col) if email_col else "", "role": r.get(role_col) if role_col else "", "created_at": r.get(created_col) if created_col else ""})
    return {"total": len(rows), "providers": providers, "receivers": receivers, "admins": admins, "recent": recent[-10:][::-1]}


def _request_analytics(conn):
    if "food_requests" not in _tables(conn):
        return {"total": 0, "pending": 0, "accepted": 0, "rejected": 0, "collected": 0, "success_rate": 0, "recent": []}
    rows = _rows(conn, "food_requests", 10000)
    cols = _columns(conn, "food_requests")
    status_col = _pick(cols, "status", "request_status")
    food_col = _pick(cols, "food_name", "food")
    receiver_col = _pick(cols, "receiver_name", "receiver")
    requested_col = _pick(cols, "requested_at", "created_at")
    counts = {"pending": 0, "accepted": 0, "rejected": 0, "collected": 0}
    for r in rows:
        st = _status(r.get(status_col)) if status_col else ""
        if st in counts:
            counts[st] += 1
    total = len(rows)
    success = round((counts["collected"] / total) * 100, 1) if total else 0
    recent = [{"receiver": r.get(receiver_col) if receiver_col else "Receiver", "food": r.get(food_col) if food_col else "Food", "status": r.get(status_col) if status_col else "", "requested_at": r.get(requested_col) if requested_col else ""} for r in rows[-10:][::-1]]
    return {"total": total, **counts, "success_rate": success, "recent": recent}


def _notification_analytics(conn):
    if "notifications" not in _tables(conn):
        return {"total": 0, "unread": 0, "ai": 0}
    rows = _rows(conn, "notifications", 10000)
    cols = _columns(conn, "notifications")
    read_col = _pick(cols, "is_read", "read")
    title_col = _pick(cols, "title")
    msg_col = _pick(cols, "message", "body")
    type_col = _pick(cols, "notification_type", "type")
    unread = sum(not bool(r.get(read_col)) for r in rows) if read_col else 0
    ai = 0
    for r in rows:
        text = " ".join(str(r.get(c) or "") for c in (title_col, msg_col, type_col)).lower()
        if "foodloop ai" in text or "ai match" in text or "ai" in str(r.get(type_col) or "").lower():
            ai += 1
    return {"total": len(rows), "unread": unread, "ai": ai}


def _receiver_locations(conn):
    if "receiver_locations" not in _tables(conn):
        return 0
    return int(conn.execute('SELECT COUNT(*) FROM "receiver_locations"').fetchone()[0])



def is_admin_user(user):
    """Check admin authorization using the separate admin_users table.

    The existing users.role field is intentionally NOT changed because
    the current database constrains it to provider/receiver.
    """
    if not user:
        return False

    try:
        user_id = int(user.get("id"))
    except Exception:
        return False

    email = str(user.get("email") or "").strip().lower()
    if not email:
        return False

    try:
        with _connect() as conn:
            tables = _tables(conn)
            if "admin_users" not in tables:
                return False

            row = conn.execute(
                "SELECT 1 FROM admin_users "
                "WHERE user_id=? AND lower(email)=lower(?) LIMIT 1",
                (user_id, email)
            ).fetchone()
            return row is not None
    except Exception:
        return False


def _parse_date_only(value):
    """Parse YYYY-MM-DD for the Phase 4.2 date filter."""
    if not value:
        return None
    text = str(value).strip()
    try:
        return datetime.strptime(text, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _month_start(dt):
    return dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _add_month(dt):
    if dt.month == 12:
        return dt.replace(year=dt.year + 1, month=1, day=1)
    return dt.replace(month=dt.month + 1, day=1)


def _month_labels(start_dt, end_dt):
    start = _month_start(start_dt)
    end = _month_start(end_dt)
    labels = []
    cur = start
    while cur <= end and len(labels) <= 120:
        labels.append(cur.strftime("%Y-%m"))
        cur = _add_month(cur)
    return labels


def _in_range(dt, start_dt, end_dt):
    if dt is None:
        return False
    return start_dt <= dt <= end_dt


def _trend_period(rows, date_col, start_dt, end_dt):
    """Return rows that have a usable date within the selected range."""
    out_rows = []
    for row in rows:
        dt = _parse_dt(row.get(date_col)) if date_col else None
        if _in_range(dt, start_dt, end_dt):
            out_rows.append((row, dt))
    return out_rows


def get_admin_trends(start_date=None, end_date=None):
    """
    Phase 4.2 read-only advanced trends.

    No database schema changes and no mutations.
    The response contains:
      - monthly food posted/rescued/expired/quantity rescued
      - monthly requests/collected/pending
      - monthly user registrations
      - monthly notifications/AI notifications
      - food type distribution for the selected period
      - selected date range
    """
    now = datetime.now(timezone.utc)

    requested_start = _parse_date_only(start_date)
    requested_end = _parse_date_only(end_date)

    if requested_end:
        # End date is inclusive through the end of that day.
        requested_end = requested_end.replace(hour=23, minute=59, second=59, microsecond=999999)

    if requested_start and requested_end and requested_start > requested_end:
        raise ValueError("Start date cannot be after end date.")

    if requested_start:
        start_dt = requested_start
    elif requested_end:
        start_dt = _month_start(requested_end)
        for _ in range(11):
            year = start_dt.year
            month = start_dt.month - 1
            if month == 0:
                year -= 1
                month = 12
            start_dt = start_dt.replace(year=year, month=month, day=1)
    else:
        # Default: last 12 calendar months including the current month.
        start_dt = _month_start(now)

        for _ in range(11):
            year = start_dt.year
            month = start_dt.month - 1
            if month == 0:
                year -= 1
                month = 12
            start_dt = start_dt.replace(year=year, month=month, day=1)

    end_dt = requested_end or now

    labels = _month_labels(start_dt, end_dt)
    posted = {m: 0 for m in labels}
    rescued = {m: 0 for m in labels}
    expired = {m: 0 for m in labels}
    rescued_quantity = {m: 0 for m in labels}

    requests_total = {m: 0 for m in labels}
    requests_collected = {m: 0 for m in labels}
    requests_pending = {m: 0 for m in labels}
    users_registered = {m: 0 for m in labels}
    notifications_total = {m: 0 for m in labels}
    notifications_ai = {m: 0 for m in labels}

    food_types = {}

    with _connect() as conn:
        tables = _tables(conn)

        # FOOD TRENDS
        if "food" in tables:
            rows = _rows(conn, "food", 50000)
            cols = _columns(conn, "food")
            status_col = _pick(cols, "status", "food_status")
            expiry_col = _pick(cols, "available_until", "expires_at", "expiry_time")
            type_col = _pick(cols, "food_type", "type")
            qty_col = _pick(cols, "quantity", "qty")
            created_col = _pick(cols, "created_at", "posted_at", "created")

            for row, dt in _trend_period(rows, created_col, start_dt, end_dt):
                key = dt.strftime("%Y-%m")
                if key not in posted:
                    continue
                posted[key] += 1

                st = _status(row.get(status_col)) if status_col else ""
                expiry = _parse_dt(row.get(expiry_col)) if expiry_col else None
                is_collected = st in {"collected", "completed", "rescued"}
                is_expired = st == "expired" or bool(expiry and expiry <= now and not is_collected)

                if is_collected:
                    rescued[key] += 1
                    rescued_quantity[key] += _num(row.get(qty_col)) if qty_col else 0
                elif is_expired:
                    expired[key] += 1

                ft = str(row.get(type_col) or "Unknown").strip() or "Unknown"
                food_types[ft] = food_types.get(ft, 0) + 1

        # REQUEST TRENDS
        if "food_requests" in tables:
            rows = _rows(conn, "food_requests", 50000)
            cols = _columns(conn, "food_requests")
            status_col = _pick(cols, "status", "request_status")
            date_col = _pick(cols, "requested_at", "created_at", "requested")
            for row, dt in _trend_period(rows, date_col, start_dt, end_dt):
                key = dt.strftime("%Y-%m")
                if key not in requests_total:
                    continue
                requests_total[key] += 1
                st = _status(row.get(status_col)) if status_col else ""
                if st == "collected":
                    requests_collected[key] += 1
                elif st == "pending":
                    requests_pending[key] += 1

        # USER GROWTH
        if "users" in tables:
            rows = _rows(conn, "users", 50000)
            cols = _columns(conn, "users")
            date_col = _pick(cols, "created_at", "registered_at", "created")
            for row, dt in _trend_period(rows, date_col, start_dt, end_dt):
                key = dt.strftime("%Y-%m")
                if key in users_registered:
                    users_registered[key] += 1

        # NOTIFICATION TRENDS
        if "notifications" in tables:
            rows = _rows(conn, "notifications", 50000)
            cols = _columns(conn, "notifications")
            date_col = _pick(cols, "created_at", "sent_at", "timestamp", "created")
            title_col = _pick(cols, "title")
            msg_col = _pick(cols, "message", "body")
            type_col = _pick(cols, "notification_type", "type")
            for row, dt in _trend_period(rows, date_col, start_dt, end_dt):
                key = dt.strftime("%Y-%m")
                if key not in notifications_total:
                    continue
                notifications_total[key] += 1
                text = " ".join(
                    str(row.get(c) or "") for c in (title_col, msg_col, type_col)
                ).lower()
                if "foodloop ai" in text or "ai match" in text or "ai" in str(row.get(type_col) or "").lower():
                    notifications_ai[key] += 1

    # A small KPI layer makes the UI easier to understand.
    total_posted = sum(posted.values())
    total_rescued = sum(rescued.values())
    total_requests = sum(requests_total.values())
    total_collected_requests = sum(requests_collected.values())
    rescue_rate = round((total_rescued / total_posted) * 100, 1) if total_posted else 0
    request_collection_rate = round(
        (total_collected_requests / total_requests) * 100, 1
    ) if total_requests else 0

    return {
        "success": True,
        "generated_at": now.isoformat(),
        "range": {
            "start": start_dt.date().isoformat(),
            "end": end_dt.date().isoformat(),
        },
        "labels": labels,
        "food": {
            "posted": [posted[m] for m in labels],
            "rescued": [rescued[m] for m in labels],
            "expired": [expired[m] for m in labels],
            "rescued_quantity": [round(rescued_quantity[m], 2) for m in labels],
        },
        "requests": {
            "total": [requests_total[m] for m in labels],
            "collected": [requests_collected[m] for m in labels],
            "pending": [requests_pending[m] for m in labels],
        },
        "users": {
            "registered": [users_registered[m] for m in labels],
        },
        "notifications": {
            "total": [notifications_total[m] for m in labels],
            "ai": [notifications_ai[m] for m in labels],
        },
        "food_types": [
            {"label": key, "value": value}
            for key, value in sorted(food_types.items(), key=lambda x: (-x[1], x[0]))
        ],
        "kpis": {
            "posted": total_posted,
            "rescued": total_rescued,
            "rescue_rate": rescue_rate,
            "requests": total_requests,
            "collected_requests": total_collected_requests,
            "request_collection_rate": request_collection_rate,
            "notifications": sum(notifications_total.values()),
            "ai_notifications": sum(notifications_ai.values()),
        },
    }



def _location_key(value):
    """Normalize a free-text pickup/location value for grouping."""
    text = str(value or "").strip()
    text = re.sub(r"\s+", " ", text)
    return text.title() if text else "Unknown / GPS Only"


def _safe_float(value):
    try:
        number = float(value)
        if number != number:
            return None
        return number
    except Exception:
        return None


def _haversine_km(lat1, lon1, lat2, lon2):
    if None in (lat1, lon1, lat2, lon2):
        return None
    try:
        rlat1, rlon1, rlat2, rlon2 = map(math.radians, [lat1, lon1, lat2, lon2])
        dlat = rlat2 - rlat1
        dlon = rlon2 - rlon1
        a = math.sin(dlat / 2) ** 2 + math.cos(rlat1) * math.cos(rlat2) * math.sin(dlon / 2) ** 2
        return 6371.0 * 2 * math.asin(math.sqrt(min(1.0, a)))
    except Exception:
        return None


def _valid_coordinates(lat, lon):
    return lat is not None and lon is not None and -90 <= lat <= 90 and -180 <= lon <= 180


def get_admin_location_analytics(start_date=None, end_date=None, top_n=12):
    """
    Phase 4.3 - Location / Area Analytics.

    Read-only analytics. No database schema changes and no mutations.

    Produces:
      - pickup-area distribution using the stored location text
      - area rescue performance
      - provider area distribution
      - receiver GPS coverage
      - food/receiver coordinate coverage
      - nearest-receiver distance statistics for geo-tagged food
      - coordinate points for a lightweight admin map/scatter view
    """
    now = datetime.now(timezone.utc)
    start_dt = _parse_date_only(start_date)
    end_dt = _parse_date_only(end_date)
    if end_dt:
        end_dt = end_dt.replace(hour=23, minute=59, second=59, microsecond=999999)
    if start_dt and end_dt and start_dt > end_dt:
        raise ValueError("Start date cannot be after end date.")
    if not start_dt and not end_dt:
        # Default to all records because this is an area/network view.
        start_dt = None
        end_dt = now

    try:
        top_n = max(3, min(int(top_n), 50))
    except Exception:
        top_n = 12

    def in_range(value):
        if not start_dt and not end_dt:
            return True
        dt = _parse_dt(value)
        if not dt:
            return False if start_dt else True
        if start_dt and dt < start_dt:
            return False
        if end_dt and dt > end_dt:
            return False
        return True

    with _connect() as conn:
        tables = _tables(conn)
        food_rows = _rows(conn, "food", 100000) if "food" in tables else []
        user_rows = _rows(conn, "users", 100000) if "users" in tables else []
        request_rows = _rows(conn, "food_requests", 100000) if "food_requests" in tables else []
        receiver_rows = _rows(conn, "receiver_locations", 100000) if "receiver_locations" in tables else []

    food_cols = set()
    # Avoid holding an extra connection open.
    if "food" in tables:
        with _connect() as conn:
            food_cols = _columns(conn, "food")
            request_cols = _columns(conn, "food_requests") if "food_requests" in tables else set()
            receiver_cols = _columns(conn, "receiver_locations") if "receiver_locations" in tables else set()
    else:
        request_cols = set()
        receiver_cols = set()

    food_created = _pick(food_cols, "created_at", "posted_at", "created")
    food_location = _pick(food_cols, "location", "pickup_location", "address")
    food_name = _pick(food_cols, "food_name", "name")
    food_qty = _pick(food_cols, "quantity", "qty")
    food_status = _pick(food_cols, "status", "food_status")
    food_lat = _pick(food_cols, "latitude", "lat")
    food_lon = _pick(food_cols, "longitude", "lon", "lng")
    food_provider = _pick(food_cols, "provider_name", "provider")
    food_provider_id = _pick(food_cols, "provider_id")
    food_expiry = _pick(food_cols, "available_until", "expires_at", "expiry_time")

    request_food_id = _pick(request_cols, "food_id")
    request_status = _pick(request_cols, "status", "request_status")
    request_created = _pick(request_cols, "created_at", "requested_at", "requested")

    receiver_id_col = _pick(receiver_cols, "receiver_id")
    receiver_name_col = _pick(receiver_cols, "receiver_name", "name")
    receiver_lat_col = _pick(receiver_cols, "latitude", "lat")
    receiver_lon_col = _pick(receiver_cols, "longitude", "lon", "lng")
    receiver_updated_col = _pick(receiver_cols, "updated_at", "created_at")

    # Request lookup: collected requests are counted against the food listing.
    request_by_food = {}
    for r in request_rows:
        if request_created and not in_range(r.get(request_created)):
            continue
        fid = r.get(request_food_id) if request_food_id else None
        try:
            fid = int(fid)
        except Exception:
            continue
        st = _status(r.get(request_status)) if request_status else ""
        if fid not in request_by_food:
            request_by_food[fid] = {"total": 0, "collected": 0}
        request_by_food[fid]["total"] += 1
        if st == "collected":
            request_by_food[fid]["collected"] += 1

    areas = {}
    provider_areas = {}
    food_points = []
    eligible_food = []
    total_quantity = 0.0
    rescued_quantity = 0.0
    expired_quantity = 0.0
    geo_food = 0
    collected_food = 0
    expired_food = 0

    for row in food_rows:
        if food_created and not in_range(row.get(food_created)):
            continue
        key = _location_key(row.get(food_location) if food_location else None)
        entry = areas.setdefault(key, {
            "area": key, "food_listings": 0, "available": 0, "rescued": 0,
            "expired": 0, "requests": 0, "collected_requests": 0,
            "estimated_quantity": 0.0, "rescued_quantity": 0.0,
            "latitude": None, "longitude": None,
        })
        entry["food_listings"] += 1
        qty = _num(row.get(food_qty)) if food_qty else 0
        entry["estimated_quantity"] += qty
        total_quantity += qty

        st = _status(row.get(food_status)) if food_status else ""
        expiry = _parse_dt(row.get(food_expiry)) if food_expiry else None
        is_collected = st in {"collected", "completed", "rescued"}
        is_expired = st == "expired" or bool(expiry and expiry <= now and not is_collected)
        if is_collected:
            entry["rescued"] += 1
            entry["rescued_quantity"] += qty
            rescued_quantity += qty
            collected_food += 1
        elif is_expired:
            entry["expired"] += 1
            expired_quantity += qty
            expired_food += 1
        else:
            entry["available"] += 1
            eligible_food.append(row)

        try:
            fid = int(row.get("id"))
            rq = request_by_food.get(fid, {})
            entry["requests"] += int(rq.get("total", 0))
            entry["collected_requests"] += int(rq.get("collected", 0))
        except Exception:
            pass

        lat = _safe_float(row.get(food_lat)) if food_lat else None
        lon = _safe_float(row.get(food_lon)) if food_lon else None
        if _valid_coordinates(lat, lon):
            geo_food += 1
            if entry["latitude"] is None:
                entry["latitude"] = round(lat, 6)
                entry["longitude"] = round(lon, 6)
            food_points.append({
                "id": row.get("id"),
                "name": str(row.get(food_name) or "Food"),
                "area": key,
                "latitude": lat,
                "longitude": lon,
                "status": "rescued" if is_collected else ("expired" if is_expired else "available"),
            })

        provider = str(row.get(food_provider) or "Unknown Provider").strip() or "Unknown Provider"
        pentry = provider_areas.setdefault(provider, {"provider": provider, "food_listings": 0, "rescued": 0, "available": 0})
        pentry["food_listings"] += 1
        if is_collected:
            pentry["rescued"] += 1
        elif not is_expired:
            pentry["available"] += 1

    # Receiver network and freshness.
    receiver_points = []
    fresh_receivers = 0
    for row in receiver_rows:
        lat = _safe_float(row.get(receiver_lat_col)) if receiver_lat_col else None
        lon = _safe_float(row.get(receiver_lon_col)) if receiver_lon_col else None
        if not _valid_coordinates(lat, lon):
            continue
        updated = _parse_dt(row.get(receiver_updated_col)) if receiver_updated_col else None
        age_hours = None
        if updated:
            age_hours = max(0.0, (now - updated).total_seconds() / 3600)
            if age_hours <= 24:
                fresh_receivers += 1
        receiver_points.append({
            "id": row.get(receiver_id_col) if receiver_id_col else row.get("id"),
            "name": str(row.get(receiver_name_col) or "Receiver").strip() or "Receiver",
            "latitude": lat,
            "longitude": lon,
            "age_hours": round(age_hours, 1) if age_hours is not None else None,
        })

    # Nearest receiver statistics for geo-tagged food.
    nearest_distances = []
    for point in food_points:
        best = None
        best_receiver = None
        for receiver in receiver_points:
            dist = _haversine_km(point["latitude"], point["longitude"], receiver["latitude"], receiver["longitude"])
            if dist is not None and (best is None or dist < best):
                best = dist
                best_receiver = receiver
        if best is not None:
            nearest_distances.append(best)
            point["nearest_receiver_km"] = round(best, 2)
            point["nearest_receiver"] = best_receiver["name"] if best_receiver else None

    coverage_buckets = {"0-2 km": 0, "2-5 km": 0, "5-10 km": 0, ">10 km": 0, "No receiver": 0}
    for distance in nearest_distances:
        if distance <= 2: coverage_buckets["0-2 km"] += 1
        elif distance <= 5: coverage_buckets["2-5 km"] += 1
        elif distance <= 10: coverage_buckets["5-10 km"] += 1
        else: coverage_buckets[">10 km"] += 1
    for _ in range(max(0, len(food_points) - len(nearest_distances))):
        coverage_buckets["No receiver"] += 1

    area_list = []
    for e in areas.values():
        e["estimated_quantity"] = round(e["estimated_quantity"], 2)
        e["rescued_quantity"] = round(e["rescued_quantity"], 2)
        e["rescue_rate"] = round((e["rescued"] / e["food_listings"]) * 100, 1) if e["food_listings"] else 0
        area_list.append(e)
    area_list.sort(key=lambda x: (-x["food_listings"], -x["rescued"], x["area"]))

    provider_list = list(provider_areas.values())
    provider_list.sort(key=lambda x: (-x["food_listings"], x["provider"]))
    provider_list = provider_list[:top_n]

    avg_nearest = round(sum(nearest_distances) / len(nearest_distances), 2) if nearest_distances else 0
    within_10 = sum(d <= 10 for d in nearest_distances)
    coverage_rate = round((within_10 / len(food_points)) * 100, 1) if food_points else 0

    return {
        "success": True,
        "generated_at": now.isoformat(),
        "range": {
            "start": start_dt.date().isoformat() if start_dt else None,
            "end": end_dt.date().isoformat() if end_dt else None,
        },
        "summary": {
            "areas": len(area_list),
            "food_listings": sum(x["food_listings"] for x in area_list),
            "available_food": sum(x["available"] for x in area_list),
            "rescued_food": collected_food,
            "expired_food": expired_food,
            "total_quantity": round(total_quantity, 2),
            "rescued_quantity": round(rescued_quantity, 2),
            "expired_quantity": round(expired_quantity, 2),
            "geo_tagged_food": geo_food,
            "receiver_locations": len(receiver_points),
            "fresh_receiver_locations": fresh_receivers,
            "avg_nearest_receiver_km": avg_nearest,
            "food_within_10km": within_10,
            "coverage_rate_10km": coverage_rate,
        },
        "areas": area_list[:top_n],
        "providers": provider_list,
        "coverage": [{"label": k, "value": v} for k, v in coverage_buckets.items()],
        "food_points": food_points[:500],
        "receiver_points": receiver_points[:500],
    }


def get_admin_performance_analytics(start_date=None, end_date=None, top_n=20):
    """Phase 4.4 - Provider & Receiver Performance Analytics.

    Read-only analytics. No database schema changes and no mutations.
    """
    now = datetime.now(timezone.utc)
    start_dt = _parse_date_only(start_date)
    end_dt = _parse_date_only(end_date)
    if end_dt:
        end_dt = end_dt.replace(hour=23, minute=59, second=59, microsecond=999999)
    if start_dt and end_dt and start_dt > end_dt:
        raise ValueError("Start date cannot be after end date.")
    try:
        top_n = max(5, min(int(top_n), 100))
    except Exception:
        top_n = 20

    def in_range(value):
        dt = _parse_dt(value)
        if not dt:
            return True if not start_dt else False
        if start_dt and dt < start_dt: return False
        if end_dt and dt > end_dt: return False
        return True

    with _connect() as conn:
        tables = _tables(conn)
        users = _rows(conn, 'users', 100000) if 'users' in tables else []
        foods = _rows(conn, 'food', 100000) if 'food' in tables else []
        reqs = _rows(conn, 'food_requests', 100000) if 'food_requests' in tables else []
        notifs = _rows(conn, 'notifications', 100000) if 'notifications' in tables else []
        receivers = _rows(conn, 'receiver_locations', 100000) if 'receiver_locations' in tables else []
        user_cols = _columns(conn,'users') if 'users' in tables else set()
        food_cols = _columns(conn,'food') if 'food' in tables else set()
        req_cols = _columns(conn,'food_requests') if 'food_requests' in tables else set()
        notif_cols = _columns(conn,'notifications') if 'notifications' in tables else set()
        recv_cols = _columns(conn,'receiver_locations') if 'receiver_locations' in tables else set()

    user_id=_pick(user_cols,'id','user_id'); user_name=_pick(user_cols,'name','username','full_name'); user_email=_pick(user_cols,'email'); user_role=_pick(user_cols,'role','user_role')
    food_id=_pick(food_cols,'id','food_id'); food_provider_id=_pick(food_cols,'provider_id'); food_provider=_pick(food_cols,'provider_name','provider'); food_created=_pick(food_cols,'created_at','posted_at','created'); food_qty=_pick(food_cols,'quantity','qty'); food_status=_pick(food_cols,'status','food_status'); food_expiry=_pick(food_cols,'available_until','expires_at','expiry_time')
    req_id=_pick(req_cols,'id','request_id'); req_food=_pick(req_cols,'food_id'); req_receiver_id=_pick(req_cols,'receiver_id','user_id'); req_receiver_name=_pick(req_cols,'receiver_name','receiver','requester_name'); req_status=_pick(req_cols,'status','request_status'); req_created=_pick(req_cols,'created_at','requested_at','requested'); req_updated=_pick(req_cols,'updated_at','processed_at','completed_at')
    notif_user=_pick(notif_cols,'user_id','receiver_id'); notif_title=_pick(notif_cols,'title'); notif_message=_pick(notif_cols,'message','body'); notif_created=_pick(notif_cols,'created_at','timestamp'); notif_read=_pick(notif_cols,'is_read','read')
    recv_id=_pick(recv_cols,'receiver_id','user_id'); recv_name=_pick(recv_cols,'receiver_name','name'); recv_updated=_pick(recv_cols,'updated_at','created_at')

    user_by_id={}
    for u in users:
        try: uid=int(u.get(user_id)) if user_id else None
        except: uid=None
        if uid is not None: user_by_id[uid]=u

    food_by_id={}
    provider_stats={}
    food_provider_key={}
    for f in foods:
        if food_created and not in_range(f.get(food_created)): continue
        try: fid=int(f.get(food_id)) if food_id else None
        except: fid=None
        pid=None
        try: pid=int(f.get(food_provider_id)) if food_provider_id else None
        except: pass
        pname=str(f.get(food_provider) or (user_by_id.get(pid,{}).get(user_name) if pid else '') or 'Unknown Provider').strip() or 'Unknown Provider'
        key=pid if pid is not None else ('name:'+pname.lower())
        food_provider_key[fid]=key
        food_by_id[fid]=f
        st=_status(f.get(food_status)) if food_status else ''
        expiry=_parse_dt(f.get(food_expiry)) if food_expiry else None
        expired=(st=='expired') or bool(expiry and expiry<=now and st not in {'collected','completed','rescued'})
        collected=st in {'collected','completed','rescued'}
        e=provider_stats.setdefault(key,{'provider_id':pid,'provider':pname,'email':str(user_by_id.get(pid,{}).get(user_email) or ''),'food_listings':0,'available':0,'rescued':0,'expired':0,'quantity':0.0,'rescued_quantity':0.0,'requests':0,'collected_requests':0,'notifications':0,'active_food':0})
        e['food_listings']+=1; e['quantity']+=_num(f.get(food_qty)) if food_qty else 0
        if collected: e['rescued']+=1; e['rescued_quantity']+=_num(f.get(food_qty)) if food_qty else 0
        elif expired: e['expired']+=1
        else: e['available']+=1; e['active_food']+=1

    receiver_stats={}
    for u in users:
        role=_status(u.get(user_role)) if user_role else ''
        if role!='receiver': continue
        try: uid=int(u.get(user_id)) if user_id else None
        except: uid=None
        if uid is None: continue
        receiver_stats[uid]={'receiver_id':uid,'receiver':str(u.get(user_name) or 'Receiver'),'email':str(u.get(user_email) or ''),'requests':0,'pending':0,'accepted':0,'rejected':0,'collected':0,'notifications':0,'active_location':False}
    for r in reqs:
        if req_created and not in_range(r.get(req_created)): continue
        try: rid=int(r.get(req_receiver_id)) if req_receiver_id else None
        except: rid=None
        if rid is None:
            name=str(r.get(req_receiver_name) or 'Receiver').strip() or 'Receiver'
            key='name:'+name.lower()
            if key not in receiver_stats: receiver_stats[key]={'receiver_id':None,'receiver':name,'email':'','requests':0,'pending':0,'accepted':0,'rejected':0,'collected':0,'notifications':0,'active_location':False}
            e=receiver_stats[key]
        else:
            if rid not in receiver_stats:
                u=user_by_id.get(rid,{})
                e=receiver_stats.setdefault(rid,{'receiver_id':rid,'receiver':str(u.get(user_name) or r.get(req_receiver_name) or 'Receiver'),'email':str(u.get(user_email) or ''),'requests':0,'pending':0,'accepted':0,'rejected':0,'collected':0,'notifications':0,'active_location':False})
            e=receiver_stats[rid]
        st=_status(r.get(req_status)) if req_status else ''
        e['requests']+=1
        if st=='pending': e['pending']+=1
        elif st=='accepted': e['accepted']+=1
        elif st=='rejected': e['rejected']+=1
        elif st in {'collected','completed'}: e['collected']+=1

        try:
            fid=int(r.get(req_food)) if req_food else None
        except: fid=None
        if fid in food_provider_key and food_provider_key[fid] in provider_stats:
            provider_stats[food_provider_key[fid]]['requests']+=1
            if st in {'collected','completed'}: provider_stats[food_provider_key[fid]]['collected_requests']+=1

    for n in notifs:
        if notif_created and not in_range(n.get(notif_created)): continue
        try: uid=int(n.get(notif_user)) if notif_user else None
        except: uid=None
        is_ai=('ai' in (str(n.get(notif_title) if notif_title else '')+' '+str(n.get(notif_message) if notif_message else '')).lower())
        if uid in receiver_stats and is_ai: receiver_stats[uid]['notifications']+=1
        if uid in provider_stats and is_ai: provider_stats[uid]['notifications']+=1

    # Receiver location freshness: active if updated within 24 hours.
    for loc in receivers:
        try: rid=int(loc.get(recv_id)) if recv_id else None
        except: rid=None
        if rid in receiver_stats:
            dt=_parse_dt(loc.get(recv_updated)) if recv_updated else None
            receiver_stats[rid]['active_location']=bool(dt and (now-dt).total_seconds() <= 86400)

    providers=[]
    for e in provider_stats.values():
        e['rescue_rate']=round(e['rescued']/e['food_listings']*100,1) if e['food_listings'] else 0
        e['request_conversion']=round(e['collected_requests']/e['requests']*100,1) if e['requests'] else 0
        e['avg_quantity']=round(e['quantity']/e['food_listings'],1) if e['food_listings'] else 0
        e['performance_score']=round(min(100, e['rescue_rate']*0.6 + e['request_conversion']*0.4),1)
        providers.append(e)
    providers.sort(key=lambda x:(x['performance_score'],x['rescued'],x['food_listings']),reverse=True)

    receivers_out=[]
    for e in receiver_stats.values():
        completed=e['collected']; total=e['requests']
        e['success_rate']=round(completed/total*100,1) if total else 0
        e['acceptance_rate']=round((e['accepted']+e['collected'])/total*100,1) if total else 0
        e['performance_score']=round(min(100, e['success_rate']*0.7 + e['acceptance_rate']*0.3),1)
        receivers_out.append(e)
    receivers_out.sort(key=lambda x:(x['performance_score'],x['collected'],x['requests']),reverse=True)

    total_provider_food=sum(x['food_listings'] for x in providers)
    total_provider_rescued=sum(x['rescued'] for x in providers)
    total_requests=sum(x['requests'] for x in receivers_out)
    total_collected=sum(x['collected'] for x in receivers_out)
    return {'success':True,'generated_at':now.isoformat(),'range':{'start':start_dt.date().isoformat() if start_dt else None,'end':end_dt.date().isoformat() if end_dt else None},'summary':{'providers':len(providers),'receivers':len(receivers_out),'food_listings':total_provider_food,'rescued_food':total_provider_rescued,'requests':total_requests,'collected_requests':total_collected,'overall_collection_rate':round(total_collected/total_requests*100,1) if total_requests else 0,'top_provider':providers[0]['provider'] if providers else None,'top_receiver':receivers_out[0]['receiver'] if receivers_out else None},'providers':providers[:top_n],'receivers':receivers_out[:top_n]}

def get_admin_dashboard():
    try:
        with _connect() as conn:
            food = _food_analytics(conn)
            users = _user_analytics(conn)
            requests = _request_analytics(conn)
            notifications = _notification_analytics(conn)
            locations = _receiver_locations(conn)
            return {
                "success": True,
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "food": food,
                "users": users,
                "requests": requests,
                "notifications": notifications,
                "receiver_locations": locations,
                "impact": {
                    "food_rescued": food["collected"],
                    "meals_estimated": food["meals_estimated"],
                    "collection_success_rate": requests["success_rate"],
                    "active_receiver_locations": locations,
                    "rescue_queue_size": len(food["rescue_queue"]),
                },
                "tables": _table_stats(conn),
            }
    except Exception as exc:
        return {"success": False, "message": f"Admin analytics failed: {exc}"}



# ============================================================
# PHASE 4.6 - DATE-RANGE REPORTS & PERIOD COMPARISON
# ============================================================

def _resolve_report_range(start_date=None, end_date=None, preset=None):
    """Resolve a safe inclusive UTC date range for Phase 4.6 reports."""
    now = datetime.now(timezone.utc)
    today = now.replace(hour=23, minute=59, second=59, microsecond=999999)
    preset = str(preset or "").strip().lower()

    if preset:
        if preset == "today":
            start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            return start, today, "Today"
        if preset == "yesterday":
            end = now.replace(hour=0, minute=0, second=0, microsecond=0)
            start = end.replace(hour=0) - timedelta(days=1)
            end = end - timedelta(microseconds=1)
            return start, end, "Yesterday"
        if preset == "last_7_days":
            start = (now - timedelta(days=6)).replace(hour=0, minute=0, second=0, microsecond=0)
            return start, today, "Last 7 Days"
        if preset == "last_30_days":
            start = (now - timedelta(days=29)).replace(hour=0, minute=0, second=0, microsecond=0)
            return start, today, "Last 30 Days"
        if preset == "this_month":
            start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            return start, today, "This Month"
        if preset == "last_month":
            first_this = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            last_month_end = first_this - timedelta(microseconds=1)
            start = last_month_end.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            return start, last_month_end, "Last Month"
        if preset == "this_year":
            start = now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
            return start, today, "This Year"
        if preset == "all_time":
            return None, None, "All Time"
        raise ValueError("Unknown date-range preset.")

    start = _parse_date_only(start_date)
    end = _parse_date_only(end_date)
    if end:
        end = end.replace(hour=23, minute=59, second=59, microsecond=999999)
    if start and end and start > end:
        raise ValueError("Start date cannot be after end date.")
    if start or end:
        label = f"{start.date().isoformat() if start else 'Beginning'} → {end.date().isoformat() if end else 'Today'}"
        return start, end, label
    return None, None, "All Time"


def _range_contains(dt, start_dt, end_dt):
    if dt is None:
        return False
    if start_dt and dt < start_dt:
        return False
    if end_dt and dt > end_dt:
        return False
    return True


def _pct_change(current, previous):
    try:
        current = float(current or 0)
        previous = float(previous or 0)
    except Exception:
        return 0
    if previous == 0:
        return 100.0 if current > 0 else 0.0
    return round((current - previous) / previous * 100, 1)


def _range_summary(start_dt=None, end_dt=None):
    """Read-only KPI summary calculated strictly inside the selected range."""
    now = datetime.now(timezone.utc)
    with _connect() as conn:
        tables = _tables(conn)
        foods = _rows(conn, "food", 20000) if "food" in tables else []
        reqs = _rows(conn, "food_requests", 20000) if "food_requests" in tables else []
        users = _rows(conn, "users", 20000) if "users" in tables else []
        notifs = _rows(conn, "notifications", 20000) if "notifications" in tables else []

    try:
        with _connect() as conn:
            food_cols = _columns(conn, "food") if "food" in tables else set()
            req_cols = _columns(conn, "food_requests") if "food_requests" in tables else set()
            user_cols = _columns(conn, "users") if "users" in tables else set()
            notif_cols = _columns(conn, "notifications") if "notifications" in tables else set()
    except Exception:
        req_cols = user_cols = notif_cols = set()

    food_created = _pick(food_cols, "created_at", "posted_at", "created")
    food_status = _pick(food_cols, "status", "food_status")
    food_qty = _pick(food_cols, "quantity", "qty")
    food_expiry = _pick(food_cols, "available_until", "expires_at", "expiry_time")
    req_created = _pick(req_cols, "requested_at", "created_at", "requested")
    req_status = _pick(req_cols, "status", "request_status")
    user_created = _pick(user_cols, "created_at", "registered_at")
    user_role = _pick(user_cols, "role", "user_role")
    notif_created = _pick(notif_cols, "created_at", "timestamp")
    notif_title = _pick(notif_cols, "title")
    notif_message = _pick(notif_cols, "message", "body")
    notif_type = _pick(notif_cols, "notification_type", "type")

    posted = rescued = expired = available = 0
    quantity_posted = quantity_rescued = 0.0
    requests = accepted = rejected = collected = pending = 0
    providers = receivers = new_users = 0
    notifications = ai_notifications = unread_notifications = 0

    for row in foods:
        dt = _parse_dt(row.get(food_created)) if food_created else None
        if not _range_contains(dt, start_dt, end_dt):
            continue
        posted += 1
        qty = _num(row.get(food_qty)) if food_qty else 0
        quantity_posted += qty
        st = _status(row.get(food_status)) if food_status else ""
        expiry = _parse_dt(row.get(food_expiry)) if food_expiry else None
        is_collected = st in {"collected", "completed", "rescued"}
        is_expired = st == "expired" or bool(expiry and expiry <= now and not is_collected)
        if is_collected:
            rescued += 1
            quantity_rescued += qty
        elif is_expired:
            expired += 1
        else:
            available += 1

    for row in reqs:
        dt = _parse_dt(row.get(req_created)) if req_created else None
        if not _range_contains(dt, start_dt, end_dt):
            continue
        requests += 1
        st = _status(row.get(req_status)) if req_status else ""
        if st == "pending": pending += 1
        elif st == "accepted": accepted += 1
        elif st == "rejected": rejected += 1
        elif st in {"collected", "completed"}: collected += 1

    for row in users:
        dt = _parse_dt(row.get(user_created)) if user_created else None
        if not _range_contains(dt, start_dt, end_dt):
            continue
        new_users += 1
        role = _status(row.get(user_role)) if user_role else ""
        if role == "provider": providers += 1
        elif role == "receiver": receivers += 1

    for row in notifs:
        dt = _parse_dt(row.get(notif_created)) if notif_created else None
        if not _range_contains(dt, start_dt, end_dt):
            continue
        notifications += 1
        text = " ".join(str(row.get(c) or "") for c in (notif_title, notif_message, notif_type)).lower()
        if "ai" in text or "foodloop ai" in text:
            ai_notifications += 1
        read_col = _pick(notif_cols, "is_read", "read")
        if read_col and not bool(row.get(read_col)):
            unread_notifications += 1

    return {
        "food_listings": posted,
        "food_rescued": rescued,
        "food_expired": expired,
        "food_available": available,
        "quantity_posted": round(quantity_posted, 2),
        "quantity_rescued": round(quantity_rescued, 2),
        "requests": requests,
        "accepted_requests": accepted,
        "rejected_requests": rejected,
        "pending_requests": pending,
        "collected_requests": collected,
        "collection_rate": round(collected / requests * 100, 1) if requests else 0,
        "new_users": new_users,
        "new_providers": providers,
        "new_receivers": receivers,
        "notifications": notifications,
        "ai_notifications": ai_notifications,
        "unread_notifications": unread_notifications,
        "rescue_rate": round(rescued / posted * 100, 1) if posted else 0,
    }


def _daily_range_trend(start_dt, end_dt):
    """Daily report series for the selected period, capped at 366 days."""
    if not start_dt or not end_dt:
        return []
    from datetime import timedelta
    days = (end_dt.date() - start_dt.date()).days + 1
    if days <= 0 or days > 366:
        return []
    result = {}
    cur = start_dt.date()
    while cur <= end_dt.date():
        key = cur.isoformat()
        result[key] = {"date": key, "posted": 0, "rescued": 0, "requests": 0, "collected": 0}
        cur += timedelta(days=1)

    with _connect() as conn:
        tables = _tables(conn)
        foods = _rows(conn, "food", 20000) if "food" in tables else []
        reqs = _rows(conn, "food_requests", 20000) if "food_requests" in tables else []
        food_cols = _columns(conn, "food") if "food" in tables else set()
        req_cols = _columns(conn, "food_requests") if "food_requests" in tables else set()

    fc = _pick(food_cols, "created_at", "posted_at", "created")
    fs = _pick(food_cols, "status", "food_status")
    rc = _pick(req_cols, "requested_at", "created_at", "requested")
    rs = _pick(req_cols, "status", "request_status")
    for row in foods:
        dt = _parse_dt(row.get(fc)) if fc else None
        if not _range_contains(dt, start_dt, end_dt): continue
        key = dt.date().isoformat()
        if key in result:
            result[key]["posted"] += 1
            if _status(row.get(fs)) in {"collected", "completed", "rescued"}: result[key]["rescued"] += 1
    for row in reqs:
        dt = _parse_dt(row.get(rc)) if rc else None
        if not _range_contains(dt, start_dt, end_dt): continue
        key = dt.date().isoformat()
        if key in result:
            result[key]["requests"] += 1
            if _status(row.get(rs)) in {"collected", "completed"}: result[key]["collected"] += 1
    return list(result.values())


def get_admin_date_range_report(start_date=None, end_date=None, preset=None, compare=True, top_n=20):
    """Phase 4.6: date-range report with previous-period comparison."""
    from datetime import timedelta
    start_dt, end_dt, label = _resolve_report_range(start_date, end_date, preset)
    current = _range_summary(start_dt, end_dt)

    performance = get_admin_performance_analytics(
        start_date=start_dt.date().isoformat() if start_dt else None,
        end_date=end_dt.date().isoformat() if end_dt else None,
        top_n=min(max(int(top_n), 5), 100),
    )
    trends = get_admin_trends(
        start_date=start_dt.date().isoformat() if start_dt else None,
        end_date=end_dt.date().isoformat() if end_dt else None,
    )

    previous = None
    previous_range = {"start": None, "end": None}
    comparison = {}
    if compare and start_dt and end_dt:
        span_days = (end_dt.date() - start_dt.date()).days + 1
        prev_end = start_dt - timedelta(microseconds=1)
        prev_start = (start_dt - timedelta(days=span_days)).replace(hour=0, minute=0, second=0, microsecond=0)
        previous = _range_summary(prev_start, prev_end)
        previous_range = {"start": prev_start.date().isoformat(), "end": prev_end.date().isoformat()}
        for key in ("food_listings", "food_rescued", "food_expired", "requests", "collected_requests", "new_users", "ai_notifications", "quantity_rescued", "collection_rate", "rescue_rate"):
            comparison[key] = {
                "current": current.get(key, 0),
                "previous": previous.get(key, 0),
                "change_percent": _pct_change(current.get(key, 0), previous.get(key, 0)),
            }

    return {
        "success": True,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "range": {"start": start_dt.date().isoformat() if start_dt else None, "end": end_dt.date().isoformat() if end_dt else None, "label": label, "days": ((end_dt.date() - start_dt.date()).days + 1) if start_dt and end_dt else None},
        "summary": current,
        "previous_range": previous_range,
        "previous_summary": previous,
        "comparison": comparison,
        "daily": _daily_range_trend(start_dt, end_dt),
        "performance": performance if isinstance(performance, dict) else {},
        "trends": trends if isinstance(trends, dict) else {},
    }

# ============================================================
# PHASE 4.5 - IMPACT REPORTING & EXPORT
# ============================================================

def get_admin_impact_report(start_date=None, end_date=None, top_n=20):
    """
    Read-only Phase 4.5 reporting layer.

    Combines the existing Phase 4 dashboard, trend, location and
    provider/receiver performance analytics into one report payload.
    No database schema changes and no operational mutations.
    """
    try:
        dashboard = get_admin_dashboard()
        trends = get_admin_trends(start_date=start_date, end_date=end_date)
        location = get_admin_location_analytics(
            start_date=start_date,
            end_date=end_date,
            top_n=min(max(int(top_n), 5), 50),
        )
        performance = get_admin_performance_analytics(
            start_date=start_date,
            end_date=end_date,
            top_n=min(max(int(top_n), 5), 100),
        )

        return {
            "success": True,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "range": {
                "start": start_date or None,
                "end": end_date or None,
            },
            "dashboard": dashboard if isinstance(dashboard, dict) else {},
            "trends": trends if isinstance(trends, dict) else {},
            "location": location if isinstance(location, dict) else {},
            "performance": performance if isinstance(performance, dict) else {},
        }
    except Exception as exc:
        return {
            "success": False,
            "message": f"Impact report generation failed: {exc}",
        }


# ============================================================
# PHASE 4.7 - ADMIN CONTROL CENTER & IMPACT INTELLIGENCE
# ============================================================

def _safe_table_count(conn, table_name):
    try:
        row = conn.execute(f"SELECT COUNT(*) AS c FROM {table_name}").fetchone()
        return int(row["c"] if hasattr(row, "keys") else row[0])
    except Exception:
        return 0


def _quantity_number(value):
    """Extract the leading positive numeric amount from values such as '10 plates'."""
    try:
        m = re.match(r"^\s*(\d+(?:\.\d+)?)", str(value or ""))
        return float(m.group(1)) if m else 0.0
    except Exception:
        return 0.0


def get_admin_data_quality():
    """Read-only data-quality checks for administrators."""
    conn = _connect()
    try:
        food_rows = _rows(conn, "food", 100000)
        request_rows = _rows(conn, "food_requests", 100000)
        user_rows = _rows(conn, "users", 100000)
        receiver_rows = _rows(conn, "receiver_locations", 100000)

        food_missing_coords = 0
        food_missing_expiry = 0
        food_invalid_quantity = 0
        for row in food_rows:
            lat = row.get("latitude")
            lon = row.get("longitude")
            if lat is None or lon is None or not _valid_coordinates(lat, lon):
                food_missing_coords += 1
            if not str(row.get("available_until") or "").strip():
                food_missing_expiry += 1
            if _quantity_number(row.get("quantity")) <= 0:
                food_invalid_quantity += 1

        user_missing_email = sum(1 for r in user_rows if not str(r.get("email") or "").strip())
        user_invalid_role = sum(1 for r in user_rows if str(r.get("role") or "").lower() not in {"provider", "receiver"})

        orphan_requests = 0
        food_ids = {int(r.get("id")) for r in food_rows if str(r.get("id") or "").isdigit()}
        for r in request_rows:
            try:
                if int(r.get("food_id")) not in food_ids:
                    orphan_requests += 1
            except Exception:
                orphan_requests += 1

        stale_locations = 0
        now = datetime.now(timezone.utc)
        for r in receiver_rows:
            dt = _parse_dt(r.get("updated_at"))
            if dt is not None:
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                if (now - dt).total_seconds() > 24 * 3600:
                    stale_locations += 1

        checks = [
            {"name": "Food pickup coordinates", "bad": food_missing_coords, "total": len(food_rows), "severity": "warning" if food_missing_coords else "ok"},
            {"name": "Food expiry/availability time", "bad": food_missing_expiry, "total": len(food_rows), "severity": "warning" if food_missing_expiry else "ok"},
            {"name": "Food quantity", "bad": food_invalid_quantity, "total": len(food_rows), "severity": "critical" if food_invalid_quantity else "ok"},
            {"name": "User email", "bad": user_missing_email, "total": len(user_rows), "severity": "critical" if user_missing_email else "ok"},
            {"name": "User role", "bad": user_invalid_role, "total": len(user_rows), "severity": "critical" if user_invalid_role else "ok"},
            {"name": "Food requests integrity", "bad": orphan_requests, "total": len(request_rows), "severity": "critical" if orphan_requests else "ok"},
            {"name": "Receiver location freshness", "bad": stale_locations, "total": len(receiver_rows), "severity": "warning" if stale_locations else "ok"},
        ]
        critical = sum(1 for c in checks if c["severity"] == "critical" and c["bad"] > 0)
        warnings = sum(1 for c in checks if c["severity"] == "warning" and c["bad"] > 0)
        return {"success": True, "checks": checks, "critical": critical, "warnings": warnings, "healthy": critical == 0}
    finally:
        conn.close()


def get_admin_control_center():
    """Phase 4.7 read-only admin control and impact overview."""
    conn = _connect()
    try:
        food = _rows(conn, "food", 100000)
        requests = _rows(conn, "food_requests", 100000)
        users = _rows(conn, "users", 100000)
        notifications = _rows(conn, "notifications", 100000)
        locations = _rows(conn, "receiver_locations", 100000)

        available = 0
        collected_food = 0
        expired_food = 0
        total_quantity = 0.0
        rescued_quantity = 0.0
        for row in food:
            status = _status(row.get("status"))
            qty = _quantity_number(row.get("quantity"))
            total_quantity += qty
            if status in {"available", "pending"}:
                available += 1
            if status == "collected":
                collected_food += 1
                rescued_quantity += qty
            if status == "expired":
                expired_food += 1

        collected_requests = sum(1 for r in requests if _status(r.get("status")) == "collected")
        accepted_requests = sum(1 for r in requests if _status(r.get("status")) == "accepted")
        rejected_requests = sum(1 for r in requests if _status(r.get("status")) == "rejected")
        pending_requests = sum(1 for r in requests if _status(r.get("status")) == "pending")
        request_total = len(requests)
        collection_rate = round((collected_requests / request_total) * 100, 2) if request_total else 0
        rescue_rate = round((collected_food / len(food)) * 100, 2) if food else 0
        location_coverage = round((sum(1 for r in food if _valid_coordinates(r.get("latitude"), r.get("longitude"))) / len(food)) * 100, 2) if food else 0

        providers = sum(1 for u in users if str(u.get("role") or "").lower() == "provider")
        receivers = sum(1 for u in users if str(u.get("role") or "").lower() == "receiver")
        ai_notifications = sum(1 for n in notifications if str(n.get("notification_type") or n.get("type") or "").lower() in {"ai", "ai_notification", "ai_match", "agent"})

        quality = get_admin_data_quality()
        system_score = max(0, round(100 - quality.get("critical", 0) * 20 - quality.get("warnings", 0) * 5, 2))

        # A transparent operational impact index, not a scientific estimate.
        impact_index = round(
            (rescue_rate * 0.45) +
            (collection_rate * 0.30) +
            (location_coverage * 0.15) +
            (min(100, len(locations) * 10) * 0.10),
            2,
        )

        return {
            "success": True,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "system": {
                "database": "connected",
                "system_score": system_score,
                "data_quality_critical": quality.get("critical", 0),
                "data_quality_warnings": quality.get("warnings", 0),
                "tables": {
                    "users": _safe_table_count(conn, "users"),
                    "food": _safe_table_count(conn, "food"),
                    "food_requests": _safe_table_count(conn, "food_requests"),
                    "notifications": _safe_table_count(conn, "notifications"),
                    "receiver_locations": _safe_table_count(conn, "receiver_locations"),
                },
            },
            "impact": {
                "impact_index": impact_index,
                "food_listings": len(food),
                "food_available": available,
                "food_rescued": collected_food,
                "food_expired": expired_food,
                "quantity_posted": round(total_quantity, 2),
                "quantity_rescued": round(rescued_quantity, 2),
                "estimated_meals_rescued": round(rescued_quantity, 2),
                "rescue_rate": rescue_rate,
                "request_total": request_total,
                "requests_collected": collected_requests,
                "requests_accepted": accepted_requests,
                "requests_rejected": rejected_requests,
                "requests_pending": pending_requests,
                "collection_rate": collection_rate,
                "providers": providers,
                "receivers": receivers,
                "receiver_locations": len(locations),
                "ai_notifications": ai_notifications,
            },
            "quality": quality,
            "top_food_types": sorted(
                [{"food_type": k, "count": v} for k, v in _count_values(food, "food_type").items()],
                key=lambda x: x["count"], reverse=True
            )[:10],
        }
    finally:
        conn.close()


def _count_values(rows, key):
    counts = {}
    for row in rows:
        value = str(row.get(key) or "Unknown").strip() or "Unknown"
        counts[value] = counts.get(value, 0) + 1
    return counts


def get_admin_user_directory(search="", role="", limit=100):
    """Read-only searchable admin user directory."""
    conn = _connect()
    try:
        rows = _rows(conn, "users", 100000)
        q = str(search or "").strip().lower()
        role_filter = str(role or "").strip().lower()
        out = []
        for row in rows:
            r = str(row.get("role") or "").lower()
            if role_filter and r != role_filter:
                continue
            hay = " ".join([str(row.get("name") or ""), str(row.get("email") or ""), r]).lower()
            if q and q not in hay:
                continue
            out.append({
                "id": row.get("id"),
                "name": row.get("name") or "",
                "email": row.get("email") or "",
                "role": row.get("role") or "",
                "created_at": row.get("created_at") or "",
            })
        out.sort(key=lambda x: str(x.get("created_at") or ""), reverse=True)
        return {"success": True, "total": len(out), "users": out[:max(1, min(int(limit), 500))]}
    finally:
        conn.close()
