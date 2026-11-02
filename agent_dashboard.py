# ============================================================
# FOODLOOP AI - PHASE 3.7 AGENT DASHBOARD
# ============================================================
"""
Phase 3.7: Live Agent Dashboard

This module builds provider-safe dashboard metrics from the existing
FoodLoop database. It does not change the database schema and does not
create food requests or modify food/request status.
"""

from datetime import datetime, timezone
import sqlite3

import database


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
            return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
        except Exception:
            pass

    return None


def _urgency(food):
    parsed = _parse_datetime(food.get("available_until"))
    if parsed is None:
        return {
            "level": "unknown",
            "label": "Expiry time unavailable",
            "minutes_remaining": None,
            "urgency_score": 0,
        }

    minutes = (parsed - datetime.now(timezone.utc)).total_seconds() / 60.0

    if minutes <= 0:
        return {
            "level": "expired",
            "label": "Expired",
            "minutes_remaining": round(minutes, 1),
            "urgency_score": 100,
        }
    if minutes <= 60:
        level, label, score = "critical", "Critical", 100
    elif minutes <= 180:
        level, label, score = "urgent", "Urgent", 80
    elif minutes <= 360:
        level, label, score = "high", "High", 60
    elif minutes <= 1440:
        level, label, score = "rescue", "Rescue", 40
    else:
        level, label, score = "normal", "Normal", 10

    return {
        "level": level,
        "label": label,
        "minutes_remaining": round(minutes, 1),
        "urgency_score": score,
    }


def _safe_int(value, default=0):
    try:
        return int(value)
    except Exception:
        return default


def _table_exists(connection, table_name):
    row = connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,),
    ).fetchone()
    return bool(row)


def get_agent_dashboard(provider_id: int):
    """Return live Phase 3.7 metrics for the authenticated provider."""
    try:
        provider_id = int(provider_id)
    except Exception:
        return {"success": False, "message": "Invalid provider ID."}

    if provider_id <= 0:
        return {"success": False, "message": "Invalid provider ID."}

    connection = database.get_connection()

    try:
        # --------------------------------------------------------
        # Provider-owned food
        # --------------------------------------------------------
        food_rows = connection.execute(
            """
            SELECT *
            FROM food
            WHERE provider_id = ?
            ORDER BY created_at DESC, id DESC
            """,
            (provider_id,),
        ).fetchall()
        foods = [dict(row) for row in food_rows]

        available = [
            food for food in foods
            if str(food.get("status") or "available").strip().lower() == "available"
        ]

        collected_food = [
            food for food in foods
            if str(food.get("status") or "").strip().lower() == "collected"
        ]

        urgency_items = []
        urgent_count = 0
        critical_count = 0
        expired_count = 0

        for food in available:
            urgency = _urgency(food)
            item = {
                "food_id": food.get("id"),
                "food_name": food.get("food_name"),
                "quantity": food.get("quantity"),
                "food_type": food.get("food_type"),
                "pickup_location": food.get("pickup_location") or food.get("location") or "",
                "available_until": food.get("available_until"),
                "urgency": urgency,
            }
            urgency_items.append(item)

            if urgency["level"] in {"rescue", "high", "urgent", "critical"}:
                urgent_count += 1
            if urgency["level"] == "critical":
                critical_count += 1
            if urgency["level"] == "expired":
                expired_count += 1

        urgency_order = {
            "critical": 0,
            "urgent": 1,
            "high": 2,
            "rescue": 3,
            "normal": 4,
            "unknown": 5,
            "expired": 6,
        }
        urgency_items.sort(
            key=lambda item: (
                urgency_order.get(item["urgency"]["level"], 9),
                item["urgency"]["minutes_remaining"]
                if item["urgency"]["minutes_remaining"] is not None
                else 10**12,
            )
        )

        # --------------------------------------------------------
        # Provider request metrics
        # --------------------------------------------------------
        request_rows = connection.execute(
            """
            SELECT fr.*
            FROM food_requests fr
            INNER JOIN food f ON f.id = fr.food_id
            WHERE f.provider_id = ?
            ORDER BY fr.created_at DESC, fr.id DESC
            """,
            (provider_id,),
        ).fetchall()
        requests = [dict(row) for row in request_rows]

        request_counts = {
            "pending": 0,
            "accepted": 0,
            "rejected": 0,
            "collected": 0,
        }
        for request in requests:
            status = str(request.get("status") or "pending").strip().lower()
            request_counts[status] = request_counts.get(status, 0) + 1

        # --------------------------------------------------------
        # Receiver network size
        # --------------------------------------------------------
        receiver_count = 0
        active_receiver_count = 0
        if _table_exists(connection, "receiver_locations"):
            receiver_count = _safe_int(
                connection.execute(
                    "SELECT COUNT(DISTINCT receiver_id) AS c FROM receiver_locations WHERE receiver_id IS NOT NULL"
                ).fetchone()["c"]
            )
            active_receiver_count = _safe_int(
                connection.execute(
                    "SELECT COUNT(DISTINCT receiver_id) AS c FROM receiver_locations WHERE latitude IS NOT NULL AND longitude IS NOT NULL"
                ).fetchone()["c"]
            )

        # --------------------------------------------------------
        # Platform AI notification activity.
        # This is intentionally labelled platform-level because the
        # existing notification schema does not store food/provider IDs.
        # --------------------------------------------------------
        ai_notification_count = 0
        unread_ai_notification_count = 0
        recent_notifications = []

        if _table_exists(connection, "notifications"):
            columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(notifications)").fetchall()
            }
            type_column = "notification_type" if "notification_type" in columns else None
            message_column = "message" if "message" in columns else None
            read_column = "is_read" if "is_read" in columns else None
            title_column = "title" if "title" in columns else None

            if type_column:
                ai_notification_count = _safe_int(
                    connection.execute(
                        "SELECT COUNT(*) AS c FROM notifications WHERE notification_type IN ('ai_match','ai_rescue','ai_notification','food_match','rescue')"
                    ).fetchone()["c"]
                )
                if read_column:
                    unread_ai_notification_count = _safe_int(
                        connection.execute(
                            "SELECT COUNT(*) AS c FROM notifications WHERE notification_type IN ('ai_match','ai_rescue','ai_notification','food_match','rescue') AND COALESCE(is_read,0)=0"
                        ).fetchone()["c"]
                    )

            select_parts = ["id"]
            if title_column:
                select_parts.append("title")
            if message_column:
                select_parts.append("message")
            if type_column:
                select_parts.append("notification_type")
            if "created_at" in columns:
                select_parts.append("created_at")
            if read_column:
                select_parts.append("is_read")

            rows = connection.execute(
                f"SELECT {', '.join(select_parts)} FROM notifications ORDER BY id DESC LIMIT 8"
            ).fetchall()
            recent_notifications = [dict(row) for row in rows]

        # --------------------------------------------------------
        # Agent availability
        # --------------------------------------------------------
        agent_status = {
            "food_matching": True,
            "receiver_selection": True,
            "notification": True,
            "expiry_rescue": True,
            "multi_agent_workflow": True,
        }

        total_requests = len(requests)
        collected_requests = request_counts.get("collected", 0)
        success_rate = round((collected_requests / total_requests) * 100, 1) if total_requests else 0

        return {
            "success": True,
            "provider_id": provider_id,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "agents": agent_status,
            "metrics": {
                "total_food": len(foods),
                "available_food": len(available),
                "collected_food": len(collected_food),
                "rescue_food": urgent_count,
                "critical_food": critical_count,
                "expired_food": expired_count,
                "total_requests": total_requests,
                "pending_requests": request_counts.get("pending", 0),
                "accepted_requests": request_counts.get("accepted", 0),
                "rejected_requests": request_counts.get("rejected", 0),
                "collected_requests": collected_requests,
                "collection_success_rate": success_rate,
                "receiver_network": receiver_count,
                "active_receiver_locations": active_receiver_count,
                "platform_ai_notifications": ai_notification_count,
                "platform_unread_ai_notifications": unread_ai_notification_count,
            },
            "urgency_queue": urgency_items[:10],
            "recent_requests": requests[:8],
            "recent_notifications": recent_notifications,
        }

    except Exception as exc:
        return {
            "success": False,
            "message": f"Agent dashboard error: {exc}",
        }
    finally:
        connection.close()
