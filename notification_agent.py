# ============================================================
# FOODLOOP AI - PHASE 3.4 NOTIFICATION AGENT
# ============================================================

import os
from datetime import datetime, timezone

import database

try:
    from ai_agent import run_receiver_selection_agent
except Exception:
    run_receiver_selection_agent = None


NOTIFICATION_TYPE_PREFIX = "ai_food_match:"


def _parse_datetime(value):
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


def _food_is_expired(food):
    available_until = food.get("available_until")
    parsed = _parse_datetime(available_until)
    if parsed is None:
        # If the listing has no usable expiry value, do not invent one.
        return False, False
    return parsed <= datetime.now(timezone.utc), True


def _urgency_text(food):
    parsed = _parse_datetime(food.get("available_until"))
    if parsed is None:
        return "Pickup availability time was not specified."

    minutes = int((parsed - datetime.now(timezone.utc)).total_seconds() / 60)
    if minutes <= 0:
        return "This food listing has reached its availability time."
    if minutes <= 60:
        return f"Only about {minutes} minute(s) remain for pickup."
    hours = minutes // 60
    if hours <= 6:
        return f"About {hours} hour(s) remain for pickup."
    return f"Pickup is available for about {hours} hour(s)."


def _get_notification_columns(connection):
    """Return the actual notification-table columns used by this project."""
    rows = connection.execute("PRAGMA table_info(notifications)").fetchall()
    columns = set()
    for row in rows:
        try:
            columns.add(row["name"])
        except Exception:
            try:
                columns.add(row[1])
            except Exception:
                pass
    return columns


def _existing_ai_notification(receiver_id, food_id):
    """
    Check duplicates directly against the real database schema.

    Supports both notification schemas used during FoodLoop development:
    receiver_id/receiver_name/notification_type and user_id/title/message.
    """
    notification_type = f"{NOTIFICATION_TYPE_PREFIX}{int(food_id)}"
    try:
        connection = database.get_connection()
        try:
            columns = _get_notification_columns(connection)
            if "notification_type" not in columns:
                return False

            identity_column = None
            if "receiver_id" in columns:
                identity_column = "receiver_id"
            elif "user_id" in columns:
                identity_column = "user_id"

            if identity_column is None:
                return False

            row = connection.execute(
                f"""
                SELECT id
                FROM notifications
                WHERE {identity_column} = ?
                  AND notification_type = ?
                LIMIT 1
                """,
                (int(receiver_id), notification_type)
            ).fetchone()
            return row is not None
        finally:
            connection.close()
    except Exception as exc:
        print("NOTIFICATION DUPLICATE CHECK ERROR:", exc)
        return False


def _create_ai_notification(
    receiver_id,
    receiver_name,
    message,
    notification_type,
):
    """
    Create an AI notification using the actual notifications-table schema.

    Supplies the required title column when the current database contains it.
    """
    title = "🤖 FoodLoop AI Food Match"
    connection = database.get_connection()

    try:
        columns = _get_notification_columns(connection)

        if "message" not in columns:
            return {
                "success": False,
                "message": "Notifications table is missing the message column."
            }

        insert_columns = []
        values = []

        if "receiver_id" in columns:
            insert_columns.append("receiver_id")
            values.append(int(receiver_id))
        elif "user_id" in columns:
            insert_columns.append("user_id")
            values.append(int(receiver_id))
        else:
            return {
                "success": False,
                "message": "Notifications table has no receiver/user ID column."
            }

        if "receiver_name" in columns:
            insert_columns.append("receiver_name")
            values.append(str(receiver_name or "").strip())

        if "title" in columns:
            insert_columns.append("title")
            values.append(title)

        insert_columns.append("message")
        values.append(str(message or "").strip())

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
        column_sql = ", ".join(insert_columns)

        cursor = connection.execute(
            f"""
            INSERT INTO notifications ({column_sql})
            VALUES ({placeholders})
            """,
            tuple(values)
        )
        connection.commit()

        return {
            "success": True,
            "notification_id": cursor.lastrowid
        }

    except Exception as exc:
        connection.rollback()
        return {
            "success": False,
            "message": str(exc)
        }
    finally:
        connection.close()


def _build_message(food, receiver, rank):
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
    reason = str(receiver.get("reason") or "Good nearby receiver match.").strip()

    return (
        f"🤖 FoodLoop AI matched you with surplus food: {food_name}. "
        f"Type: {food_type}. Quantity: {quantity}. "
        f"Pickup: {pickup}. Distance: {distance} km. "
        f"AI Match: {score}%. Rank: #{rank}. "
        f"{reason} {_urgency_text(food)} "
        f"Open FoodLoop to review and request this food."
    )[:500]


def run_notification_agent(
    food_id: int,
    max_distance_km: float = 10,
    max_notifications: int = 3,
):
    """
    Phase 3.4 Notification Agent.

    Flow:
      1. Read the real food listing.
      2. Validate that it is still available and not expired.
      3. Run the Phase 3.3 Receiver Selection Agent.
      4. Select the highest-ranked eligible receivers.
      5. Prevent duplicate AI notifications for the same food/receiver.
      6. Create receiver-specific notifications.

    Important: this agent DOES NOT create, accept, reject, or collect
    food requests. The receiver must explicitly request the food.
    """
    try:
        food_id = int(food_id)
    except Exception:
        return {"success": False, "message": "Invalid food ID.", "notified": []}

    if food_id <= 0:
        return {"success": False, "message": "Invalid food ID.", "notified": []}

    try:
        max_distance_km = float(max_distance_km)
    except Exception:
        max_distance_km = 10
    max_distance_km = max(0.1, min(max_distance_km, 50))

    try:
        max_notifications = int(max_notifications)
    except Exception:
        max_notifications = 3
    max_notifications = max(1, min(max_notifications, 5))

    food = database.get_food_by_id(food_id)
    if not food:
        return {"success": False, "message": "Food listing not found.", "notified": []}
    food = dict(food)

    status = str(food.get("status") or "available").strip().lower()
    if status != "available":
        return {
            "success": False,
            "message": "Only available food can trigger AI notifications.",
            "notified": [],
        }

    expired, has_expiry = _food_is_expired(food)
    if expired:
        return {
            "success": False,
            "message": "This food listing has expired and cannot trigger a notification.",
            "notified": [],
            "expired": True,
        }

    if run_receiver_selection_agent is None:
        return {
            "success": False,
            "message": "Receiver Selection Agent is unavailable.",
            "notified": [],
        }

    selection = run_receiver_selection_agent(
        food_id=food_id,
        max_distance_km=max_distance_km,
    )

    if not selection.get("success"):
        return {
            "success": False,
            "message": selection.get("message") or "Receiver selection failed.",
            "notified": [],
        }

    matches = selection.get("matches") or []
    if not matches:
        return {
            "success": True,
            "message": "No eligible receivers were found within the selected radius.",
            "notified": [],
            "candidate_count": 0,
            "skipped_duplicates": 0,
            "expired": False,
        }

    notified = []
    skipped_duplicates = 0

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

        if _existing_ai_notification(receiver_id, food_id):
            skipped_duplicates += 1
            continue

        message = _build_message(food, receiver, rank)
        notification_type = f"{NOTIFICATION_TYPE_PREFIX}{food_id}"

        result = _create_ai_notification(
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
                "score_breakdown": receiver.get("score_breakdown", {}),
                "notification_id": result.get("notification_id"),
            })

    if notified:
        message = f"AI notification sent to {len(notified)} receiver(s)."
    elif skipped_duplicates:
        message = "These receivers have already been notified for this food listing."
    else:
        message = "No new receiver notifications were created."

    return {
        "success": True,
        "message": message,
        "notified": notified,
        "candidate_count": len(matches),
        "skipped_duplicates": skipped_duplicates,
        "max_notifications": max_notifications,
        "max_distance_km": max_distance_km,
        "expired": False,
        "has_expiry": has_expiry,
        "food": {
            "food_id": food_id,
            "food_name": food.get("food_name"),
            "quantity": food.get("quantity"),
            "food_type": food.get("food_type"),
            "available_until": food.get("available_until"),
        },
    }
