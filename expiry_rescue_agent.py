# ============================================================
# FOODLOOP AI - PHASE 3.5 EXPIRY / RESCUE AGENT
# ============================================================

"""
Phase 3.5: Expiry / Rescue Agent

Purpose:
    Detect available food that is approaching its available_until time,
    calculate a rescue urgency level, and notify suitable nearby receivers.

Safety rules:
    - Never invent an expiry time.
    - Never notify for expired food.
    - Never create a food request automatically.
    - Never change food ownership or request status.
    - Provider-side API must verify ownership before calling this agent.
    - Duplicate rescue notifications are prevented per food/receiver.
"""

from datetime import datetime, timezone
import re

import database

try:
    from ai_agent import run_receiver_selection_agent
except Exception:
    run_receiver_selection_agent = None


RESCUE_NOTIFICATION_PREFIX = "ai_expiry_rescue:"

# Rescue windows.
# Food with more than 24 hours remaining is not treated as urgent by this agent.
CRITICAL_MINUTES = 60
URGENT_MINUTES = 180
HIGH_MINUTES = 360
RESCUE_WINDOW_MINUTES = 1440


def parse_datetime(value):
    """Parse common ISO/database datetime formats into UTC."""
    if value is None:
        return None

    text = str(value).strip()
    if not text:
        return None

    candidates = [text]
    if text.endswith("Z"):
        candidates.append(text[:-1] + "+00:00")

    for candidate in candidates:
        try:
            parsed = datetime.fromisoformat(candidate)
            if parsed.tzinfo is None:
                # Existing FoodLoop values are local database timestamps.
                # Treat naive values as UTC rather than inventing a timezone.
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)
        except Exception:
            pass

    for fmt in (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M",
    ):
        try:
            parsed = datetime.strptime(text, fmt)
            return parsed.replace(tzinfo=timezone.utc)
        except Exception:
            pass

    return None


def _minutes_remaining(food):
    parsed = parse_datetime(food.get("available_until"))
    if parsed is None:
        return None, False

    minutes = (parsed - datetime.now(timezone.utc)).total_seconds() / 60.0
    return minutes, True


def classify_urgency(minutes_remaining):
    """Return a deterministic rescue urgency classification."""
    if minutes_remaining is None:
        return {
            "level": "unknown",
            "label": "Unknown",
            "emoji": "❓",
            "urgency_score": 0,
            "should_rescue": False,
        }

    if minutes_remaining <= 0:
        return {
            "level": "expired",
            "label": "Expired",
            "emoji": "⛔",
            "urgency_score": 100,
            "should_rescue": False,
        }

    if minutes_remaining <= CRITICAL_MINUTES:
        return {
            "level": "critical",
            "label": "Critical",
            "emoji": "🚨",
            "urgency_score": 100,
            "should_rescue": True,
        }

    if minutes_remaining <= URGENT_MINUTES:
        return {
            "level": "urgent",
            "label": "Urgent",
            "emoji": "🔴",
            "urgency_score": 80,
            "should_rescue": True,
        }

    if minutes_remaining <= HIGH_MINUTES:
        return {
            "level": "high",
            "label": "High",
            "emoji": "🟠",
            "urgency_score": 60,
            "should_rescue": True,
        }

    if minutes_remaining <= RESCUE_WINDOW_MINUTES:
        return {
            "level": "rescue",
            "label": "Rescue Needed",
            "emoji": "🟡",
            "urgency_score": 40,
            "should_rescue": True,
        }

    return {
        "level": "normal",
        "label": "Normal",
        "emoji": "🟢",
        "urgency_score": 10,
        "should_rescue": False,
    }


def _time_text(minutes_remaining):
    if minutes_remaining is None:
        return "Expiry time not specified."

    if minutes_remaining <= 0:
        return "Food has expired."

    rounded = max(1, int(minutes_remaining))
    if rounded < 60:
        return f"About {rounded} minute(s) remain."

    hours = rounded / 60
    if hours < 24:
        return f"About {hours:.1f} hour(s) remain."

    return f"About {hours / 24:.1f} day(s) remain."


def _quantity_number(value):
    match = re.search(r"\d+(?:\.\d+)?", str(value or ""))
    if not match:
        return None
    try:
        return float(match.group(0))
    except Exception:
        return None


def _receiver_count_for_urgency(level):
    if level == "critical":
        return 5
    if level == "urgent":
        return 4
    if level == "high":
        return 3
    return 2


def _radius_for_urgency(level, requested_radius):
    # During an emergency, allow a wider search so food has a better chance
    # of being rescued. The provider's requested radius remains the minimum.
    try:
        radius = float(requested_radius)
    except Exception:
        radius = 10

    radius = max(0.1, min(radius, 50))

    if level == "critical":
        return min(50, max(radius, 20))
    if level == "urgent":
        return min(50, max(radius, 15))
    if level == "high":
        return min(50, max(radius, 10))
    return radius


def _notification_columns(connection):
    rows = connection.execute("PRAGMA table_info(notifications)").fetchall()
    result = set()
    for row in rows:
        try:
            result.add(row["name"])
        except Exception:
            try:
                result.add(row[1])
            except Exception:
                pass
    return result


def _existing_rescue_notification(receiver_id, food_id):
    notification_type = f"{RESCUE_NOTIFICATION_PREFIX}{int(food_id)}"

    try:
        connection = database.get_connection()
        try:
            columns = _notification_columns(connection)
            if "notification_type" not in columns:
                return False

            identity = "receiver_id" if "receiver_id" in columns else None
            if identity is None and "user_id" in columns:
                identity = "user_id"
            if identity is None:
                return False

            row = connection.execute(
                f"""
                SELECT id FROM notifications
                WHERE {identity} = ?
                  AND notification_type = ?
                LIMIT 1
                """,
                (int(receiver_id), notification_type),
            ).fetchone()
            return row is not None
        finally:
            connection.close()
    except Exception as exc:
        print("EXPIRY DUPLICATE CHECK ERROR:", exc)
        return False


def _create_rescue_notification(receiver_id, receiver_name, message, notification_type):
    """Insert a notification against whichever schema the project currently uses."""
    title = "🚨 FoodLoop AI Rescue Alert"
    connection = database.get_connection()

    try:
        columns = _notification_columns(connection)
        if "message" not in columns:
            return {"success": False, "message": "Notifications table is missing message."}

        insert_columns = []
        values = []

        if "receiver_id" in columns:
            insert_columns.append("receiver_id")
            values.append(int(receiver_id))
        elif "user_id" in columns:
            insert_columns.append("user_id")
            values.append(int(receiver_id))
        else:
            return {"success": False, "message": "Notifications table has no receiver/user ID column."}

        if "receiver_name" in columns:
            insert_columns.append("receiver_name")
            values.append(str(receiver_name or "Receiver").strip())

        if "title" in columns:
            insert_columns.append("title")
            values.append(title)

        insert_columns.append("message")
        values.append(str(message or "")[:500])

        if "notification_type" in columns:
            insert_columns.append("notification_type")
            values.append(notification_type)

        if "is_read" in columns:
            insert_columns.append("is_read")
            values.append(0)

        if "created_at" in columns:
            insert_columns.append("created_at")
            values.append(datetime.now().isoformat(timespec="seconds"))

        placeholders = ", ".join("?" for _ in values)
        sql = f"INSERT INTO notifications ({', '.join(insert_columns)}) VALUES ({placeholders})"
        cursor = connection.execute(sql, tuple(values))
        connection.commit()

        return {"success": True, "notification_id": cursor.lastrowid}

    except Exception as exc:
        connection.rollback()
        return {"success": False, "message": str(exc)}
    finally:
        connection.close()


def _build_rescue_message(food, receiver, rank, urgency, minutes_remaining):
    food_name = str(food.get("food_name") or "Surplus food").strip()
    food_type = str(food.get("food_type") or "Other").strip()
    quantity = str(food.get("quantity") or "Not specified").strip()
    pickup = str(
        food.get("pickup_location")
        or food.get("location")
        or "Pickup location available in FoodLoop"
    ).strip()
    distance = receiver.get("distance_km", "—")
    score = receiver.get("score", 0)
    reason = str(receiver.get("reason") or "Nearby eligible receiver.").strip()

    return (
        f"🚨 FoodLoop AI Rescue Alert: {food_name} needs urgent pickup. "
        f"Type: {food_type}. Quantity: {quantity}. Pickup: {pickup}. "
        f"Distance: {distance} km. AI Match: {score}%. Rank: #{rank}. "
        f"Urgency: {urgency['label']}. {_time_text(minutes_remaining)} "
        f"{reason} Please review and request this food if you can collect it."
    )[:500]


def analyze_food_expiry(food_id, max_distance_km=10, max_notifications=None, notify=True):
    """Analyze one food listing and optionally trigger an AI rescue."""
    try:
        food_id = int(food_id)
    except Exception:
        return {"success": False, "message": "Invalid food ID."}

    if food_id <= 0:
        return {"success": False, "message": "Invalid food ID."}

    food = database.get_food_by_id(food_id)
    if not food:
        return {"success": False, "message": "Food listing not found."}
    food = dict(food)

    status = str(food.get("status") or "available").strip().lower()
    minutes, has_expiry = _minutes_remaining(food)
    urgency = classify_urgency(minutes)

    base = {
        "food_id": food_id,
        "food_name": food.get("food_name"),
        "quantity": food.get("quantity"),
        "food_type": food.get("food_type"),
        "status": status,
        "available_until": food.get("available_until"),
        "has_expiry": has_expiry,
        "minutes_remaining": round(minutes, 2) if minutes is not None else None,
        "time_text": _time_text(minutes),
        "urgency": urgency,
        "notified": [],
        "skipped_duplicates": 0,
    }

    if status != "available":
        base.update({
            "success": True,
            "rescue_triggered": False,
            "message": f"Food is not currently available (status: {status}).",
        })
        return base

    if not has_expiry:
        base.update({
            "success": True,
            "rescue_triggered": False,
            "message": "No usable expiry time is stored, so the Rescue Agent will not invent one.",
        })
        return base

    if urgency["level"] == "expired":
        base.update({
            "success": True,
            "rescue_triggered": False,
            "message": "Food has expired. No rescue notification was sent.",
        })
        return base

    if not urgency["should_rescue"]:
        base.update({
            "success": True,
            "rescue_triggered": False,
            "message": "Food is not yet within the Rescue Agent's 24-hour urgency window.",
        })
        return base

    if not notify:
        base.update({
            "success": True,
            "rescue_triggered": False,
            "message": f"{urgency['emoji']} {urgency['label']} rescue priority detected.",
        })
        return base

    if run_receiver_selection_agent is None:
        base.update({
            "success": False,
            "message": "Receiver Selection Agent is unavailable.",
        })
        return base

    radius = _radius_for_urgency(urgency["level"], max_distance_km)
    if max_notifications is None:
        max_notifications = _receiver_count_for_urgency(urgency["level"])
    try:
        max_notifications = max(1, min(int(max_notifications), 5))
    except Exception:
        max_notifications = _receiver_count_for_urgency(urgency["level"])

    try:
        selection = run_receiver_selection_agent(
            food_id=food_id,
            max_distance_km=radius,
        )
    except Exception as exc:
        base.update({"success": False, "message": f"Receiver selection failed: {exc}"})
        return base

    if not selection.get("success"):
        base.update({
            "success": False,
            "message": selection.get("message") or "Receiver selection failed.",
        })
        return base

    matches = selection.get("matches") or []
    if not matches:
        base.update({
            "success": True,
            "rescue_triggered": True,
            "message": f"{urgency['emoji']} {urgency['label']} food detected, but no eligible receivers were found.",
            "search_radius_km": radius,
        })
        return base

    notified = []
    skipped = 0
    notification_type = f"{RESCUE_NOTIFICATION_PREFIX}{food_id}"

    for rank, receiver in enumerate(matches, start=1):
        if len(notified) >= max_notifications:
            break

        try:
            receiver_id = int(receiver.get("receiver_id"))
        except Exception:
            continue
        if receiver_id <= 0:
            continue

        receiver_name = str(receiver.get("receiver_name") or "Receiver").strip()

        if _existing_rescue_notification(receiver_id, food_id):
            skipped += 1
            continue

        message = _build_rescue_message(
            food,
            receiver,
            rank,
            urgency,
            minutes,
        )

        result = _create_rescue_notification(
            receiver_id=receiver_id,
            receiver_name=receiver_name,
            message=message,
            notification_type=notification_type,
        )

        if result.get("success"):
            notified.append({
                "receiver_id": receiver_id,
                "receiver_name": receiver_name,
                "rank": rank,
                "score": receiver.get("score", 0),
                "distance_km": receiver.get("distance_km"),
                "notification_id": result.get("notification_id"),
            })

    base.update({
        "success": True,
        "rescue_triggered": True,
        "message": (
            f"{urgency['emoji']} {urgency['label']} rescue alert sent to {len(notified)} receiver(s)."
            if notified
            else "Rescue analysis completed; no new rescue notifications were created."
        ),
        "notified": notified,
        "skipped_duplicates": skipped,
        "candidate_count": len(matches),
        "search_radius_km": radius,
        "max_notifications": max_notifications,
    })
    return base


def scan_provider_foods(provider_id, max_distance_km=10, notify=True):
    """Scan all available food owned by one provider."""
    try:
        provider_id = int(provider_id)
    except Exception:
        return {"success": False, "message": "Invalid provider ID.", "foods": []}

    try:
        foods = database.get_all_food() or []
    except Exception as exc:
        return {"success": False, "message": f"Could not read food listings: {exc}", "foods": []}

    results = []
    for item in foods:
        food = dict(item)
        if str(food.get("provider_id")) != str(provider_id):
            continue
        if str(food.get("status") or "available").lower() != "available":
            continue

        results.append(
            analyze_food_expiry(
                food.get("id"),
                max_distance_km=max_distance_km,
                notify=notify,
            )
        )

    urgent = [
        item for item in results
        if item.get("urgency", {}).get("should_rescue")
    ]
    notifications = sum(len(item.get("notified") or []) for item in results)

    return {
        "success": True,
        "message": f"Expiry/Rescue scan completed for {len(results)} available food listing(s).",
        "foods": results,
        "urgent_count": len(urgent),
        "notification_count": notifications,
    }
